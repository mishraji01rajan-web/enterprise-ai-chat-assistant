"""Single choke point for executing a business tool call.

Responsibilities:
- Re-validate authorization (never trust a prior check from earlier in a
  pipeline; always re-authorize immediately before execution).
- Enforce a hard timeout so a stuck tool cannot hang the agent turn.
- Retry transient database errors a bounded number of times.
- Return a uniform result envelope so calling code (agent nodes, API
  handlers) doesn't need tool-specific error handling.
"""
from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.auth.schemas import CurrentUser
from app.config import settings
from app.tools.business_tools import TOOL_FUNCTIONS
from app.tools.permissions import ToolPermissionError, authorize_tool_call

logger = logging.getLogger("app.tools.executor")

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool-exec")


@dataclass
class ToolExecutionResult:
    ok: bool
    tool_name: str
    args: dict
    requires_approval: bool = False
    data: dict | None = None
    error: str | None = None


@retry(
    retry=retry_if_exception_type(OperationalError),
    stop=stop_after_attempt(3),
    wait=wait_fixed(0.2),
    reraise=True,
)
def _run_with_retry(fn, db: Session, args: dict) -> dict:
    return fn(db, **args)


def execute_tool(
    db: Session, user: CurrentUser, tool_name: str, args: dict, *, bypass_approval_gate: bool = False
) -> ToolExecutionResult:
    """Execute a tool call after re-authorizing it.

    `bypass_approval_gate` is only ever set by the approvals endpoint
    (`app/api/approvals.py`) after a human has explicitly approved a
    previously-proposed write action — it does not skip authorization
    (role/row-scoping is always re-checked), only the "stop and ask for
    approval" short-circuit for tools in `WRITE_TOOLS`.
    """
    try:
        authorized = authorize_tool_call(user, tool_name, args)
    except ToolPermissionError as exc:
        logger.warning("tool_permission_denied user=%s tool=%s reason=%s", user.username, tool_name, exc.message)
        return ToolExecutionResult(ok=False, tool_name=tool_name, args=args, error=exc.message)

    if authorized.requires_approval and not bypass_approval_gate:
        return ToolExecutionResult(
            ok=True, tool_name=tool_name, args=authorized.args, requires_approval=True
        )

    fn = TOOL_FUNCTIONS.get(tool_name)
    if fn is None:
        return ToolExecutionResult(ok=False, tool_name=tool_name, args=args, error=f"Tool '{tool_name}' not implemented")

    future = _pool.submit(_run_with_retry, fn, db, authorized.args)
    try:
        data = future.result(timeout=settings.tool_call_timeout_seconds)
        return ToolExecutionResult(ok=True, tool_name=tool_name, args=authorized.args, data=data)
    except concurrent.futures.TimeoutError:
        logger.error("tool_timeout tool=%s args=%s", tool_name, authorized.args)
        return ToolExecutionResult(ok=False, tool_name=tool_name, args=authorized.args, error="Tool call timed out")
    except Exception as exc:  # noqa: BLE001 - convert any tool failure into a safe envelope
        logger.exception("tool_execution_failed tool=%s", tool_name)
        return ToolExecutionResult(ok=False, tool_name=tool_name, args=authorized.args, error=f"Tool execution failed: {exc}")
