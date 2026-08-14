"""Shared pytest fixtures.

Critical ordering constraint: environment variables that steer
`app.config.settings` (a module-level singleton) MUST be set before any
`app.*` module is imported anywhere in the test session, because
pydantic-settings reads them once at import time. This file is collected by
pytest before any test module, so setting them here at module scope (not
inside a fixture) guarantees the ordering. Tests run against a throwaway
SQLite file and a throwaway Chroma directory in the OS temp dir — never
against the dev data/ directory.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="enterprise_agent_test_"))
os.environ["SQL_DB_PATH"] = str(_TEST_DIR / "test_app.db")
os.environ["VECTOR_DB_PATH"] = str(_TEST_DIR / "chroma")
os.environ["DATA_DIR"] = str(_TEST_DIR)
os.environ["LLM_PROVIDER"] = "offline"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth.schemas import CurrentUser  # noqa: E402
from app.db.models import Role  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.rag.ingest import ingest_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_environment():
    seed()
    ingest_all()
    yield


@pytest.fixture()
def db_session():
    with get_session() as session:
        yield session


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username: str, password: str) -> str:
    resp = client.post("/auth/login", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture()
def admin_token(client) -> str:
    return _login(client, "admin", "Admin#2026!")


@pytest.fixture()
def employee_token(client) -> str:
    return _login(client, "finance.morgan", "Finance#2026!")


@pytest.fixture()
def support_token(client) -> str:
    return _login(client, "agent.jordan", "Support#2026!")


@pytest.fixture()
def acme_customer_token(client) -> str:
    return _login(client, "acme.customer", "Acme#2026!")


@pytest.fixture()
def blueharbor_customer_token(client) -> str:
    return _login(client, "blueharbor.customer", "Blue#2026!")


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


CURRENT_USERS = {
    "admin": CurrentUser(id=1, username="admin", full_name="Priya Nair", role=Role.ADMIN, customer_id=None),
    "employee": CurrentUser(id=3, username="finance.morgan", full_name="Morgan Ellis", role=Role.EMPLOYEE, customer_id=None),
    "acme_customer": CurrentUser(id=4, username="acme.customer", full_name="Sam Rivera", role=Role.CUSTOMER, customer_id=1),
    "blueharbor_customer": CurrentUser(id=5, username="blueharbor.customer", full_name="Riley Chen", role=Role.CUSTOMER, customer_id=2),
}


@pytest.fixture()
def current_users() -> dict:
    return CURRENT_USERS
