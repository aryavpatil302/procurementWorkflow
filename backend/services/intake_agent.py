"""
Intake agent — conversational procurement request collection.

Maintains per-session conversation history, calls Groq/Llama in a tool-calling
loop, normalizes enum values post-extraction, and saves completed requests to
the database via risk_scorer and policy_engine.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import AuditLogORM, ProcurementRequestORM
from backend.services._groq_utils import MODEL, call_with_retry, get_client
from backend.services._normalizers import (
    normalize_category,
    normalize_data_access,
    normalize_spend_type,
)
from backend.services.policy_engine import evaluate
from backend.services.risk_scorer import score_supplier

# ── Session store ─────────────────────────────────────────────────────────────

SESSION_TTL_MINUTES = 60
_sessions: dict[str, dict] = {}

SYSTEM_PROMPT = """You are Aria, an expert procurement intake specialist. Your job is to guide employees through a new supplier request using natural, efficient conversation — not a form.

You have deep expertise in enterprise procurement, supplier risk, GDPR, DORA, SOC 2, ISO 27001, and approval routing. You understand that poor intake creates months of downstream rework, so you are thorough but never bureaucratic.

REQUIRED FIELDS TO COLLECT:
- supplier_name: The company being engaged
- service_description: What this supplier actually provides (their product or service, one sentence)
- spend_amount: Total spend in GBP (numeric)
- spend_type: one-time | recurring | subscription
- category: Software | Hardware | Services | Marketing | Legal | Other
- geography: Where will this be used? UK | EU | US | Global
- data_access: none | internal | confidential | personal_data
  - none: no company data involved (e.g. office furniture, catering)
  - internal: company devices or tools that hold internal business data (e.g. laptops, servers, collaboration software with internal docs) — use this for hardware like laptops even if employees store work files on them
  - confidential: supplier accesses IP, financial records, or trade secrets
  - personal_data: supplier actively processes personal data about employees or customers as their core function (e.g. HR/payroll software, CRM, analytics platforms with customer records) — do NOT use for hardware purchases
- business_justification: Why the business needs this — the problem it solves or value it delivers
- requester_name: Full name of the person requesting
- department: Their team or department

OPTIONAL (ask only if relevant to the request):
- contract_expiry_date

REPLACEMENT LOGIC (critical):
If user says "replace X with Y", "switch from X to Y", or "instead of X use Y" — the NEW supplier being requested is Y. X is what is being replaced, not the purchase target.
Example: "I want to replace Jira with Linear" → supplier_name = Linear.

APPROVED SUPPLIERS (already onboarded by the company):
Slack→Engineering, Google Workspace→IT, Microsoft 365→IT, Salesforce→Sales, HubSpot→Marketing, Notion→Operations, Figma→Design, GitHub→Engineering, Zoom→IT, Jira/Atlassian→Engineering.
- If the requested supplier IS on this list → is_new_supplier = false. Say: "We already have [supplier] approved under [team]. Is this a separate purchase, or do you need access to the existing licence?"
- If NOT on this list → is_new_supplier = true. Do not ask. Just proceed.
- Only ask if the supplier is ambiguous (e.g. a large multi-product vendor like Oracle or IBM).

REAL-TIME POLICY ALERTS — surface these immediately when the relevant field is captured, inline in the conversation:
- spend > £10,000 → "Note: this spend level requires Finance approval."
- spend > £50,000 → "Note: this requires Finance and Legal approval."
- spend > £100,000 → "Note: strategic spend — CFO, Finance, and Legal approval required."
- data_access = personal_data → "Note: personal data access triggers a Legal and DPO review under GDPR."
- data_access = confidential → "Note: confidential data access requires an IT Security review."
- geography = EU or Global AND data_access = personal_data → "Note: data leaving the EU — GDPR Article 46 transfer mechanisms apply."
- category = Legal → "Note: Legal services engagements require Legal team review."

INFERENCE RULES — apply silently, never mention them to the user, never show reasoning in your response:
- "laptop", "laptops", "MacBook", "ThinkPad", "hardware purchase", "devices", "computers" → category = Hardware, data_access = internal. Do not ask about either. Do not tell the user you are inferring this.
- "for our London/UK/British team" or company context implies UK → geography = UK.
- "SaaS", "software", "platform", "tool", "app" → category = Software.
- Quantity × unit price mentioned → spend_amount = that total. Do not ask again.
- One-time physical purchases (laptops, equipment) → spend_type = one-time. Do not ask.
- "campaign", "project", "project-based", "one-off", "agency fee", "retainer for a campaign", "single engagement" → spend_type = one-time, contract_duration = N/A. Do not ask about either.
- "from the X department" or "I'm in X" → department = X. Do not ask for department.
- "budget is confirmed", "budget confirmed", "already budgeted" → do not ask about budget or spend type again.
- If geography spans multiple regions (e.g. UK, EU, and APAC) → geography = Global. Do not ask.

Never expose internal field names or technical schema details (e.g. do not say "category", "data_access", "spend_type", "is_new_supplier", "Inferring X as Y"). Speak like a person, not a system — say "I'll note that" or "Got it" rather than revealing what field you are recording.

CONVERSATION RULES:
- On your FIRST response, if the user has not stated their name, always ask "Who's making this request?" (or a natural equivalent) as part of your opening reply — group it with other missing fields. Do not wait until the end to collect this.
- NEVER ask the user to confirm or verify something they have already told you, either directly or by clear implication. If they said "laptops", do not ask "Is this a hardware purchase?" If they said "10 laptops at £1,200 each", do not ask "What is the total spend?"
- Ask at most 3 missing fields at a time, grouped naturally.
- service_description and business_justification are different things: service_description = what the supplier provides; business_justification = why the business needs it. Collect both separately.
- Keep language natural and professional — never robotic or form-like.

COMPLETION:
When ALL required fields are collected, output a concise bullet summary in exactly this format:
* Supplier name: [value]
* Service description: [what the supplier provides in ≤10 words, e.g. "Business laptops for employee use" or "Cloud-based HR and payroll software"]
* Spend amount: £[value] [qualifier e.g. per year]
* Spend type: [value]
* Category: [value]
* Geography: [value]
* Data access: [value]
* Business justification: [capture the actual reason given in the conversation — include what problem it solves, what it replaces, and why, in 1–2 sentences]
* Contract duration: [Under 6 months | 6–12 months | 1–2 years | Ongoing | N/A for one-time]
* Requester name: [value]
* Department: [value]

Each bullet must be a single line. Do NOT include reasoning, explanations, or commentary inside any bullet value.

Immediately after the summary (on the next line), output exactly: VIEW_OPTIONS_READY
Do NOT ask for confirmation before this. Do NOT submit anything yourself."""

# ── Session management ────────────────────────────────────────────────────────

def _get_messages(session_id: str) -> list:
    """Return message list for session, creating it if new. Evicts expired sessions."""
    now = datetime.now(timezone.utc)
    stale = [
        sid for sid, s in _sessions.items()
        if now - s["last_active"] > timedelta(minutes=SESSION_TTL_MINUTES)
    ]
    for sid in stale:
        _sessions.pop(sid, None)

    if session_id not in _sessions:
        _sessions[session_id] = {
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
            "last_active": now,
        }
    _sessions[session_id]["last_active"] = now
    return _sessions[session_id]["messages"]


def _clear_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


# ── DB persistence ────────────────────────────────────────────────────────────

def _save_request(data: dict, session_id: str, db: Session) -> ProcurementRequestORM:
    """Score risk, apply policy, write request row + initial audit log entry."""
    spend = data["spend_amount"]
    if not isinstance(spend, (int, float)) or spend <= 0:
        raise ValueError(f"spend_amount must be a positive number, got: {spend!r}")

    is_new = data.get("is_new_supplier", True)

    risk_score, risk_label = score_supplier(
        spend_amount=spend,
        category=data["category"],
        data_access=data["data_access"],
        is_new_supplier=is_new,
        geography=data.get("geography", "UK"),
    )

    policy = evaluate(
        spend_amount=spend,
        category=data["category"],
        data_access=data["data_access"],
        risk_score=risk_score,
        is_new_supplier=is_new,
        geography=data.get("geography", "UK"),
        contract_duration=data.get("contract_duration"),
    )

    req = ProcurementRequestORM(
        session_id=session_id,
        supplier_name=data["supplier_name"],
        supplier_website=data.get("supplier_website"),
        is_new_supplier=is_new,
        spend_amount=spend,
        spend_type=data["spend_type"],
        category=data["category"],
        data_access=data["data_access"],
        business_justification=data["business_justification"],
        service_description=data.get("service_description"),
        geography=data.get("geography", "UK"),
        contract_duration=data.get("contract_duration"),
        security_certifications=data.get("security_certifications"),
        requester_name=data["requester_name"],
        department=data["department"],
        contract_expiry_date=data.get("contract_expiry_date"),
        risk_score=risk_score,
        risk_label=risk_label,
        required_approvers=policy.required_approvers,
        policy_flags=policy.flags,
        questionnaire_depth=policy.questionnaire_depth,
        status="pending",
    )
    db.add(req)
    db.flush()  # gets req.id without committing

    audit = AuditLogORM(
        request_id=req.id,
        action="created",
        actor=data["requester_name"],
    )
    db.add(audit)
    db.commit()
    db.refresh(req)
    return req


# ── Main chat function ────────────────────────────────────────────────────────

def chat(
    session_id: str,
    user_message: str,
    db: Session,
) -> tuple[Optional[str], bool, Optional[str]]:
    """Process one user turn. Returns (reply_text, is_complete, request_id)."""
    client = get_client()
    messages = _get_messages(session_id)
    messages.append({"role": "user", "content": user_message})

    reply_text: Optional[str] = None
    is_complete = False
    request_id: Optional[str] = None

    try:
        response = call_with_retry(
            client,
            model=MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=1024,
        )
    except Exception:
        _clear_session(session_id)
        return "I'm sorry, I encountered an issue. Please try again.", False, None

    choice = response.choices[0]
    reply_text = choice.message.content or ""

    # Detect the readiness signal — agent has all fields and user confirmed
    is_complete = "VIEW_OPTIONS_READY" in reply_text
    # Strip the marker from the displayed reply
    reply_text = reply_text.replace("VIEW_OPTIONS_READY", "").strip()

    if reply_text and session_id in _sessions:
        _sessions[session_id]["messages"].append({"role": "assistant", "content": reply_text})

    return reply_text, is_complete, None
