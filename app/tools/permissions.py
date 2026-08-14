"""Backend-enforced tool authorization.

This is the single choke point every tool call passes through before it
touches the database. It is deliberately independent of the LLM/agent: the
model can *ask* for anything (including things a prompt-injection attempt
talked it into), but this module decides what actually executes, based on
the authenticated user's role — not on anything the model said.

Two layers of enforcement:
1. Role whitelist per tool (RBAC).
2. Row-level scoping for the CUSTOMER role: a customer's `customer_id` is
   taken from their authenticated session and forcibly substituted into the
   tool arguments, overriding anything the agent/LLM supplied. A customer
   user can therefore never read or act on another customer's data, even if
   the LLM is tricked into requesting it.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.auth.schemas import CurrentUser
from app.db.models import Role

# Tools that only read data.
READ_ONLY_TOOLS = {"customer_lookup", "invoice_lookup", "ticket_lookup", "sql_query"}

# Tools that modify data and therefore always require explicit human approval
# before execution, regardless of who is calling them (see SEC-002).
WRITE_TOOLS = {"create_support_ticket"}

ALL_TOOLS = READ_ONLY_TOOLS | WRITE_TOOLS

TOOL_ROLES: dict[str, set[Role]] = {
    "customer_lookup": {Role.ADMIN, Role.EMPLOYEE},
    "invoice_lookup": {Role.ADMIN, Role.EMPLOYEE, Role.CUSTOMER},
    "ticket_lookup": {Role.ADMIN, Role.EMPLOYEE, Role.CUSTOMER},
    "create_support_ticket": {Role.ADMIN, Role.EMPLOYEE, Role.CUSTOMER},
    "sql_query": {Role.ADMIN, Role.EMPLOYEE},
}

# Tools whose arguments include a customer_id that must be pinned to the
# caller's own account when the caller has the CUSTOMER role.
CUSTOMER_SCOPED_TOOLS = {"invoice_lookup", "ticket_lookup", "create_support_ticket"}
# Tools entirely unavailable to the CUSTOMER role (checked via TOOL_ROLES too;
# listed here for clarity/documentation).
CUSTOMER_FORBIDDEN_TOOLS = {"customer_lookup", "sql_query"}


class ToolPermissionError(PermissionError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class AuthorizedCall:
    tool_name: str
    args: dict
    requires_approval: bool


def authorize_tool_call(user: CurrentUser, tool_name: str, args: dict) -> AuthorizedCall:
    """Validate + (if needed) rewrite a tool call for the given user.

    Raises ToolPermissionError if the call is not permitted. Returns the
    (possibly rewritten) args and whether human approval is required before
    the call may run.
    """
    if tool_name not in TOOL_ROLES:
        raise ToolPermissionError(f"Unknown tool '{tool_name}'.")

    allowed_roles = TOOL_ROLES[tool_name]
    if user.role not in allowed_roles:
        raise ToolPermissionError(
            f"Role '{user.role.value}' is not authorized to use tool '{tool_name}'."
        )

    safe_args = dict(args)

    if user.role == Role.CUSTOMER:
        if tool_name in CUSTOMER_FORBIDDEN_TOOLS:
            raise ToolPermissionError(
                f"Customer accounts cannot use tool '{tool_name}'."
            )
        if tool_name in CUSTOMER_SCOPED_TOOLS:
            if user.customer_id is None:
                raise ToolPermissionError("Customer account is not linked to a customer record.")
            # Force-override: never trust an LLM-supplied customer_id for a
            # customer-role caller, even if the prompt/injection asked for
            # a different one.
            safe_args["customer_id"] = user.customer_id

    return AuthorizedCall(
        tool_name=tool_name,
        args=safe_args,
        requires_approval=tool_name in WRITE_TOOLS,
    )
