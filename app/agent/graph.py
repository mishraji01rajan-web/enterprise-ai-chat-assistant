"""LangGraph agentic workflow wiring.

Graph shape:

    START -> classify -> (fan-out to any of: sql_node, rag_node, tool_node) -> synthesize -> END

`classify` decides, per-request, which capabilities are needed (possibly
more than one, e.g. the canonical "outstanding invoices + payment policy"
multi-step question needs both `sql_node` and `rag_node`). All triggered
branches run, then `synthesize` fans back in once every triggered branch has
completed, and produces the final answer.

Loop prevention: the graph itself is acyclic, but `recursion_limit` (backed
by `settings.agent_max_steps`) is still enforced on every invocation as a
hard structural guard — if a future change introduces a cycle (e.g. a
re-planning loop), it cannot run away unbounded.

Two entry points are exposed:
- `run_agent`: full non-streaming run (used by tests, eval, and any
  non-streaming caller) — includes the `synthesize` node.
- `gather_context`: runs everything *except* synthesis and returns the raw
  state, so the API layer can stream the final LLM answer token-by-token
  itself while reusing the exact same classification/retrieval/tool logic.
"""
from __future__ import annotations

import functools
import logging
import time
from dataclasses import dataclass

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent.llm import get_chat_model
from app.agent.nodes import (
    classify_node,
    rag_node,
    route_after_classify,
    sql_node,
    synthesize_node,
    tool_node,
)
from app.agent.state import AgentState
from app.auth.schemas import CurrentUser
from app.config import settings

logger = logging.getLogger("app.agent.graph")


class AgentAbortedError(Exception):
    """Raised when the graph is stopped by the recursion/loop guard."""


def _initial_state(user: CurrentUser, conversation_id: str, history: list[dict], user_query: str) -> AgentState:
    return {
        "user_id": user.id,
        "username": user.username,
        "role": user.role.value,
        "customer_id": user.customer_id,
        "conversation_id": conversation_id,
        "history": history,
        "user_query": user_query,
        "trace": [],
        "step_count": 0,
        "rag_chunks": [],
        "sql_results": [],
        "citations": [],
    }


def build_graph(db: Session, user: CurrentUser, chat_model, *, with_synthesis: bool = True) -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("classify", functools.partial(classify_node, db=db, user=user, chat_model=chat_model))
    graph.add_node("rag_node", functools.partial(rag_node, db=db, user=user, chat_model=chat_model))
    graph.add_node("sql_node", functools.partial(sql_node, db=db, user=user, chat_model=chat_model))
    graph.add_node("tool_node", functools.partial(tool_node, db=db, user=user, chat_model=chat_model))

    fan_in_target = "synthesize" if with_synthesis else END
    path_map = {"sql_node": "sql_node", "rag_node": "rag_node", "tool_node": "tool_node", "synthesize": fan_in_target}

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", route_after_classify, path_map)
    graph.add_edge("sql_node", fan_in_target)
    graph.add_edge("rag_node", fan_in_target)
    graph.add_edge("tool_node", fan_in_target)

    if with_synthesis:
        graph.add_node("synthesize", functools.partial(synthesize_node, db=db, user=user, chat_model=chat_model))
        graph.add_edge("synthesize", END)

    return graph


def gather_context(
    db: Session,
    user: CurrentUser,
    chat_model,
    conversation_id: str,
    history: list[dict],
    user_query: str,
) -> AgentState:
    """Run classify + whichever of sql/rag/tool are needed; stop before synthesis."""
    graph = build_graph(db, user, chat_model, with_synthesis=False).compile()
    initial_state = _initial_state(user, conversation_id, history, user_query)
    try:
        return graph.invoke(initial_state, config={"recursion_limit": settings.agent_max_steps})
    except GraphRecursionError as exc:
        logger.error("agent_recursion_limit_exceeded conversation=%s", conversation_id)
        raise AgentAbortedError("recursion_limit_exceeded") from exc


@dataclass
class AgentRunResult:
    final_answer: str
    citations: list[dict]
    trace: list[dict]
    pending_approval_id: str | None
    sql_query_text: str | None
    sql_results: list[dict]
    tool_result: dict | None
    needs_rag: bool
    needs_sql: bool
    needs_tool: bool
    duration_ms: float
    aborted: bool = False
    abort_reason: str | None = None


def run_agent(
    db: Session,
    user: CurrentUser,
    conversation_id: str,
    history: list[dict],
    user_query: str,
) -> AgentRunResult:
    start = time.perf_counter()
    chat_model = get_chat_model()
    graph = build_graph(db, user, chat_model, with_synthesis=True).compile()
    initial_state = _initial_state(user, conversation_id, history, user_query)

    try:
        final_state = graph.invoke(initial_state, config={"recursion_limit": settings.agent_max_steps})
    except GraphRecursionError:
        logger.error("agent_recursion_limit_exceeded conversation=%s", conversation_id)
        return AgentRunResult(
            final_answer=(
                "I wasn't able to complete this request within the allowed number of steps, "
                "so I've stopped rather than continue indefinitely. Please try rephrasing or "
                "breaking the question into smaller parts."
            ),
            citations=[],
            trace=[],
            pending_approval_id=None,
            sql_query_text=None,
            sql_results=[],
            tool_result=None,
            needs_rag=False,
            needs_sql=False,
            needs_tool=False,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            aborted=True,
            abort_reason="recursion_limit_exceeded",
        )

    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    return AgentRunResult(
        final_answer=final_state.get("final_answer") or "I wasn't able to generate a response.",
        citations=final_state.get("citations", []),
        trace=final_state.get("trace", []),
        pending_approval_id=final_state.get("pending_approval_id"),
        sql_query_text=final_state.get("sql_query_text"),
        sql_results=final_state.get("sql_results", []),
        tool_result=final_state.get("tool_result"),
        needs_rag=bool(final_state.get("needs_rag")),
        needs_sql=bool(final_state.get("needs_sql")),
        needs_tool=bool(final_state.get("needs_tool")),
        duration_ms=duration_ms,
    )
