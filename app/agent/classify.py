"""Intent classification: decide which capability/capabilities a query needs.

Tries an LLM-based structured classification first (when a real provider is
configured), and always falls back to a deterministic keyword-based
heuristic if the LLM call fails, times out, or returns something that
doesn't validate — classification must never crash a turn, and must always
produce *something* usable. The heuristic is also the sole path in offline
mode, which keeps routing behavior fully deterministic for tests/eval.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.agent.llm import is_offline
from app.agent.state import IntentClassification
from app.db.models import Role

logger = logging.getLogger("app.agent.classify")

TICKET_CREATE_RE = re.compile(r"\b(open|create|file|log|raise|submit)\b.{0,15}\bticket\b", re.IGNORECASE)
TICKET_STATUS_RE = re.compile(r"\bticket(s)?\b", re.IGNORECASE)
INVOICE_RE = re.compile(r"\b(invoices?|outstanding|overdue|balances?|owe[sd]?|bill(?:ing|s)?)\b", re.IGNORECASE)
POLICY_RE = re.compile(
    r"\b(policy|polic(y|ies)|violat|how (do|does|can|should)|what is|what are|explain|entitled|"
    r"eligib|allowed|guideline|procedure|process|sla|refund|reimburse|remote work|leave|pto)\b",
    re.IGNORECASE,
)
CUSTOMER_LOOKUP_RE = re.compile(r"\b(look ?up|find|search for)\b.{0,20}\bcustomer\b", re.IGNORECASE)
SEV1_RE = re.compile(r"\b(critical|urgent|down|outage|sev-?1|production is down)\b", re.IGNORECASE)


class LLMIntentSchema(BaseModel):
    needs_rag: bool = Field(description="Whether the user's question requires searching the knowledge base / policy documents.")
    needs_sql: bool = Field(description="Whether the question requires querying structured business data (invoices, orders) across the database. Only ever true for internal staff, never for customer self-service.")
    needs_tool: bool = Field(description="Whether a specific business tool must be called (customer lookup, invoice lookup, ticket lookup, or ticket creation).")
    tool_name: str | None = Field(default=None, description="One of customer_lookup, invoice_lookup, ticket_lookup, create_support_ticket, or null.")
    customer_reference: str | None = Field(default=None, description="A customer name or ID mentioned in the question, if any.")
    rag_search_query: str | None = Field(default=None, description="A focused search query to use against the knowledge base.")
    ticket_subject: str | None = Field(default=None, description="If creating a ticket, a short subject line.")
    ticket_description: str | None = Field(default=None, description="If creating a ticket, the full description.")
    ticket_severity: str | None = Field(default=None, description="If creating a ticket: sev1, sev2, or sev3.")
    ticket_category: str | None = Field(default=None, description="If creating a ticket: billing, technical, account, feature_request, or other.")


def classify_heuristic(query: str, role: str) -> IntentClassification:
    result: IntentClassification = {
        "needs_rag": False,
        "needs_sql": False,
        "needs_tool": False,
        "tool_name": None,
        "customer_reference": None,
        "rag_search_query": query,
        "ticket_subject": None,
        "ticket_description": None,
        "ticket_severity": None,
        "ticket_category": None,
    }

    is_create_ticket = bool(TICKET_CREATE_RE.search(query))
    mentions_ticket = bool(TICKET_STATUS_RE.search(query))
    mentions_invoice = bool(INVOICE_RE.search(query))
    mentions_policy = bool(POLICY_RE.search(query))
    mentions_customer_lookup = bool(CUSTOMER_LOOKUP_RE.search(query))

    name_match = re.search(r"\bcustomer\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})", query)
    if not name_match:
        name_match = re.search(r"\bfor\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})", query)
    if name_match:
        result["customer_reference"] = name_match.group(1).strip().rstrip(".,?!")

    id_match = re.search(r"\bcustomer(?:\s+id)?\s*#?(\d+)\b", query, re.IGNORECASE)
    if id_match:
        result["customer_reference"] = id_match.group(1)

    if is_create_ticket:
        result["needs_tool"] = True
        result["tool_name"] = "create_support_ticket"
        result["ticket_subject"] = query.strip()[:120]
        result["ticket_description"] = query.strip()
        result["ticket_severity"] = "sev1" if SEV1_RE.search(query) else "sev3"
        result["ticket_category"] = "billing" if mentions_invoice else "technical" if "sync" in query.lower() or "login" in query.lower() or "error" in query.lower() else "other"
    elif mentions_customer_lookup and role != Role.CUSTOMER.value:
        result["needs_tool"] = True
        result["tool_name"] = "customer_lookup"
    elif mentions_ticket and not mentions_invoice:
        result["needs_tool"] = True
        result["tool_name"] = "ticket_lookup"

    if mentions_invoice:
        if role == Role.CUSTOMER.value:
            result["needs_tool"] = True
            result["tool_name"] = result["tool_name"] or "invoice_lookup"
        else:
            result["needs_sql"] = True

    if mentions_policy or (not result["needs_tool"] and not result["needs_sql"]):
        result["needs_rag"] = True

    # Multi-step case: "invoices ... policy" style queries need both.
    if mentions_invoice and mentions_policy:
        result["needs_rag"] = True
        if role == Role.CUSTOMER.value:
            result["needs_tool"] = True
            result["tool_name"] = result["tool_name"] or "invoice_lookup"
        else:
            result["needs_sql"] = True

    return result


def classify_intent(query: str, role: str, chat_model=None) -> IntentClassification:
    if not is_offline() and chat_model is not None:
        try:
            structured = chat_model.with_structured_output(LLMIntentSchema)
            system = (
                "You are the routing component of an enterprise assistant. Classify what the "
                "user's question needs. Never set needs_sql=true if the caller's role is "
                "'customer' (customers use scoped account tools instead of raw SQL). "
                "Respond only via the provided schema."
            )
            llm_result: LLMIntentSchema = structured.invoke(
                [
                    {"role": "system", "content": f"{system}\nCaller role: {role}"},
                    {"role": "user", "content": query},
                ]
            )
            data = llm_result.model_dump()
            if role == Role.CUSTOMER.value:
                data["needs_sql"] = False
            return data  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 - any LLM/classification failure -> heuristic fallback
            logger.warning("llm_classification_failed falling back to heuristic", exc_info=True)

    return classify_heuristic(query, role)
