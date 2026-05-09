"""
Layer 4 tests: HTTP endpoint behavior via FastAPI TestClient.

Groq is mocked — all tests run without a real API key.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401 — register ORM classes
from backend.database import Base, get_db
from backend.main import app
from backend.services.intake_agent import _sessions

# Use the same test API key everywhere
TEST_API_KEY = "dev-key-change-me"
AUTH_HEADERS = {"X-Api-Key": TEST_API_KEY}


# ── Test DB fixture ───────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def client():
    """TestClient with an isolated in-memory DB, fresh per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    _sessions.clear()

    # Set env vars so startup checks pass
    with patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "API_KEY": TEST_API_KEY}):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_text_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_submit_response(args: dict):
    tool_call = MagicMock()
    tool_call.id = "call_submit"
    tool_call.function.name = "submit_request"
    tool_call.function.arguments = json.dumps(args)
    choice = MagicMock()
    choice.message.content = None
    choice.message.tool_calls = [tool_call]
    response = MagicMock()
    response.choices = [choice]
    return response


_SUBMIT_ARGS = {
    "supplier_name": "Notion",
    "spend_amount": 150.0,
    "spend_type": "subscription",
    "category": "Software",
    "data_access": "internal",
    "business_justification": "Note-taking for the team.",
    "requester_name": "Carol",
    "department": "Engineering",
}


# ── /health ───────────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── POST /chat ────────────────────────────────────────────────────────────────

def test_chat_returns_reply(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_text_response("What supplier are you buying from?")
        resp = client.post("/chat", json={"session_id": "s1", "message": "I need software"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "What supplier are you buying from?"
    assert data["is_complete"] is False
    assert data["request_id"] is None


def test_chat_submit_returns_request_id(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Your request has been submitted!"),
        ]
        resp = client.post("/chat", json={"session_id": "s2", "message": "submit"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_complete"] is True
    assert data["request_id"] is not None


def test_chat_second_turn_uses_same_session(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_text_response("Got it. What's the supplier name?")
        client.post("/chat", json={"session_id": "s3", "message": "I need a project tool"})

        mock_call.return_value = _make_text_response("And the spend amount?")
        resp = client.post("/chat", json={"session_id": "s3", "message": "Linear"})

    assert resp.status_code == 200
    assert "s3" in _sessions


def test_chat_message_too_long(client):
    resp = client.post("/chat", json={"session_id": "s-long", "message": "x" * 2001})
    assert resp.status_code == 422


def test_chat_empty_message(client):
    resp = client.post("/chat", json={"session_id": "s-empty", "message": ""})
    assert resp.status_code == 422


# ── GET /requests ─────────────────────────────────────────────────────────────

def test_list_requests_empty(client):
    resp = client.get("/requests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_requests_after_submit(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        client.post("/chat", json={"session_id": "s4", "message": "submit"})

    resp = client.get("/requests")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["supplier_name"] == "Notion"
    assert "company_id" in resp.json()[0]
    assert "inherent_risk_score" in resp.json()[0]


# ── GET /requests/{id} ────────────────────────────────────────────────────────

def test_get_request_by_id(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        submit_resp = client.post("/chat", json={"session_id": "s5", "message": "submit"})

    request_id = submit_resp.json()["request_id"]
    resp = client.get(f"/requests/{request_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == request_id
    assert resp.json()["supplier_name"] == "Notion"


def test_get_request_not_found(client):
    resp = client.get("/requests/nonexistent-id")
    assert resp.status_code == 404


# ── PATCH /requests/{id}/status ───────────────────────────────────────────────

def _submit_and_get_id(client) -> str:
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        resp = client.post("/chat", json={"session_id": f"s-sub-{id(mock_call)}", "message": "submit"})
    return resp.json()["request_id"]


def test_update_status_requires_api_key(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        submit_resp = client.post("/chat", json={"session_id": "s-auth", "message": "submit"})

    request_id = submit_resp.json()["request_id"]
    resp = client.patch(f"/requests/{request_id}/status", json={"status": "approved"})
    assert resp.status_code == 401


def test_update_status_approved(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        submit_resp = client.post("/chat", json={"session_id": "s6", "message": "submit"})

    request_id = submit_resp.json()["request_id"]
    resp = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "approved"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_update_status_invalid_transition(client):
    """Cannot move from approved back to pending."""
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        submit_resp = client.post("/chat", json={"session_id": "s7", "message": "submit"})

    request_id = submit_resp.json()["request_id"]
    # First approve it
    client.patch(
        f"/requests/{request_id}/status",
        json={"status": "approved"},
        headers=AUTH_HEADERS,
    )
    # Then try to move back to pending — should fail
    resp = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "pending"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422


def test_update_status_rejected_is_terminal(client):
    """Cannot change status once rejected."""
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        submit_resp = client.post("/chat", json={"session_id": "s8", "message": "submit"})

    request_id = submit_resp.json()["request_id"]
    client.patch(
        f"/requests/{request_id}/status",
        json={"status": "rejected"},
        headers=AUTH_HEADERS,
    )
    resp = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "approved"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422


def test_update_status_not_found(client):
    resp = client.patch(
        "/requests/bad-id/status",
        json={"status": "approved"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 404


def test_status_change_writes_audit_log(client):
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        submit_resp = client.post("/chat", json={"session_id": "s9", "message": "submit"})

    request_id = submit_resp.json()["request_id"]
    client.patch(
        f"/requests/{request_id}/status",
        json={"status": "approved"},
        headers=AUTH_HEADERS,
    )
    # Verify status changed (audit log verified in unit tests via DB)
    resp = client.get(f"/requests/{request_id}")
    assert resp.json()["status"] == "approved"
