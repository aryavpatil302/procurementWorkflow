# Code Review: Procurement Intake Agent

**Reviewer:** Marcus Venn | **Date:** 2026-05-05

---

## 1. Overall Assessment

Competently structured POC. Clean layering, correct SQLAlchemy patterns, real understanding of Groq's tool-calling API. The bones are good. The in-process session store is a foundational choice that must be ripped out before real traffic. There are correctness bugs in the tool-call loop and the risk scorer. Security posture is essentially nonexistent — which matters more than usual for a system making approval routing decisions.

---

## 2. What's Well Done

1. **`_normalizers.py` normalizer pattern** — `.lower().strip()` preprocessing before every lookup, safe fallback on unknown values. Shows real operational awareness of LLM enum drift.

2. **`assistant_message_dict` in `_groq_utils.py`** — Solves the Pydantic serialization failure correctly. The docstring explains why. Technical precision in the `tool_calls` structure.

3. **`_SPEND_SCORE` table in `risk_scorer.py`** — Tiered scoring as `(threshold, points)` list is clean and extendable.

4. **`db.flush()` before `db.commit()` in `_save_request`** — Gets `req.id` for the FK without committing, so audit log and request row commit atomically. Correct SQLAlchemy pattern.

5. **Policy engine uses `set` for approvers, `sorted()` on output** — Prevents duplicates, makes output deterministic.

6. **`StaticPool` + `dependency_overrides` in test infrastructure** — Correct FastAPI testing pattern.

---

## 3. Bugs and Correctness Issues

**Bug 1 — CRITICAL: No loop iteration guard in `intake_agent.py` `while True`.**
A misbehaving model (infinite `extract_state` loop, or retry exhaustion) spins forever or propagates an unhandled exception with dirty session state. Fix: `for iteration in range(MAX_ITERATIONS)` with `_clear_session` in the else branch.

**Bug 2 — HIGH: `risk_scorer.py` does not validate `spend_amount >= 0`.**
A negative spend (possible from bad LLM extraction) matches no threshold and scores only category + new supplier. Fix: `if spend_amount < 0: raise ValueError(...)` at top of `score_supplier`.

**Bug 3 — HIGH: Closing reply after `submit_request` is never appended to messages.**
`intake_agent.py` lines 267–284: the closing Groq call result is read but not appended. Latent reliability issue if post-submission processing re-uses message history.

**Bug 4 — MEDIUM: `_serialize` omits `company_id`.**
`routers/requests.py` lines 83–107: `company_id` exists on the ORM but never included in the serialized response.

**Bug 5 — MEDIUM: `get_db` does not rollback on exception.**
`database.py` lines 27–32: `finally: db.close()` without a rollback. Fix: add `except Exception: db.rollback(); raise`.

**Bug 6 — MEDIUM: `test_rate_limit_retry` is a false green test.**
`test_intake_agent.py` lines 284–294: patches `call_with_retry` itself — the retry logic is never exercised. Should patch `groq.Groq` and raise `RateLimitError` on first two calls.

---

## 4. Design and Architecture Issues

- **In-process `_sessions` dict** — Breaks with 2+ workers. O(n) TTL scan per request. Fix: Redis with SETEX.
- **No input validation on `ChatRequest.message`** — Unbounded length, no prompt injection mitigation. Add `Field(max_length=2000)`.
- **Status transitions unconstrained** — `approved → pending` is allowed. Implement transition matrix.
- **Hardcoded currency `£` in policy_engine.py** — Should be a config constant.
- **`company_id` exists but no query filter** — Either implement tenant isolation or remove the field to avoid false confidence.

---

## 5. Testing Quality Assessment

- **`test_models.py`** — Solid. Missing: cascade delete test.
- **`test_normalizers.py`** — Very good. Missing: `normalize_spend_type(None)` raises AttributeError; leading/trailing whitespace test for multi-word keys.
- **`test_intake_agent.py`** — Mostly good. One false test (`test_rate_limit_retry`). Missing: `submit_request` with missing required field; `json.JSONDecodeError` from malformed tool call.
- **`test_api.py`** — Good endpoint coverage. `test_status_change_writes_audit_log` doesn't actually verify the audit log — just checks the status changed.

---

## 6. Security Issues

1. **No auth on any endpoint** — Any caller can read all requests and approve anything.
2. **`actor` is a free-form unverified string** — Audit trail is trivially falsifiable.
3. **CORS `allow_origins` hardcoded** — Should come from env var for deployment.
4. **No rate limiting on `/chat`** — One caller can exhaust Groq API quota.
5. **`GROQ_API_KEY=None` fails with obscure error** — Should raise `EnvironmentError` at startup.
6. **All spend data returned unredacted without auth** — Commercially sensitive.

---

## 7. Top 5 Priority Fixes

1. **Loop iteration guard** — `while True` → `for iteration in range(MAX_ITERATIONS)`. 5 lines. Prevents runaway API spend.
2. **Validate `spend_amount > 0`** — In `_save_request` and tool schema. LLM will extract nonsense.
3. **Basic auth on status update** — X-API-Key header. Even a demo needs this to be credible.
4. **`get_db` rollback** — One-liner. Prevents silent data corruption.
5. **Fix `test_rate_limit_retry`** — A false green test is worse than no test.
