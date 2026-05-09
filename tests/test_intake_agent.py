"""
Layer 3 tests: intake agent behavior.

All Groq API calls are mocked — no real API key needed.
Each test exercises a specific behavioral requirement.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.services import intake_agent
from backend.services.intake_agent import (
    SESSION_TTL_MINUTES,
    SYSTEM_PROMPT,
    _clear_session,
    _get_messages,
    _save_request,
    _sessions,
    chat,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_text_response(content: str):
    """Build a mock Groq response that returns plain text (no tool call)."""
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_response(tool_name: str, args: dict, call_id: str = "call_1"):
    """Build a mock Groq response that calls a tool."""
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.function.name = tool_name
    tool_call.function.arguments = json.dumps(args)

    choice = MagicMock()
    choice.message.content = None
    choice.message.tool_calls = [tool_call]
    choice.finish_reason = "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    return response


def _full_submit_args() -> dict:
    return {
        "supplier_name": "Figma Inc.",
        "spend_amount": 200.0,
        "spend_type": "subscription",
        "category": "Software",
        "data_access": "internal",
        "business_justification": "Design team needs Figma.",
        "requester_name": "Alice",
        "department": "Design",
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_session_created_on_first_message(db):
    """Session with system prompt is auto-created on first chat call."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_text_response("What supplier are you looking to purchase from?")
        chat("sess-new", "I need to buy something", db)

    assert "sess-new" in _sessions
    assert _sessions["sess-new"]["messages"][0]["role"] == "system"
    assert _sessions["sess-new"]["messages"][0]["content"] == SYSTEM_PROMPT


def test_single_turn_reply(db):
    """Model returns a conversational reply — function returns that reply."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_text_response("What supplier are you looking to purchase from?")
        reply, is_complete, request_id = chat("sess-a", "I need software", db)

    assert reply == "What supplier are you looking to purchase from?"
    assert is_complete is False
    assert request_id is None


def test_multi_turn_session_continues(db):
    """Second message in same session continues existing history."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_text_response("Got it. What is the supplier name?")
        chat("sess-b", "I need a design tool", db)

        mock_call.return_value = _make_text_response("And the annual cost?")
        chat("sess-b", "Figma", db)

    messages = _sessions["sess-b"]["messages"]
    user_messages = [m for m in messages if m["role"] == "user"]
    assert len(user_messages) == 2
    assert user_messages[0]["content"] == "I need a design tool"
    assert user_messages[1]["content"] == "Figma"


def test_submit_saves_request(db):
    """submit_request tool call creates a DB row and returns is_complete=True."""
    _sessions.clear()

    submit_args = _full_submit_args()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", submit_args),
            _make_text_response("Your request has been submitted! ID: ..."),
        ]
        reply, is_complete, request_id = chat("sess-submit", "submit it", db)

    assert is_complete is True
    assert request_id is not None

    from backend.models import ProcurementRequestORM
    req = db.get(ProcurementRequestORM, request_id)
    assert req is not None
    assert req.supplier_name == "Figma Inc."
    assert req.spend_amount == 200.0
    assert req.status == "pending"


def test_session_cleared_after_submit(db):
    """Session is removed from _sessions after successful submission."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", _full_submit_args()),
            _make_text_response("Submitted!"),
        ]
        chat("sess-clear", "submit it", db)

    assert "sess-clear" not in _sessions


def test_session_not_recreated_after_submit(db):
    """After submit clears the session, the reply is NOT stored back into _sessions."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", _full_submit_args()),
            _make_text_response("All done!"),
        ]
        chat("sess-norecreate", "submit it", db)

    # Session must NOT exist after submit
    assert "sess-norecreate" not in _sessions


def test_enum_normalization_on_submit(db):
    """Non-canonical enum values (e.g. 'annual') are normalized before DB write."""
    _sessions.clear()

    args = _full_submit_args()
    args["spend_type"] = "annual"          # should normalize to "recurring"
    args["category"] = "saas"              # should normalize to "Software"
    args["data_access"] = "customer_data"  # should normalize to "personal_data"

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", args),
            _make_text_response("Submitted!"),
        ]
        _, _, request_id = chat("sess-norm", "submit", db)

    from backend.models import ProcurementRequestORM
    req = db.get(ProcurementRequestORM, request_id)
    assert req.spend_type == "recurring"
    assert req.category == "Software"
    assert req.data_access == "personal_data"


def test_risk_score_persisted(db):
    """Risk score and label are computed and persisted on submit."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", _full_submit_args()),
            _make_text_response("Submitted!"),
        ]
        _, _, request_id = chat("sess-risk", "submit", db)

    from backend.models import ProcurementRequestORM
    req = db.get(ProcurementRequestORM, request_id)
    assert req.risk_score is not None
    assert req.risk_label in ("low", "medium", "high", "critical")


def test_policy_output_persisted(db):
    """Policy engine output (approvers, flags, questionnaire_depth) is persisted."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", _full_submit_args()),
            _make_text_response("Submitted!"),
        ]
        _, _, request_id = chat("sess-policy", "submit", db)

    from backend.models import ProcurementRequestORM
    req = db.get(ProcurementRequestORM, request_id)
    assert isinstance(req.required_approvers, list)
    assert len(req.required_approvers) >= 1  # at minimum, manager
    assert req.questionnaire_depth in ("basic", "standard", "deep_due_diligence")


def test_audit_log_written_on_submit(db):
    """An audit log 'created' entry is written when a request is submitted."""
    _sessions.clear()

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("submit_request", _full_submit_args()),
            _make_text_response("Submitted!"),
        ]
        _, _, request_id = chat("sess-audit", "submit", db)

    from backend.models import AuditLogORM
    log = db.query(AuditLogORM).filter_by(request_id=request_id, action="created").first()
    assert log is not None
    assert log.actor == "Alice"


def test_extract_state_continues_loop(db):
    """extract_state tool call does NOT break the loop — model continues to reply."""
    _sessions.clear()

    extract_args = {"missing_fields": ["spend_amount", "category"]}

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.side_effect = [
            _make_tool_response("extract_state", extract_args),
            _make_text_response("What is your expected spend amount?"),
        ]
        reply, is_complete, _ = chat("sess-extract", "I need Figma", db)

    assert is_complete is False
    assert reply == "What is your expected spend amount?"


def test_ttl_expired_session_is_reset(db):
    """A session older than TTL is evicted and rebuilt fresh on next access."""
    _sessions.clear()

    old_time = datetime.now(timezone.utc) - timedelta(minutes=SESSION_TTL_MINUTES + 1)
    _sessions["sess-old"] = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "old message"},
        ],
        "last_active": old_time,
    }

    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_text_response("Hi, how can I help?")
        chat("sess-old", "new message", db)

    # Old session was evicted; new one has only system prompt + new user message
    messages = _sessions["sess-old"]["messages"]
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "new message"


def test_rate_limit_retry(db):
    """RateLimitError on first two calls triggers retry; third call succeeds."""
    _sessions.clear()

    import groq
    from backend.services._groq_utils import call_with_retry as real_retry

    rate_limit_err = groq.RateLimitError(
        message="rate limit exceeded",
        response=MagicMock(status_code=429, headers={}),
        body={"error": {"message": "rate limit exceeded"}},
    )

    call_count = 0

    def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise rate_limit_err
        choice = MagicMock()
        choice.message.content = "Hello!"
        choice.message.tool_calls = None
        response = MagicMock()
        response.choices = [choice]
        return response

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("backend.services._groq_utils.time") as mock_time, \
         patch("backend.services.intake_agent.call_with_retry", wraps=real_retry), \
         patch("backend.services.intake_agent.get_client", return_value=mock_client):
        reply, _, _ = chat("sess-retry", "hi", db)

    assert reply == "Hello!"
    assert call_count == 3  # failed twice, succeeded on third
    assert mock_time.sleep.call_count == 2  # slept twice


def test_save_request_high_spend_deep_diligence(db):
    """Spend > £50k triggers deep_due_diligence questionnaire depth."""
    data = {
        "supplier_name": "BigCorp",
        "spend_amount": 75_000.0,
        "spend_type": "recurring",
        "category": "Services",
        "data_access": "internal",
        "business_justification": "Enterprise consulting contract.",
        "requester_name": "Bob",
        "department": "Operations",
        "is_new_supplier": False,
    }
    req = _save_request(data, "sess-highspend", db)
    assert req.questionnaire_depth == "deep_due_diligence"
    assert "finance" in req.required_approvers


def test_save_request_negative_spend_raises(db):
    """Negative spend_amount raises ValueError before any DB write."""
    import pytest
    data = {
        "supplier_name": "BadCorp",
        "spend_amount": -500.0,
        "spend_type": "one-time",
        "category": "Services",
        "data_access": "none",
        "business_justification": "Test.",
        "requester_name": "Dave",
        "department": "Ops",
    }
    with pytest.raises(ValueError, match="spend_amount must be a positive number"):
        _save_request(data, "sess-neg", db)


def test_save_request_zero_spend_raises(db):
    """Zero spend_amount raises ValueError."""
    import pytest
    data = {
        "supplier_name": "FreeCorp",
        "spend_amount": 0,
        "spend_type": "one-time",
        "category": "Software",
        "data_access": "none",
        "business_justification": "Free trial.",
        "requester_name": "Eve",
        "department": "Product",
    }
    with pytest.raises(ValueError, match="spend_amount must be a positive number"):
        _save_request(data, "sess-zero", db)


def test_loop_guard_exhaustion(db):
    """When model loops past MAX_LOOP_ITERATIONS, agent returns an error message and clears session."""
    _sessions.clear()

    extract_args = {"missing_fields": ["spend_amount"]}

    # Return extract_state 8 times — more than MAX_LOOP_ITERATIONS
    with patch("backend.services.intake_agent.call_with_retry") as mock_call:
        mock_call.return_value = _make_tool_response("extract_state", extract_args)
        reply, is_complete, request_id = chat("sess-loop", "I need something", db)

    assert is_complete is False
    assert request_id is None
    # Session must be cleared after loop exhaustion
    assert "sess-loop" not in _sessions
    # Reply should be an error message, not None
    assert reply is not None
