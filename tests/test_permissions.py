import pytest

from app.tools.permissions import ToolPermissionError, authorize_tool_call


def test_employee_can_use_customer_lookup(current_users):
    authorize_tool_call(current_users["employee"], "customer_lookup", {"customer_id": 2})


def test_customer_cannot_use_customer_lookup(current_users):
    with pytest.raises(ToolPermissionError):
        authorize_tool_call(current_users["acme_customer"], "customer_lookup", {"customer_id": 1})


def test_customer_cannot_use_sql_query(current_users):
    with pytest.raises(ToolPermissionError):
        authorize_tool_call(current_users["acme_customer"], "sql_query", {})


def test_customer_invoice_lookup_is_forced_to_own_account(current_users):
    # Even though the caller asks for customer_id=2 (Blue Harbor), an Acme
    # customer's call must be rewritten to their own account (1), never
    # trusting the argument the caller (or an LLM tricked by injected text)
    # supplied.
    result = authorize_tool_call(current_users["acme_customer"], "invoice_lookup", {"customer_id": 2})
    assert result.args["customer_id"] == 1


def test_employee_invoice_lookup_keeps_requested_customer(current_users):
    result = authorize_tool_call(current_users["employee"], "invoice_lookup", {"customer_id": 2})
    assert result.args["customer_id"] == 2


def test_create_ticket_always_requires_approval_for_every_role(current_users):
    for user in current_users.values():
        result = authorize_tool_call(user, "create_support_ticket", {"customer_id": user.customer_id or 1})
        assert result.requires_approval is True


def test_unknown_tool_rejected(current_users):
    with pytest.raises(ToolPermissionError):
        authorize_tool_call(current_users["admin"], "delete_everything", {})
