import json

from tests.conftest import auth_headers


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event_line, data_line = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_line = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        if event_line and data_line is not None:
            events.append({"event": event_line, "data": json.loads(data_line)})
    return events


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_login_success(client):
    resp = client.post("/auth/login", data={"username": "admin", "password": "Admin#2026!"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["access_token"]


def test_login_failure_wrong_password(client):
    resp = client.post("/auth/login", data={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401


def test_login_failure_unknown_user(client):
    resp = client.post("/auth/login", data={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_auth_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_auth_me_returns_profile(client, employee_token):
    resp = client.get("/auth/me", headers=auth_headers(employee_token))
    assert resp.status_code == 200
    assert resp.json()["username"] == "finance.morgan"


def test_chat_requires_auth(client):
    resp = client.post("/chat", json={"message": "hello"})
    assert resp.status_code == 401


def test_chat_rejects_empty_message(client, employee_token):
    resp = client.post("/chat", json={"message": "   "}, headers=auth_headers(employee_token))
    assert resp.status_code == 400


def test_chat_rag_question_streams_and_cites_sources(client, employee_token):
    resp = client.post(
        "/chat",
        json={"message": "How many days of PTO do employees accrue per year?"},
        headers=auth_headers(employee_token),
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    tokens = [e for e in events if e["event"] == "token"]
    done = [e for e in events if e["event"] == "done"][0]
    assert tokens, "expected at least one streamed token event"
    assert any(c["doc_id"] == "HR-001" for c in done["data"]["citations"])
    assert done["data"]["aborted"] is False


def test_multi_turn_conversation_persists_history(client, employee_token):
    headers = auth_headers(employee_token)
    resp1 = client.post("/chat", json={"message": "How many days of PTO do employees get?"}, headers=headers)
    conv_id = parse_sse(resp1.text)[-1]["data"]["conversation_id"]

    resp2 = client.post(
        "/chat", json={"message": "And how many public holidays are observed?", "conversation_id": conv_id}, headers=headers
    )
    assert resp2.status_code == 200
    assert parse_sse(resp2.text)[-1]["data"]["conversation_id"] == conv_id

    detail = client.get(f"/conversations/{conv_id}", headers=headers)
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert len(messages) == 4
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


def test_cannot_read_another_users_conversation(client, employee_token, acme_customer_token):
    headers = auth_headers(employee_token)
    resp = client.post("/chat", json={"message": "What is our refund policy?"}, headers=headers)
    conv_id = parse_sse(resp.text)[-1]["data"]["conversation_id"]

    other_headers = auth_headers(acme_customer_token)
    resp2 = client.get(f"/conversations/{conv_id}", headers=other_headers)
    assert resp2.status_code == 403


def test_ticket_creation_requires_approval_then_executes(client, acme_customer_token):
    headers = auth_headers(acme_customer_token)
    resp = client.post(
        "/chat",
        json={"message": "Please open an urgent ticket, our CloudSync sync keeps failing."},
        headers=headers,
    )
    assert resp.status_code == 200
    done = parse_sse(resp.text)[-1]["data"]
    approval_id = done["pending_approval_id"]
    assert approval_id is not None

    pending = client.get(f"/approvals/{approval_id}", headers=headers)
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    decided = client.post(f"/approvals/{approval_id}/decide", json={"decision": "approve"}, headers=headers)
    assert decided.status_code == 200
    body = decided.json()
    assert body["status"] == "executed"
    result = json.loads(body["result_json"])
    assert result["created"] is True
    assert result["customer_id"] == 1  # Acme, the caller's own account

    # Deciding twice must not be allowed.
    redecide = client.post(f"/approvals/{approval_id}/decide", json={"decision": "approve"}, headers=headers)
    assert redecide.status_code == 409


def test_ticket_creation_can_be_rejected(client, acme_customer_token):
    headers = auth_headers(acme_customer_token)
    resp = client.post(
        "/chat", json={"message": "Please file a ticket about a minor UI glitch."}, headers=headers
    )
    approval_id = parse_sse(resp.text)[-1]["data"]["pending_approval_id"]
    assert approval_id is not None

    decided = client.post(f"/approvals/{approval_id}/decide", json={"decision": "reject"}, headers=headers)
    assert decided.status_code == 200
    assert decided.json()["status"] == "rejected"


def test_customer_cannot_decide_another_customers_approval(client, acme_customer_token, blueharbor_customer_token):
    headers = auth_headers(acme_customer_token)
    resp = client.post(
        "/chat", json={"message": "Please open a ticket, urgent billing issue."}, headers=headers
    )
    approval_id = parse_sse(resp.text)[-1]["data"]["pending_approval_id"]

    other_headers = auth_headers(blueharbor_customer_token)
    resp2 = client.post(f"/approvals/{approval_id}/decide", json={"decision": "approve"}, headers=other_headers)
    assert resp2.status_code == 403


def test_customer_sees_only_own_pending_approvals(client, acme_customer_token, blueharbor_customer_token):
    acme_headers = auth_headers(acme_customer_token)
    client.post("/chat", json={"message": "Please open a ticket about a login issue."}, headers=acme_headers)

    blue_headers = auth_headers(blueharbor_customer_token)
    resp = client.get("/approvals", headers=blue_headers)
    assert resp.status_code == 200
    for approval in resp.json():
        assert approval["requested_by"] == "blueharbor.customer"
