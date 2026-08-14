"""Resolve a free-text customer reference to a concrete customer_id.

For CUSTOMER-role callers this is trivial and non-negotiable: they are
always resolved to their own account, never to whatever the query text (or
an injected instruction) mentions. For staff (employee/admin) callers, a
name or numeric id mentioned in the question is looked up against the
customers table.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.db.models import Customer, Role


def resolve_customer_id(db: Session, user: CurrentUser, customer_reference: str | None) -> tuple[int | None, str | None]:
    """Returns (customer_id, error_message)."""
    if user.role == Role.CUSTOMER:
        return user.customer_id, None

    if not customer_reference:
        return None, None

    ref = customer_reference.strip()
    if ref.isdigit():
        customer = db.query(Customer).filter(Customer.id == int(ref)).one_or_none()
    else:
        customer = db.query(Customer).filter(Customer.name.ilike(f"%{ref}%")).first()

    if customer is None:
        return None, f"No customer found matching '{customer_reference}'."
    return customer.id, None
