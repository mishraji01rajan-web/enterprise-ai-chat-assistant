"""Turn a natural-language data question into a validated read-only SQL query.

When a real LLM provider is configured, the model authors the SQL against a
fixed schema description; the result is always re-validated by
`app.db.sql_guard` before execution (never trusted just because the model
produced it). If the LLM is unavailable, disabled (offline mode), or
produces something that fails validation, a deterministic template query
scoped to the resolved customer is used instead — this keeps the SQL path
fully exercisable in tests without a live model, and guarantees a query
always runs rather than failing the whole turn over an LLM hiccup.
"""
from __future__ import annotations

import logging
import re

from app.agent.llm import is_offline
from app.db.sql_guard import SQLGuardError, validate_select_query

logger = logging.getLogger("app.agent.sql_builder")

SCHEMA_DESCRIPTION = """\
Tables (SQLite, read-only):
  customers(id INTEGER, name TEXT, email TEXT, tier TEXT['starter','business','enterprise'], created_at DATETIME)
  orders(id INTEGER, customer_id INTEGER, product_name TEXT, amount REAL, order_date DATETIME)
  invoices(id INTEGER, customer_id INTEGER, order_id INTEGER, amount REAL,
           status TEXT['pending','paid','overdue','disputed'], invoice_date DATETIME, due_date DATETIME)
  support_tickets(id INTEGER, customer_id INTEGER, subject TEXT, description TEXT, category TEXT,
                  severity TEXT['sev1','sev2','sev3'], status TEXT, created_by TEXT, created_at DATETIME)
"""

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    match = _SQL_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _deterministic_query(customer_id: int | None) -> str:
    if customer_id is not None:
        return (
            "SELECT id, amount, status, invoice_date, due_date FROM invoices "
            f"WHERE customer_id = {int(customer_id)} ORDER BY invoice_date DESC"
        )
    return (
        "SELECT id, customer_id, amount, status, invoice_date, due_date FROM invoices "
        "WHERE status = 'overdue' ORDER BY due_date ASC"
    )


def build_sql_query(user_query: str, customer_id: int | None, chat_model=None) -> str:
    if not is_offline() and chat_model is not None:
        try:
            system = (
                "You write a single read-only SQLite SELECT statement to answer the user's "
                "question, using only the schema below. Never write INSERT/UPDATE/DELETE/DDL. "
                "Return ONLY the SQL, no explanation.\n\n" + SCHEMA_DESCRIPTION
            )
            if customer_id is not None:
                system += f"\nScope strictly to customer_id = {customer_id} unless the question clearly asks otherwise.\n"

            response = chat_model.invoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_query},
                ]
            )
            candidate = _extract_sql(response.content if isinstance(response.content, str) else str(response.content))
            validate_select_query(candidate)  # raises if unsafe; we discard candidate.sql (re-validated at exec time)
            return candidate
        except SQLGuardError as exc:
            logger.warning("llm_generated_sql_rejected reason=%s falling back to template", exc)
        except Exception:  # noqa: BLE001
            logger.warning("llm_sql_generation_failed falling back to template", exc_info=True)

    return _deterministic_query(customer_id)
