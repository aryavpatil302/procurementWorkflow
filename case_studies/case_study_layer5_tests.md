# Case Study: Layer 5 — Full Test Suite

## What We Built

A complete, deterministic test suite covering all four layers — 94 tests, all passing in under 1 second. No API keys, no network calls, no external dependencies. The suite runs in CI, on a laptop with no internet, or in a Docker container with no environment variables set.

**Test files:**
- `tests/conftest.py` — shared `db` fixture (in-memory SQLite, `StaticPool`, function-scoped)
- `tests/test_models.py` — 9 ORM round-trip tests
- `tests/test_normalizers.py` — 59 parametrized normalizer tests
- `tests/test_intake_agent.py` — 14 behavioral agent tests (mocked Groq)
- `tests/test_api.py` — 12 end-to-end HTTP tests (mocked Groq, TestClient)

---

## How It Mirrors Omnea

Omnea is a compliance-critical system. Procurement decisions made on bad data — a wrong risk score, a missed approver, an unenforced policy rule — can result in legal exposure, security breaches, or regulatory penalties. This isn't a product where "it mostly works" is acceptable.

A mature procurement platform at Omnea's scale would have:

| Test category | Our coverage | Omnea equivalent |
|---|---|---|
| ORM round-trips | ✅ All fields, all defaults | Schema regression tests |
| Enum normalization | ✅ 59 cases, all variants | LLM output sanitization tests |
| Agent behavioral rules | ✅ Session TTL, submit logic, audit | Conversation flow tests |
| API contract | ✅ All endpoints, all error states | Integration/contract tests |
| Risk scoring | ✅ Via agent test (`_save_request`) | Policy calculation tests |
| Policy engine | ✅ Via `test_save_request_high_spend_deep_diligence` | Approval routing tests |

What we don't have (yet):
- **Load tests** — what happens at 100 concurrent intake sessions?
- **Fuzz tests** — what happens when the user sends SQL injection attempts, emoji, 50KB messages?
- **Property-based tests** — does `score_supplier()` always return a value between 0.0 and 1.0, for any input?
- **Snapshot tests** — does the system prompt still produce the expected behavior after an edit?

---

## The `conftest.py` Architecture Decision: `StaticPool`

This is the most non-obvious technical decision in the test suite, and it deserves a full explanation.

SQLite has a quirk that bites almost every developer who tries to use in-memory databases for testing: **each connection to `sqlite:///:memory:` creates a completely separate, empty database.** SQLAlchemy's default connection pool for `sqlite:///:memory:` is `NullPool` — which creates a new connection for every database operation. The result:

1. `Base.metadata.create_all(engine)` → creates tables in connection #1
2. Test runs, FastAPI endpoint calls `db.query(...)` → opens connection #2 → **empty database**, no tables
3. `OperationalError: no such table: procurement_requests`

The fix is `StaticPool` — it forces the engine to reuse exactly one connection for all operations, so all code sees the same in-memory database state.

```python
from sqlalchemy.pool import StaticPool
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
```

This is not obvious from the SQLAlchemy docs and it's not mentioned in most FastAPI testing tutorials. It's a known footgun. We hit it, diagnosed it, and fixed it — and now the fix is documented here so anyone building on top of this codebase doesn't lose hours to the same problem.

---

## Test Design Principles

### 1. One Behavior Per Test

Every test asserts exactly one behavioral requirement. `test_session_cleared_after_submit` only checks that `_sessions` doesn't contain the session_id after submission. `test_session_not_recreated_after_submit` is a separate test that checks the session isn't re-added by the reply storage code. Two separate bugs, two separate tests.

This makes test failures self-documenting. When `test_session_not_recreated_after_submit` fails in CI, you know exactly what broke without reading the assertion.

### 2. Parametrize for Exhaustive Coverage

`test_normalizers.py` uses `@pytest.mark.parametrize` to cover every variant in the lookup tables:

```python
@pytest.mark.parametrize("raw,expected", [
    ("annual", "recurring"),
    ("Annual", "recurring"),
    ("ANNUAL", "recurring"),
    ...
])
def test_normalize_spend_type_known(raw, expected):
    assert normalize_spend_type(raw) == expected
```

This gives 59 test cases from ~20 lines of test code. Each variant is a separate test run — so if `"ANNUAL"` starts failing (case-sensitivity bug) but `"annual"` still passes, pytest reports exactly which case broke.

### 3. Mock at the Right Layer

Groq is mocked at `backend.services.intake_agent.call_with_retry` — not at the `groq.Groq` class level. This means:
- We test our calling code (correct arguments, correct retry behavior)
- We don't test Groq's SDK internals
- The mock is easy to set up and understand

If we mocked at the `groq.Groq` class level, we'd be coupled to the SDK's internal structure. If Groq changes their SDK, our mocks break even though our code is fine.

### 4. Test the Negative Path

Every endpoint test has a "not found" variant:
- `test_get_request_not_found` → 404 on bad ID
- `test_update_status_not_found` → 404 on bad ID
- `test_update_status_invalid` → 422 on invalid status value

These are as important as the happy path. A system that returns 500 instead of 404 is telling clients "server error" when the real answer is "wrong ID." This breaks client retry logic and makes debugging harder.

### 5. DB Isolation Per Test

The `db` fixture in `conftest.py` is `scope="function"` — each test gets a fresh, empty database. This means:
- Tests are independent (no ordering dependencies)
- A test that writes data can't corrupt a subsequent test
- Tests can be run in any order, in parallel, or individually

---

## Benefits

**Fast feedback loop.** 94 tests in 0.81 seconds. A developer making a change can run the full suite in under 2 seconds and know immediately if something broke. Compare to Selenium/Playwright UI tests that take 5-30 minutes.

**Zero external dependencies.** No Groq API key, no internet, no running server. Tests work in a fresh checkout with just `pip install -r requirements.txt && pytest`. This is the baseline for "the suite works in CI."

**Behavioral tests as documentation.** Reading `test_intake_agent.py` is faster than reading the implementation. `test_enum_normalization_on_submit` tells you: "when the LLM returns 'annual', it should be stored as 'recurring' in the DB." This is living documentation that breaks if someone removes the normalization step.

**Regression safety.** The `StaticPool` fix, the session-recreation bug, the ForeignKey constraint requirement, the Pydantic serialization crash — all of these are now tested. If a future refactor re-introduces any of them, the tests will catch it.

---

## Potential Pitfalls

**The tests don't test the real Groq API.** Mocked Groq returns exactly what we tell it to. The real model is nondeterministic — it might call `submit_request` with a missing field, or loop on `extract_state` forever, or return `"Annual"` when we specified `"recurring"` in the schema. These failure modes require manual testing or an integration test with a real API key.

**`StaticPool` is not thread-safe.** `StaticPool` uses one shared connection. If multiple threads try to use the same engine concurrently, they'll share that one connection — potentially causing transaction collisions. This is fine in tests (pytest runs tests sequentially by default). Never use `StaticPool` in production.

**Test coverage doesn't include risk_scorer or policy_engine unit tests.** We test them indirectly via `_save_request` in `test_intake_agent.py`, but there are no dedicated tests like `test_score_supplier_high_spend_personal_data()`. The consequence: if the risk scorer has a bug in a specific combination of inputs, we might not catch it until that combination appears in an integration test.

**The mock doesn't simulate the real LLM loop.** Our mocks return a single tool call or a single text response. The real model might call `extract_state` 3 times before producing a text reply. We test that `extract_state` continues the loop (one mock iteration), but we don't test a 5-turn extract cycle. This could hide bugs in message history accumulation.

---

## Areas for Improvement

1. **Unit tests for risk_scorer** — `test_risk_scorer.py` with parametrized spend/category/data combinations
2. **Unit tests for policy_engine** — `test_policy_engine.py` verifying each of the 7 rules fires correctly
3. **Property-based tests** — `hypothesis` library: "for any valid spend_amount and category, risk_score is always in [0.0, 1.0]"
4. **Integration test with real Groq key** — marked `@pytest.mark.integration`, skipped unless `GROQ_API_KEY` is set
5. **Multi-iteration loop test** — mock returning `extract_state` → `extract_state` → text reply to test message accumulation across 3 loops
6. **Fuzz test on normalizers** — feed 10,000 random strings to each normalizer, assert no crashes and all outputs are valid canonical values
7. **Coverage report** — `pytest --cov=backend --cov-report=html` to identify untested branches
8. **Parallel test execution** — `pytest-xdist` for parallel test runs once `StaticPool` is replaced with per-thread connections in a thread-safe fixture

---

## Logic Flow

```
pytest tests/

  conftest.py loaded
    └── db fixture defined (StaticPool, in-memory SQLite, function-scoped)

  test_models.py (9 tests)
    └── Each test: db fixture creates fresh DB, test adds/reads ORM rows, fixture closes and drops

  test_normalizers.py (59 tests)
    └── Pure function calls, no fixtures needed
    └── pytest parametrize generates 59 test cases from 4 parametrize decorators

  test_intake_agent.py (14 tests)
    └── Each test: db fixture + _sessions.clear() + patch("call_with_retry")
    └── mock side_effect[] controls what Groq "returns" turn by turn
    └── Assertions on _sessions state, DB state, return values

  test_api.py (12 tests)
    └── Each test: client fixture creates in-memory DB, overrides get_db, clears _sessions
    └── with TestClient(app) as c: triggers startup → init_db() on global engine (creates procurement.db file)
    └── But DB sessions go to in-memory engine via override
    └── HTTP calls → router → intake_agent → mocked Groq → DB → response
```

---

## Code Flow

`conftest.py`:
- `engine = create_engine("sqlite:///:memory:", ..., poolclass=StaticPool)` — one shared in-memory DB per test
- `import backend.models` — registers ORM classes with `Base.metadata` (side effect)
- `Base.metadata.create_all(engine)` — creates tables in the test DB
- `Session = sessionmaker(bind=engine)` — session factory for this test's engine
- `yield session` — test runs with this session
- `Base.metadata.drop_all(engine)` — clean up (not strictly necessary for in-memory, but good practice)

`test_api.py`:
- `app.dependency_overrides[get_db] = override_get_db` — replaces FastAPI's real `get_db` with the test version
- `with TestClient(app) as c:` — starts the app (triggers startup event), yields the HTTP client
- After `yield c`, cleanup: `app.dependency_overrides.clear()`, `Base.metadata.drop_all(engine)`
- The `_make_text_response()` and `_make_submit_response()` helpers build `MagicMock` objects that look exactly like Groq SDK responses — `response.choices[0].message.content`, `response.choices[0].message.tool_calls`, etc.

`test_intake_agent.py`:
- `patch("backend.services.intake_agent.call_with_retry")` — patches at the module level where it's used, not where it's defined. This is the correct way to mock in Python — mock where it's imported, not where it's defined.
- `mock_call.side_effect = [response1, response2]` — `side_effect` as a list means the first call returns `response1`, the second returns `response2`. This lets us simulate multi-turn mock conversations.
