"""LangGraph node implementations.

Every node is a plain function of (state, db, user, chat_model) -> partial
state update dict. Nodes never make authorization decisions themselves for
write actions — `tool_node` always goes through
`app.tools.executor.execute_tool`, which re-checks role/row-level
permissions regardless of what classification or the LLM decided.

Note on `trace`/`step_count`: sql_node, rag_node, and tool_node can run
concurrently in the same LangGraph superstep (fan-out from `classify`), so
each node must return only its own *delta* — a single-entry list for
`trace`, the integer `1` for `step_count` — never the full accumulated
state. Both fields use additive reducers (see `app/agent/state.py`) that
merge concurrent branch outputs; returning a full snapshot from more than
one branch in the same step would double-count or raise
`InvalidUpdateError`.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.agent.classify import classify_intent
from app.agent.entities import resolve_customer_id
from app.agent.prompts import build_synthesis_prompt
from app.agent.sql_builder import build_sql_query
from app.agent.state import AgentState
from app.auth.schemas import CurrentUser
from app.db.models import ApprovalStatus, PendingApproval, Role
from app.db.sql_guard import SQLGuardError, execute_readonly_query
from app.rag.retriever import citations_from_chunks, format_context_block, retrieve
from app.tools.executor import execute_tool

logger = logging.getLogger("app.agent.nodes")


def _trace_entry(node: str, **detail) -> list[dict]:
    return [{"node": node, **detail}]


def classify_node(state: AgentState, *, db: Session, user: CurrentUser, chat_model) -> dict:
    start = time.perf_counter()
    intent = classify_intent(state["user_query"], state["role"], chat_model=chat_model)

    customer_id, resolve_error = resolve_customer_id(db, user, intent.get("customer_reference"))

    updates: dict = {
        "needs_rag": bool(intent.get("needs_rag")),
        "needs_sql": bool(intent.get("needs_sql")) and user.role != Role.CUSTOMER,
        "needs_tool": bool(intent.get("needs_tool")),
        "tool_name": intent.get("tool_name"),
        "customer_reference": intent.get("customer_reference"),
        "resolved_customer_id": customer_id,
        "rag_search_query": intent.get("rag_search_query") or state["user_query"],
        "ticket_subject": intent.get("ticket_subject"),
        "ticket_description": intent.get("ticket_description"),
        "ticket_severity": intent.get("ticket_severity"),
        "ticket_category": intent.get("ticket_category"),
        "step_count": 1,
    }

    if resolve_error and (updates["needs_sql"] or updates["needs_tool"]):
        updates["sql_error"] = resolve_error
        updates["tool_error"] = resolve_error
        updates["needs_sql"] = False
        updates["needs_tool"] = False

    updates["trace"] = _trace_entry(
        "classify",
        needs_rag=updates["needs_rag"], needs_sql=updates["needs_sql"], needs_tool=updates["needs_tool"],
        tool_name=updates["tool_name"], duration_ms=round((time.perf_counter() - start) * 1000, 1),
    )
    return updates


def route_after_classify(state: AgentState) -> list[str]:
    branches = []
    if state.get("needs_sql"):
        branches.append("sql_node")
    if state.get("needs_rag"):
        branches.append("rag_node")
    if state.get("needs_tool"):
        branches.append("tool_node")
    return branches or ["synthesize"]


def rag_node(state: AgentState, *, db: Session, user: CurrentUser, chat_model) -> dict:
    start = time.perf_counter()
    query = state.get("rag_search_query") or state["user_query"]
    chunks = retrieve(query)
    return {
        "rag_chunks": [c.__dict__ for c in chunks],
        "trace": _trace_entry("rag", query=query, hits=len(chunks), duration_ms=round((time.perf_counter() - start) * 1000, 1)),
        "step_count": 1,
    }


def sql_node(state: AgentState, *, db: Session, user: CurrentUser, chat_model) -> dict:
    start = time.perf_counter()
    customer_id = state.get("resolved_customer_id")
    sql_text = build_sql_query(state["user_query"], customer_id, chat_model=chat_model)
    try:
        rows = execute_readonly_query(sql_text)
        updates = {
            "sql_query_text": sql_text,
            "sql_results": rows,
            "sql_error": None,
        }
    except SQLGuardError as exc:
        logger.warning("sql_guard_rejected query=%r reason=%s", sql_text, exc)
        updates = {
            "sql_query_text": sql_text,
            "sql_results": [],
            "sql_error": f"Query was rejected by the SQL safety guard: {exc}",
        }
    updates["trace"] = _trace_entry(
        "sql", query=sql_text, row_count=len(updates.get("sql_results") or []),
        duration_ms=round((time.perf_counter() - start) * 1000, 1),
    )
    updates["step_count"] = 1
    return updates


def tool_node(state: AgentState, *, db: Session, user: CurrentUser, chat_model) -> dict:
    start = time.perf_counter()
    tool_name = state.get("tool_name")
    customer_id = state.get("resolved_customer_id")

    if not tool_name:
        return {"tool_error": "No tool could be determined for this request.", "step_count": 1}

    if tool_name == "customer_lookup":
        args = {"customer_id": customer_id} if customer_id else {"name": state.get("customer_reference")}
    elif tool_name in ("invoice_lookup", "ticket_lookup"):
        if customer_id is None:
            return {
                "tool_error": "Could not determine which customer account this request applies to.",
                "trace": _trace_entry("tool", tool=tool_name, result="no_customer"),
                "step_count": 1,
            }
        args = {"customer_id": customer_id}
    elif tool_name == "create_support_ticket":
        if customer_id is None:
            return {
                "tool_error": "Could not determine which customer account to file the ticket under.",
                "trace": _trace_entry("tool", tool=tool_name, result="no_customer"),
                "step_count": 1,
            }
        args = {
            "customer_id": customer_id,
            "subject": state.get("ticket_subject") or state["user_query"][:120],
            "description": state.get("ticket_description") or state["user_query"],
            "category": state.get("ticket_category") or "other",
            "severity": state.get("ticket_severity") or "sev3",
            "created_by": user.username,
        }
    else:
        return {"tool_error": f"Unknown tool '{tool_name}'.", "step_count": 1}

    result = execute_tool(db, user, tool_name, args)

    updates: dict = {
        "trace": _trace_entry(
            "tool", tool=tool_name, ok=result.ok, requires_approval=result.requires_approval,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        ),
        "step_count": 1,
    }

    if not result.ok:
        updates["tool_error"] = result.error
    elif result.requires_approval:
        approval_id = str(uuid.uuid4())
        summary = _describe_pending_action(tool_name, result.args)
        approval = PendingApproval(
            id=approval_id,
            conversation_id=state["conversation_id"],
            requested_by=user.username,
            tool_name=tool_name,
            tool_args_json=json.dumps(result.args, default=str),
            summary=summary,
            status=ApprovalStatus.PENDING,
            created_at=datetime.utcnow(),
        )
        db.add(approval)
        db.flush()
        updates["pending_approval_id"] = approval_id
        updates["tool_result"] = {
            "pending_approval": True,
            "approval_id": approval_id,
            "tool_name": tool_name,
            "customer_id": result.args.get("customer_id"),
            "summary": summary,
        }
    else:
        updates["tool_result"] = result.data

    return updates


def _describe_pending_action(tool_name: str, args: dict) -> str:
    if tool_name == "create_support_ticket":
        return (
            f"Create a {args.get('severity', 'sev3').upper()} support ticket for customer "
            f"#{args.get('customer_id')} — subject: \"{args.get('subject')}\""
        )
    return f"Execute {tool_name} with args {args}"


def synthesize_node(state: AgentState, *, db: Session, user: CurrentUser, chat_model) -> dict:
    start = time.perf_counter()
    chunk_dicts = state.get("rag_chunks") or []

    from app.rag.retriever import RetrievedChunk

    chunks = [RetrievedChunk(**c) for c in chunk_dicts]
    context_block = format_context_block(chunks) if state.get("needs_rag") else "<retrieved_documents>(not needed for this question)</retrieved_documents>"

    messages = build_synthesis_prompt(
        history=state.get("history", []),
        user_query=state["user_query"],
        context_block=context_block,
        sql_results=state.get("sql_results") if state.get("needs_sql") else None,
        sql_error=state.get("sql_error"),
        tool_result=state.get("tool_result"),
        tool_error=state.get("tool_error"),
    )

    response = chat_model.invoke(messages)
    answer = response.content if isinstance(response.content, str) else str(response.content)

    citations = citations_from_chunks(chunks)

    return {
        "final_answer": answer,
        "citations": citations,
        "trace": _trace_entry("synthesize", duration_ms=round((time.perf_counter() - start) * 1000, 1)),
        "step_count": 1,
    }
