"""Defense-in-depth guard around LLM-generated SQL.

Three independent layers, any one of which alone would be enough to stop a
destructive query, are stacked here:

1. **AST allow-listing** (sqlglot): the query must parse as a single ``SELECT``
   that only touches a small set of whitelisted tables/columns. Anything else
   (INSERT/UPDATE/DELETE/DROP/ATTACH/PRAGMA, multiple statements, DDL, writes
   disguised as CTEs, etc.) is rejected before it ever reaches a database
   connection.
2. **OS/driver-level read-only connection**: the query executes over a SQLite
   connection opened with ``mode=ro``, so even a bug in layer 1 cannot result
   in a write — the driver itself refuses.
3. **Row cap + timeout**: results are hard-capped and the call is wrapped in a
   timeout so a pathological query cannot hang or exhaust memory.

This module is the *only* code path the agent's SQL tool is allowed to use to
touch the database with LLM-generated text. The rest of the app (writing
conversations, tickets, etc.) never goes through here — it uses the ORM with
explicit, developer-written statements instead.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.config import settings

ALLOWED_TABLES = {"customers", "orders", "invoices", "support_tickets"}

# Fast-path rejection of obviously write/DDL/introspection statements before
# the (more expensive, more precise) AST parse below. This is belt-and-
# suspenders on top of the table whitelist and the read-only connection —
# NOT a column-level filter; no column allow-list exists anywhere in this
# module (or needs to: `users`, the only table with a secret column, is
# already outside ALLOWED_TABLES entirely).
# `pragma\w*` (not `\bpragma\b`) deliberately also matches SQLite's
# `pragma_<name>(...)` table-valued function syntax (e.g.
# `pragma_table_info(...)`), which `\b` word-boundary matching alone would
# miss since there's no boundary between "pragma" and an immediately
# following underscore.
DISALLOWED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma\w*|vacuum|"
    r"replace|grant|reindex|trigger|transaction|begin|commit|rollback)\b",
    re.IGNORECASE,
)

MAX_ROWS = 200
DEFAULT_LIMIT = 100


class SQLGuardError(ValueError):
    """Raised when a query fails validation and must not be executed."""


@dataclass
class ValidatedQuery:
    sql: str
    tables: set[str] = field(default_factory=set)


def validate_select_query(raw_sql: str) -> ValidatedQuery:
    """Validate that `raw_sql` is a single, safe, read-only SELECT statement.

    Raises SQLGuardError with a human-readable reason on any violation.
    """
    if not raw_sql or not raw_sql.strip():
        raise SQLGuardError("Empty query is not allowed.")

    if DISALLOWED_KEYWORDS.search(raw_sql):
        raise SQLGuardError(
            "Query contains a disallowed keyword (only read-only SELECT statements are permitted)."
        )

    try:
        statements = sqlglot.parse(raw_sql, read="sqlite")
    except Exception as exc:  # noqa: BLE001 - surface parser errors as guard errors
        raise SQLGuardError(f"Query could not be parsed: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLGuardError("Only a single SELECT statement is allowed per query.")

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SQLGuardError(f"Only SELECT statements are allowed, got: {type(tree).__name__}")

    # Reject anything that could mutate state even inside a SELECT context,
    # e.g. SQLite's `PRAGMA` masquerading, or attached-database references.
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create, exp.Command)):
            raise SQLGuardError(f"Disallowed SQL construct: {type(n).__name__}")

    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables:
        raise SQLGuardError("Query does not reference any table.")

    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        raise SQLGuardError(
            f"Query references table(s) not permitted for this tool: {sorted(disallowed)}. "
            f"Allowed tables: {sorted(ALLOWED_TABLES)}."
        )

    # Enforce a row cap by wrapping in an outer LIMIT if the query doesn't
    # already have one, or by tightening an existing limit that's too large.
    existing_limit = tree.args.get("limit")
    if existing_limit is None:
        tree = tree.limit(DEFAULT_LIMIT)
    else:
        try:
            limit_value = int(existing_limit.expression.this)
            if limit_value > MAX_ROWS:
                tree.set("limit", exp.Limit(this=None, expression=exp.Literal.number(MAX_ROWS)))
        except (AttributeError, ValueError, TypeError):
            tree.set("limit", exp.Limit(this=None, expression=exp.Literal.number(DEFAULT_LIMIT)))

    safe_sql = tree.sql(dialect="sqlite")
    return ValidatedQuery(sql=safe_sql, tables=tables)


def execute_readonly_query(raw_sql: str) -> list[dict]:
    """Validate then execute a query against a strictly read-only connection."""
    validated = validate_select_query(raw_sql)

    uri = f"file:{settings.sql_db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.execute("PRAGMA query_only = 1;")
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(validated.sql)
        rows = cursor.fetchmany(MAX_ROWS)
        return [dict(row) for row in rows]
    except sqlite3.OperationalError as exc:
        raise SQLGuardError(f"Query failed to execute: {exc}") from exc
    finally:
        conn.close()
