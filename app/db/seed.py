"""Deterministic seed data for the demo/eval dataset.

Re-runnable: wipes and recreates all tables so `python -m app.db.seed` (or the
Docker entrypoint) always produces the same known-good dataset that the
README, eval suite, and demo script all rely on.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.auth.security import hash_password
from app.db.models import (
    Base,
    Customer,
    CustomerTier,
    Invoice,
    InvoiceStatus,
    Order,
    Role,
    SupportTicket,
    TicketSeverity,
    TicketStatus,
    User,
)
from app.db.session import engine, get_session

NOW = datetime(2026, 8, 13, 9, 0, 0)


def _d(days_ago: int) -> datetime:
    return NOW - timedelta(days=days_ago)


def seed() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with get_session() as db:
        customers = [
            Customer(id=1, name="Acme Manufacturing", email="ap@acme-mfg.example", tier=CustomerTier.ENTERPRISE, created_at=_d(900)),
            Customer(id=2, name="Blue Harbor Logistics", email="billing@blueharbor.example", tier=CustomerTier.BUSINESS, created_at=_d(700)),
            Customer(id=3, name="Crestline Retail Co", email="finance@crestline.example", tier=CustomerTier.STARTER, created_at=_d(400)),
            Customer(id=4, name="Delta Health Systems", email="accounts@deltahealth.example", tier=CustomerTier.ENTERPRISE, created_at=_d(1200)),
            Customer(id=5, name="Everline Studios", email="ops@everline.example", tier=CustomerTier.BUSINESS, created_at=_d(250)),
        ]
        db.add_all(customers)
        db.flush()

        orders = [
            Order(id=1, customer_id=1, product_name="WidgetPro Enterprise", amount=48000.0, order_date=_d(400)),
            Order(id=2, customer_id=1, product_name="CloudSync Enterprise", amount=36000.0, order_date=_d(200)),
            Order(id=3, customer_id=2, product_name="WidgetPro Business", amount=5880.0, order_date=_d(300)),
            Order(id=4, customer_id=3, product_name="WidgetPro Starter", amount=228.0, order_date=_d(120)),
            Order(id=5, customer_id=4, product_name="WidgetPro Enterprise", amount=60000.0, order_date=_d(600)),
            Order(id=6, customer_id=4, product_name="CloudSync Enterprise", amount=42000.0, order_date=_d(90)),
            Order(id=7, customer_id=5, product_name="CloudSync Pro", amount=3588.0, order_date=_d(60)),
        ]
        db.add_all(orders)
        db.flush()

        # Invoice scenario design (relative to NOW = 2026-08-13):
        #  - Acme (customer 1): two invoices overdue >30 days past due AND total
        #    overdue balance > $5,000 -> clearly VIOLATES payment policy (POL-001).
        #  - Blue Harbor (customer 2): one invoice slightly overdue (<30 days past
        #    due date) -> NOT a violation yet.
        #  - Crestline (customer 3): fully paid, no violation.
        #  - Delta Health (customer 4): 3 simultaneously overdue invoices (violation
        #    via the "3+ overdue" rule) even though amounts are individually small.
        #  - Everline (customer 5): current/paid, no violation.
        invoices = [
            # Acme: due 70 & 45 days ago (>60 days after invoice date => >30 days past due), sum > $5000
            Invoice(id=1, customer_id=1, order_id=1, amount=12000.0, status=InvoiceStatus.OVERDUE, invoice_date=_d(100), due_date=_d(70)),
            Invoice(id=2, customer_id=1, order_id=2, amount=9000.0, status=InvoiceStatus.OVERDUE, invoice_date=_d(75), due_date=_d(45)),
            Invoice(id=3, customer_id=1, order_id=None, amount=3000.0, status=InvoiceStatus.PAID, invoice_date=_d(150), due_date=_d(120)),

            # Blue Harbor: due 10 days ago -> overdue but within the 30-day grace window
            Invoice(id=4, customer_id=2, order_id=3, amount=1960.0, status=InvoiceStatus.OVERDUE, invoice_date=_d(40), due_date=_d(10)),
            Invoice(id=5, customer_id=2, order_id=None, amount=1960.0, status=InvoiceStatus.PAID, invoice_date=_d(70), due_date=_d(40)),

            # Crestline: fully paid
            Invoice(id=6, customer_id=3, order_id=4, amount=228.0, status=InvoiceStatus.PAID, invoice_date=_d(90), due_date=_d(60)),

            # Delta Health: 3 invoices overdue simultaneously (rule: 3+ overdue => violation)
            Invoice(id=7, customer_id=4, order_id=5, amount=1500.0, status=InvoiceStatus.OVERDUE, invoice_date=_d(50), due_date=_d(20)),
            Invoice(id=8, customer_id=4, order_id=5, amount=1200.0, status=InvoiceStatus.OVERDUE, invoice_date=_d(45), due_date=_d(15)),
            Invoice(id=9, customer_id=4, order_id=6, amount=900.0, status=InvoiceStatus.OVERDUE, invoice_date=_d(35), due_date=_d(5)),
            Invoice(id=10, customer_id=4, order_id=None, amount=2200.0, status=InvoiceStatus.PAID, invoice_date=_d(200), due_date=_d(170)),

            # Everline: current, not due yet
            Invoice(id=11, customer_id=5, order_id=7, amount=299.0, status=InvoiceStatus.PENDING, invoice_date=_d(5), due_date=_d(-25)),
        ]
        db.add_all(invoices)
        db.flush()

        tickets = [
            SupportTicket(
                id=1, customer_id=1, subject="CloudSync conflict resolution errors",
                description="Getting repeated conflict-resolution failures since the Enterprise upgrade.",
                category="technical", severity=TicketSeverity.SEV2, status=TicketStatus.IN_PROGRESS,
                created_by="support@example.com", created_at=_d(6),
            ),
            SupportTicket(
                id=2, customer_id=2, subject="Question about Business edition seat limit",
                description="We're approaching 100 seats, what happens if we exceed the Business edition cap?",
                category="account", severity=TicketSeverity.SEV3, status=TicketStatus.OPEN,
                created_by="billing@blueharbor.example", created_at=_d(2),
            ),
            SupportTicket(
                id=3, customer_id=4, subject="SSO login intermittent failures",
                description="A subset of users are getting SSO login failures intermittently since yesterday.",
                category="technical", severity=TicketSeverity.SEV1, status=TicketStatus.OPEN,
                created_by="accounts@deltahealth.example", created_at=_d(1),
            ),
        ]
        db.add_all(tickets)
        db.flush()

        users = [
            User(id=1, username="admin", hashed_password=hash_password("Admin#2026!"), full_name="Priya Nair", role=Role.ADMIN, customer_id=None, created_at=_d(900)),
            User(id=2, username="agent.jordan", hashed_password=hash_password("Support#2026!"), full_name="Jordan Kim", role=Role.EMPLOYEE, customer_id=None, created_at=_d(500)),
            User(id=3, username="finance.morgan", hashed_password=hash_password("Finance#2026!"), full_name="Morgan Ellis", role=Role.EMPLOYEE, customer_id=None, created_at=_d(400)),
            User(id=4, username="acme.customer", hashed_password=hash_password("Acme#2026!"), full_name="Sam Rivera (Acme AP)", role=Role.CUSTOMER, customer_id=1, created_at=_d(300)),
            User(id=5, username="blueharbor.customer", hashed_password=hash_password("Blue#2026!"), full_name="Riley Chen (Blue Harbor)", role=Role.CUSTOMER, customer_id=2, created_at=_d(200)),
        ]
        db.add_all(users)

    print("Seed complete: 5 customers, 7 orders, 11 invoices, 3 tickets, 5 users.")


if __name__ == "__main__":
    seed()
