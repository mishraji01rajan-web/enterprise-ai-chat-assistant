"""Business tool implementations.

Every function here is a plain, developer-written database operation — no
LLM-generated SQL, no dynamic code execution. The agent can only reach these
through `execute_tool`, which always re-checks authorization via
`app.tools.permissions.authorize_tool_call` first. Write operations
(`create_support_ticket`) never execute directly from this module when
called through the agent path — see `app/agent/graph.py` and
`app/api/approvals.py` for the human-approval gate.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Customer, Invoice, SupportTicket, TicketSeverity, TicketStatus


def customer_lookup(db: Session, *, customer_id: int | None = None, name: str | None = None) -> dict:
    query = db.query(Customer)
    if customer_id is not None:
        query = query.filter(Customer.id == customer_id)
    elif name:
        query = query.filter(Customer.name.ilike(f"%{name}%"))
    else:
        return {"error": "customer_id or name is required"}

    customer = query.first()
    if not customer:
        return {"found": False}
    return {
        "found": True,
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "tier": customer.tier.value,
        "customer_since": customer.created_at.date().isoformat(),
    }


def invoice_lookup(db: Session, *, customer_id: int, status: str | None = None) -> dict:
    query = db.query(Invoice).filter(Invoice.customer_id == customer_id)
    if status:
        query = query.filter(Invoice.status == status)
    invoices = query.order_by(Invoice.invoice_date.desc()).all()
    return {
        "customer_id": customer_id,
        "count": len(invoices),
        "invoices": [
            {
                "id": inv.id,
                "amount": inv.amount,
                "status": inv.status.value,
                "invoice_date": inv.invoice_date.date().isoformat(),
                "due_date": inv.due_date.date().isoformat(),
            }
            for inv in invoices
        ],
    }


def ticket_lookup(db: Session, *, customer_id: int, status: str | None = None) -> dict:
    query = db.query(SupportTicket).filter(SupportTicket.customer_id == customer_id)
    if status:
        query = query.filter(SupportTicket.status == status)
    tickets = query.order_by(SupportTicket.created_at.desc()).all()
    return {
        "customer_id": customer_id,
        "count": len(tickets),
        "tickets": [
            {
                "id": t.id,
                "subject": t.subject,
                "category": t.category,
                "severity": t.severity.value,
                "status": t.status.value,
                "created_at": t.created_at.date().isoformat(),
            }
            for t in tickets
        ],
    }


def create_support_ticket(
    db: Session,
    *,
    customer_id: int,
    subject: str,
    description: str,
    category: str = "other",
    severity: str = "sev3",
    created_by: str = "assistant",
) -> dict:
    """Executes the actual write. Only ever called after human approval."""
    customer = db.query(Customer).filter(Customer.id == customer_id).one_or_none()
    if not customer:
        return {"error": f"No customer with id {customer_id}"}

    ticket = SupportTicket(
        customer_id=customer_id,
        subject=subject[:300],
        description=description,
        category=category if category in {"billing", "technical", "account", "feature_request", "other"} else "other",
        severity=TicketSeverity(severity) if severity in {s.value for s in TicketSeverity} else TicketSeverity.SEV3,
        status=TicketStatus.OPEN,
        created_by=created_by,
        created_at=datetime.utcnow(),
    )
    db.add(ticket)
    db.flush()
    return {
        "created": True,
        "ticket_id": ticket.id,
        "customer_id": customer_id,
        "subject": ticket.subject,
        "severity": ticket.severity.value,
        "status": ticket.status.value,
    }


TOOL_FUNCTIONS = {
    "customer_lookup": customer_lookup,
    "invoice_lookup": invoice_lookup,
    "ticket_lookup": ticket_lookup,
    "create_support_ticket": create_support_ticket,
}
