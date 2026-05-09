"""
Questionnaire tests — question bank logic and HTTP endpoint.

Pure function tests need no fixtures. HTTP tests reuse the client fixture
from test_api.py's pattern (inline fixture here so this file is self-contained).
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models  # noqa: F401
from backend.database import Base, get_db
from backend.main import app
from backend.services.intake_agent import _sessions
from backend.services.questionnaire import get_questions, _ALL_QUESTIONS, _DEPTH_SLICE

TEST_API_KEY = "dev-key-change-me"


# ── HTTP client fixture ───────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def client():
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

    with patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "API_KEY": TEST_API_KEY}):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _make_text_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    response = MagicMock()
    response.choices = [choice]
    return response


_SUBMIT_ARGS = {
    "supplier_name": "Acme Security Ltd",
    "spend_amount": 25_000.0,
    "spend_type": "recurring",
    "category": "Services",
    "data_access": "personal_data",
    "business_justification": "Security audit services.",
    "requester_name": "Frank",
    "department": "IT",
}


def _submit_request(client) -> str:
    """Submit a request via /chat and return its request_id."""
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_submit_response(_SUBMIT_ARGS),
            _make_text_response("Submitted!"),
        ]
        resp = client.post("/chat", json={"session_id": "qs-session", "message": "submit"})
    return resp.json()["request_id"]


# ── Pure function tests (no HTTP, no DB) ──────────────────────────────────────

def test_basic_returns_5_questions():
    assert len(get_questions("basic")) == 5


def test_standard_returns_10_questions():
    assert len(get_questions("standard")) == 10


def test_deep_returns_15_questions():
    assert len(get_questions("deep_due_diligence")) == 15


def test_standard_includes_all_basic_questions():
    basic = get_questions("basic")
    standard = get_questions("standard")
    assert standard[:5] == basic


def test_deep_includes_all_standard_questions():
    standard = get_questions("standard")
    deep = get_questions("deep_due_diligence")
    assert deep[:10] == standard


def test_unknown_depth_falls_back_to_basic():
    result = get_questions("enterprise_extra_special")
    assert len(result) == 5
    assert result == get_questions("basic")


def test_all_question_ids_are_unique():
    ids = [q["id"] for q in _ALL_QUESTIONS]
    assert len(ids) == len(set(ids)), "Duplicate question IDs found"


def test_all_questions_have_required_fields():
    required_keys = {"id", "section", "text", "type", "required"}
    for q in _ALL_QUESTIONS:
        missing = required_keys - q.keys()
        assert not missing, f"Question {q.get('id')} is missing keys: {missing}"


def test_question_types_are_valid():
    valid_types = {"text", "yes_no", "upload"}
    for q in _ALL_QUESTIONS:
        assert q["type"] in valid_types, f"Question {q['id']} has invalid type: {q['type']!r}"


def test_all_questions_in_bank_equals_max_depth():
    assert len(_ALL_QUESTIONS) == _DEPTH_SLICE["deep_due_diligence"]


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

def test_questionnaire_endpoint_returns_correct_count(client):
    request_id = _submit_request(client)
    resp = client.get(f"/requests/{request_id}/questionnaire")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_questions"] == len(data["questions"])
    assert data["total_questions"] > 0


def test_questionnaire_endpoint_returns_request_id_and_supplier(client):
    request_id = _submit_request(client)
    resp = client.get(f"/requests/{request_id}/questionnaire")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == request_id
    assert data["supplier_name"] == _SUBMIT_ARGS["supplier_name"]


def test_questionnaire_endpoint_depth_matches_stored_depth(client):
    """personal_data + spend>10k should yield deep_due_diligence or standard."""
    request_id = _submit_request(client)

    # Verify stored depth via /requests/{id}
    req_resp = client.get(f"/requests/{request_id}")
    stored_depth = req_resp.json()["questionnaire_depth"]

    qs_resp = client.get(f"/requests/{request_id}/questionnaire")
    assert qs_resp.json()["questionnaire_depth"] == stored_depth


def test_questionnaire_questions_have_correct_structure(client):
    request_id = _submit_request(client)
    resp = client.get(f"/requests/{request_id}/questionnaire")
    questions = resp.json()["questions"]
    for q in questions:
        assert "id" in q
        assert "section" in q
        assert "text" in q
        assert "type" in q
        assert "required" in q
        assert q["type"] in ("text", "yes_no", "upload")


def test_questionnaire_endpoint_404_on_bad_id(client):
    resp = client.get("/requests/nonexistent-uuid/questionnaire")
    assert resp.status_code == 404


def test_questionnaire_endpoint_400_if_no_depth(client):
    """A request with questionnaire_depth=None returns 400."""
    from backend.models import ProcurementRequestORM
    from backend.database import SessionLocal
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Insert a bare request with no questionnaire_depth directly into the test DB
    # We do this by using the overridden get_db session from the app
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    req = ProcurementRequestORM(
        session_id="bare-session",
        supplier_name="NoDepth Corp",
        spend_amount=100.0,
        spend_type="one-time",
        category="Software",
        data_access="none",
        business_justification="Test.",
        requester_name="Tester",
        department="QA",
        questionnaire_depth=None,
    )
    session.add(req)
    session.commit()
    request_id = req.id
    session.close()

    # Override the DB for this specific check
    def override_bare():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_bare
    with TestClient(app) as bare_client:
        resp = bare_client.get(f"/requests/{request_id}/questionnaire")
    app.dependency_overrides[get_db] = client.app.dependency_overrides.get(get_db)

    assert resp.status_code == 400
    assert "questionnaire depth" in resp.json()["detail"].lower()
