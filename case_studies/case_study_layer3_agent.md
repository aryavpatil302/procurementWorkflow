# Case Study: Layer 3 — The Intake Agent (Core LLM Loop)

## What We Built

The heart of the system: a stateful, tool-calling LLM conversation loop that turns free-form text into a structured, validated, risk-scored procurement request.

**Files:**
- `backend/services/intake_agent.py` — session store, system prompt, tool definitions, `chat()` function, `_save_request()`
- `backend/services/risk_scorer.py` — inherent risk scoring
- `backend/services/policy_engine.py` — approval routing + questionnaire depth
- `tests/test_intake_agent.py` — 14 behavioral tests, all passing

---

## How It Mirrors Omnea

### The Conversation-First Intake Model

Traditional procurement: an employee fills out a 15-field web form. Half the fields are confusing, so they're filled incorrectly. The form is submitted with `"N/A"` in required fields. The approver sends it back. Three days lost.

Omnea's approach (and ours): the employee types one sentence — "I need to buy Figma for the design team, ~$200/year." The system extracts what it can and asks targeted follow-up questions for the rest. The employee never sees a form. The data is always correct because the agent validates as it goes.

This is exactly what Omnea demoed as their "AI-native intake" — chat interface, progressive field collection, real-time policy surfacing.

### Two-Tool Architecture

We use exactly two tools registered with Groq:

**`extract_state`** — called every turn to let the model track what it knows. Contains all possible fields and a `missing_fields` list. This is the model's working memory — it fills in what it has so far, leaving unknowns null. The function returns `"State recorded."` and the loop continues.

**`submit_request`** — called only when all required fields are confirmed. This is the commit point. Once this tool is called, the request is scored, policy is applied, and the DB row is written.

The separation is deliberate: `extract_state` is ephemeral (tracking only), `submit_request` is permanent (writes to DB). You could add a third tool — `clarify_policy` — that lets the model look up what approvers are required for a given spend amount before telling the user. Omnea likely has something equivalent.

### Real-Time Policy Surfacing

The system prompt instructs the model:
> "If spend_amount > 10000, mention during conversation: 'Just so you know, this will require Finance approval.'"

This is one of Omnea's key differentiators: the requester finds out about approval requirements *during the conversation*, not after submission. Compare to legacy tools where you submit a £50k request, it goes to Finance, and two weeks later you get an email saying it needs CFO sign-off — a surprise that could have been surfaced in 10 seconds.

### Risk Scoring — Inherent Risk Model

`risk_scorer.score_supplier()` computes an inherent risk score from 4 factors:

| Factor | Max contribution |
|---|---|
| Spend amount | 0.40 |
| Data access level | 0.40 |
| Category | 0.12 |
| New supplier | 0.10 |

The "inherent" terminology comes from Omnea's TPRM framework. Inherent risk = risk *before* the supplier provides any certifications or mitigations. After the supplier fills out the questionnaire and provides their ISO 27001 cert, the risk officer would calculate *residual* risk (what remains after controls). Our POC only calculates inherent risk — residual would come in POC 2 (Supplier Portal).

### Policy Engine — Approval Routing

`policy_engine.evaluate()` implements 7 rules evaluated independently (all matching rules fire):

1. Manager always required
2. Spend > £100k → CFO + Finance + Legal
3. Spend > £50k → Finance + Legal
4. Spend > £10k → Finance
5. Personal data → Legal + DPO
6. Confidential data → IT Security
7. High risk score (≥0.65) → IT Security
8. New supplier → IT Security + enhanced due diligence flag
9. Legal category → Legal

The output includes `questionnaire_depth` (basic/standard/deep_due_diligence), which will drive the Supplier Portal in POC 2.

This directly mirrors Omnea's Workflow Builder rules engine — a condition/action system where each rule is an if-then pair, all rules are evaluated, and the results are merged.

### Session Store — TTL-Aware In-Memory Dict

`_sessions` is a `dict[str, dict]` where each value holds:
- `messages`: the full conversation history as plain dicts
- `last_active`: UTC timestamp of last activity

Every access to `_get_messages()` runs lazy cleanup — checking all sessions for expiry before returning the requested one. This avoids needing a background cleanup thread.

The TTL is 60 minutes — matching a typical browser session. An abandoned conversation (user opened the intake form, started typing, then got pulled into a meeting) is cleaned up automatically.

---

## Benefits

**Progressive disclosure.** The agent asks one question at a time. A user who knows everything can type it all in one message and skip straight to submission. A user who's unsure can be walked through step by step. The same code handles both.

**Validation at extraction time, not submission time.** Traditional forms validate on submit — the user fills everything out, clicks "Submit", and gets a wall of error messages. The agent validates during conversation: "You mentioned £200/year — should that be categorized as a subscription?" The correction happens naturally.

**Risk score on every request.** Every submitted request has a `risk_score` and `risk_label` computed deterministically from the policy rules. Approvers see "Risk: High (0.72)" immediately. They don't have to read the request to know how much scrutiny to apply.

**Audit trail from day one.** The `AuditLogORM` entry is written at the same time as the request row, in the same DB transaction. There's no window where a request exists without an audit trail.

---

## Potential Pitfalls

**The model can decide to submit prematurely.** The system prompt says "Only call submit_request when ALL required fields are confirmed." But Llama 3.3-70b sometimes calls `submit_request` with missing fields anyway — especially if the user provides a long first message that the model misinterprets as complete. Fix: validate required fields in the `submit_request` handler before writing to the DB, and return a tool error if fields are missing.

**The model can loop on `extract_state` forever.** If the model repeatedly calls `extract_state` without ever producing a text reply, the while-True loop would run indefinitely. Fix: add a `max_iterations` counter (e.g., 10) and break with an error after that threshold.

**In-memory sessions don't survive restarts.** If Uvicorn restarts (deployment, crash), all active conversations are lost. The user gets back "I'm sorry, I don't have context for that conversation — could you start over?" Production fix: serialize sessions to Redis with `SETEX key 3600 value`.

**One session per conversation — no multi-user isolation.** The session_id is the only isolation mechanism. If two users accidentally share a session_id, their conversations interleave. In production, session_ids must be generated server-side (UUIDs) and associated with authenticated user accounts.

**The `_save_request` and session clear aren't in a DB transaction.** If `_save_request` succeeds but the follow-up `call_with_retry` for the closing message fails, the session is not cleared (the `_clear_session` call is inside the `if is_complete:` block, which is inside `while True:`). The request is saved but the session lives on. On the next message, the model will see the `submit_request` tool result in history and behave unpredictably. Fix: clear the session immediately after `_save_request` succeeds, before calling for the closing message.

---

## Areas for Improvement

1. **Required field validation in submit_request handler** — raise a tool error if any required field is null, preventing premature submission
2. **Max iterations guard** — break after 10 loop iterations to prevent infinite tool-call loops
3. **Redis session store** — `SETEX` with TTL for production durability
4. **Async LLM calls** — `httpx.AsyncClient` + `asyncio.gather` for parallel tool processing
5. **Duplicate supplier detection via DB query** — the system prompt mentions it but the handler doesn't implement a real similarity search; add a fuzzy match against `supplier_name` column using SQLite's LIKE or Postgres's `pg_trgm`
6. **Residual risk calculation** — add supplier questionnaire response scoring to reduce risk score after certifications are provided

---

## Logic Flow

```
User: "I need to buy Figma for the design team, around $200/year"

chat("sess-001", "I need to buy Figma...", db)
  │
  ├── _get_messages("sess-001") → creates new session with system prompt
  ├── Append user message
  │
  └── Loop iteration 1:
        call_with_retry(model, messages, tools=INTAKE_TOOLS)
          → Model calls extract_state({
              supplier_name: "Figma",
              spend_amount: 200,
              spend_type: "subscription",
              missing_fields: ["category", "data_access", "business_justification", ...]
            })
        Append assistant_message_dict(choice) to messages
        Append tool result "State recorded." to messages
        Continue loop

  └── Loop iteration 2:
        call_with_retry(model, messages, tools=INTAKE_TOOLS)
          → Model produces text: "Got it! Figma for $200/year sounds like a subscription.
             What department is this for, and what's the main business reason?"
        Append assistant_message_dict(choice) to messages
        break

  └── Store reply in _sessions["sess-001"]["messages"]
  └── Return ("Got it! Figma...", False, None)

[Several more turns...]

User: "Yes, that summary looks correct — please submit it."

  └── Loop:
        Model calls submit_request({all fields confirmed})
        normalize_spend_type("subscription") → "subscription"
        normalize_category("Software") → "Software"
        normalize_data_access("internal") → "internal"
        _save_request({...}, "sess-001", db)
          → score_supplier(200, "Software", "internal", is_new=True) → (0.35, "medium")
          → evaluate(200, "Software", "internal", 0.35, True) → PolicyResult(
               required_approvers=["it_security", "manager"],
               flags=["New supplier — enhanced due diligence required."],
               questionnaire_depth="basic"
             )
          → ProcurementRequestORM(supplier_name="Figma", ..., risk_score=0.35, ...) → DB
          → AuditLogORM(action="created", actor="Alice") → DB
          → commit
        Append tool result "Request submitted. ID: abc-123" to messages
        set is_complete = True, break

  └── call_with_retry for closing message → "Your request has been submitted! ..."
  └── _clear_session("sess-001")
  └── Return ("Your request has been submitted!", True, "abc-123")
```

---

## Code Flow

`intake_agent.py` is organized into four sections:

1. **Imports and constants** — `SESSION_TTL_MINUTES`, `SYSTEM_PROMPT`, `INTAKE_TOOLS` (the two tool JSON schema dicts)

2. **Session management** — `_sessions` dict, `_get_messages()` (creates/fetches/cleans sessions), `_clear_session()`

3. **`_save_request(data, session_id, db)`** — orchestrates `score_supplier()`, `evaluate()`, and the DB write. Returns the ORM object (so the caller has the `id`).

4. **`chat(session_id, user_message, db)`** — the main public function. Receives the DB session from FastAPI's DI (`Depends(get_db)` in the router). Runs the while-True loop. The loop terminates on two conditions: `finish_reason == "stop"` (no tool call → text reply) or `submit_request` tool call (`is_complete = True`).

The `assistant_message_dict(choice)` call happens immediately after every Groq response. This is the fix for the Pydantic serialization bug — the message is converted to a plain dict before it can cause problems downstream.
