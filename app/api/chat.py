"""The main chat endpoint: streams the assistant's response over SSE.

Flow per request:
1. Load (or create) the conversation and its prior messages (multi-turn memory).
2. Persist the incoming user message immediately.
3. Run the agent's gather phase (classification, RAG retrieval, SQL query,
   tool call) — this is the exact same logic `run_agent` uses for
   non-streaming callers, just stopped one node early.
4. Stream the final synthesis LLM call token-by-token to the client.
5. Persist the assembled assistant message + citations, and log a
   structured observability event covering the whole turn.

Every failure mode (LLM error, tool error, timeout, recursion-limit abort)
degrades to a clear, safe message rather than a raised exception reaching
the client mid-stream.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool
from starlette.responses import StreamingResponse

from app.agent.graph import AgentAbortedError, gather_context
from app.agent.llm import get_chat_model
from app.agent.prompts import build_synthesis_prompt
from app.agent.state import AgentState
from app.api.schemas import ChatRequest
from app.auth.dependencies import get_current_user
from app.auth.schemas import CurrentUser
from app.config import settings
from app.db.models import Conversation, Message, Role
from app.db.session import SessionLocal, session_dependency
from app.observability.logging_config import get_logger
from app.rag.retriever import RetrievedChunk, citations_from_chunks

router = APIRouter(prefix="/chat", tags=["chat"])
logger = get_logger("app.api.chat")

GATHER_TIMEOUT_SECONDS = settings.agent_step_timeout_seconds * 3


def _sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _load_history(db: Session, conversation_id: str) -> list[dict]:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [{"role": r.role, "content": r.content} for r in rows]


def _gather_in_own_session(current_user, chat_model, conversation_id, history, message):
    """Run gather_context on a dedicated Session, never the request's `db`.

    `asyncio.wait_for(asyncio.to_thread(...), timeout=...)` cannot actually
    cancel the worker thread — on timeout it just stops awaiting while the
    thread keeps running. If that thread were still using the request-scoped
    `db` Session (which the main coroutine immediately reuses to write the
    fallback message and commit), the two threads would mutate the same,
    non-thread-safe SQLAlchemy Session concurrently — SQLite's own
    check-same-thread guard is deliberately disabled for this engine
    (`app/db/session.py`), so nothing else stops that. Giving the worker its
    own session eliminates the shared-mutable-state hazard entirely: an
    abandoned thread can only ever commit/rollback/close its own session.

    Residual behavior (accepted, documented): if a gather call is abandoned
    this way and later completes anyway (e.g. a slow tool call finishing
    after the timeout), any write it performed (such as a proposed
    PendingApproval) still lands in the database on its own schedule, after
    the client has already received a timeout message. That row is still
    correctly authorized/attributed — it's just not something the client
    session it can act on.
    """
    session = SessionLocal()
    try:
        result = gather_context(session, current_user, chat_model, conversation_id, history, message)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _get_or_create_conversation(db: Session, user: CurrentUser, conversation_id: str | None) -> Conversation:
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        if conversation.user_id != user.id and user.role != Role.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your conversation")
        return conversation

    now = datetime.utcnow()
    conversation = Conversation(id=str(uuid.uuid4()), user_id=user.id, title="New conversation", created_at=now, updated_at=now)
    db.add(conversation)
    db.flush()
    return conversation


@router.post("")
async def chat(
    request: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(session_dependency),
) -> StreamingResponse:
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message must not be empty")

    turn_start = time.perf_counter()
    conversation = _get_or_create_conversation(db, current_user, request.conversation_id)
    conversation_id = conversation.id

    history = _load_history(db, conversation_id)

    now = datetime.utcnow()
    db.add(Message(conversation_id=conversation_id, role="user", content=request.message, created_at=now))
    if conversation.title == "New conversation":
        conversation.title = request.message.strip()[:80]
    conversation.updated_at = now
    db.commit()

    chat_model = get_chat_model()

    async def event_stream():
        aborted = False
        abort_reason = None
        gathered: AgentState = {}
        try:
            gathered = await asyncio.wait_for(
                asyncio.to_thread(
                    _gather_in_own_session, current_user, chat_model, conversation_id, history, request.message
                ),
                timeout=GATHER_TIMEOUT_SECONDS,
            )
        except AgentAbortedError:
            aborted = True
            abort_reason = "recursion_limit_exceeded"
        except asyncio.TimeoutError:
            aborted = True
            abort_reason = "gather_timeout"
        except Exception:  # noqa: BLE001
            logger.exception("gather_context_failed", conversation_id=conversation_id)
            aborted = True
            abort_reason = "internal_error"

        if aborted:
            fallback = (
                "I wasn't able to gather the information needed to answer that within the "
                "allowed time/steps. Please try again or rephrase your question."
            )
            yield _sse("token", {"text": fallback})
            db.add(Message(conversation_id=conversation_id, role="assistant", content=fallback, created_at=datetime.utcnow()))
            conversation.updated_at = datetime.utcnow()
            db.commit()
            logger.warning("agent_turn_aborted", conversation_id=conversation_id, reason=abort_reason)
            yield _sse("done", {"citations": [], "pending_approval_id": None, "aborted": True, "abort_reason": abort_reason})
            return

        chunk_dicts = gathered.get("rag_chunks") or []
        chunks = [RetrievedChunk(**c) for c in chunk_dicts]
        from app.rag.retriever import format_context_block

        context_block = (
            format_context_block(chunks)
            if gathered.get("needs_rag")
            else "<retrieved_documents>(not needed for this question)</retrieved_documents>"
        )
        messages = build_synthesis_prompt(
            history=history,
            user_query=request.message,
            context_block=context_block,
            sql_results=gathered.get("sql_results") if gathered.get("needs_sql") else None,
            sql_error=gathered.get("sql_error"),
            tool_result=gathered.get("tool_result"),
            tool_error=gathered.get("tool_error"),
        )

        full_answer_parts: list[str] = []
        try:
            sync_stream = chat_model.stream(messages)
            async for piece in iterate_in_threadpool(iter(sync_stream)):
                text = piece.content if isinstance(piece.content, str) else str(piece.content)
                if not text:
                    continue
                full_answer_parts.append(text)
                yield _sse("token", {"text": text})
        except Exception:  # noqa: BLE001
            logger.exception("llm_streaming_failed", conversation_id=conversation_id)
            fallback = "I ran into an internal error while generating a response. Please try again."
            full_answer_parts = [fallback]
            yield _sse("token", {"text": fallback})

        final_answer = "".join(full_answer_parts) or "I wasn't able to generate a response."
        citations = citations_from_chunks(chunks)

        db.add(Message(conversation_id=conversation_id, role="assistant", content=final_answer, created_at=datetime.utcnow()))
        conversation.updated_at = datetime.utcnow()
        db.commit()

        duration_ms = round((time.perf_counter() - turn_start) * 1000, 1)
        logger.info(
            "agent_turn_completed",
            conversation_id=conversation_id,
            user=current_user.username,
            role=current_user.role.value,
            needs_rag=bool(gathered.get("needs_rag")),
            needs_sql=bool(gathered.get("needs_sql")),
            needs_tool=bool(gathered.get("needs_tool")),
            pending_approval_id=gathered.get("pending_approval_id"),
            duration_ms=duration_ms,
            trace=gathered.get("trace", []),
        )

        yield _sse(
            "done",
            {
                "conversation_id": conversation_id,
                "citations": citations,
                "pending_approval_id": gathered.get("pending_approval_id"),
                "duration_ms": duration_ms,
                "aborted": False,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
