"""LangGraph agent state definition."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # --- caller / conversation context (read-only inputs) ---
    user_id: int
    username: str
    role: str
    customer_id: Optional[int]
    conversation_id: str
    history: list[dict]  # [{"role": "user"|"assistant", "content": str}, ...]
    user_query: str

    # --- classification / routing ---
    needs_rag: bool
    needs_sql: bool
    needs_tool: bool
    tool_name: Optional[str]
    customer_reference: Optional[str]
    resolved_customer_id: Optional[int]
    rag_search_query: Optional[str]
    ticket_subject: Optional[str]
    ticket_description: Optional[str]
    ticket_severity: Optional[str]
    ticket_category: Optional[str]

    # --- gathered evidence ---
    rag_chunks: list[dict]
    sql_query_text: Optional[str]
    sql_results: list[dict]
    sql_error: Optional[str]
    tool_result: Optional[dict]
    tool_error: Optional[str]
    pending_approval_id: Optional[str]

    # --- output ---
    final_answer: Optional[str]
    citations: list[dict]

    # --- observability / safety ---
    # Multiple graph branches (sql_node/rag_node/tool_node) can run in the same
    # superstep and each append here; both fields use additive reducers so
    # LangGraph merges concurrent writes instead of raising an
    # InvalidUpdateError. Each node contributes only its own delta (a
    # single-entry list / the integer 1), never the full accumulated state.
    trace: Annotated[list[dict], operator.add]
    step_count: Annotated[int, operator.add]
    aborted_reason: Optional[str]


class IntentClassification(TypedDict, total=False):
    needs_rag: bool
    needs_sql: bool
    needs_tool: bool
    tool_name: Optional[Literal["customer_lookup", "invoice_lookup", "ticket_lookup", "create_support_ticket"]]
    customer_reference: Optional[str]
    rag_search_query: Optional[str]
    ticket_subject: Optional[str]
    ticket_description: Optional[str]
    ticket_severity: Optional[str]
    ticket_category: Optional[str]
