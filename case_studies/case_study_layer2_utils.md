# Case Study: Layer 2 — Groq Utils & Enum Normalizers

## What We Built

Two utility modules that handle all the messy edges of working with a real LLM API:

- **`_groq_utils.py`**: Groq client singleton, `call_with_retry()` with exponential backoff, and `assistant_message_dict()` to safely convert Pydantic responses to plain dicts
- **`_normalizers.py`**: Four lookup-table functions that correct Llama 3.3-70b's creative enum values back to canonical form before any DB write

**59 tests, all passing.** Tests are purely deterministic — no API calls, no randomness.

---

## How It Mirrors Omnea

### The Pydantic Serialization Problem

Omnea's intake system processes tool-call responses from its LLM provider. Every LLM SDK wraps responses in SDK-specific objects. The moment you try to append one of these objects directly to a message list and send it back in the next API call, you get a JSON serialization error.

`assistant_message_dict()` solves exactly this. It extracts the fields the API expects (`role`, `content`, `tool_calls`) and returns a plain Python dict — something every JSON encoder can handle. This is a pattern you'll see in every mature LLM integration codebase.

### Exponential Backoff (Rate Limiting)

Groq's free tier is 30 requests/minute. A multi-turn conversation can burn through 10-15 calls in a minute (each `extract_state` tool call counts). Without backoff, users in the middle of a conversation would hit a hard `RateLimitError` and lose their progress.

`call_with_retry()` does 1s → 2s → 4s waits before giving up. This mirrors what Omnea almost certainly does internally — their system has thousands of concurrent intake conversations. They would hit provider rate limits constantly without retries and backoff.

### Enum Normalization — Why Llama Can't Follow Instructions

The `_normalizers.py` module exists because of a fundamental tension in prompt engineering: you can tell the model to return `"one-time"`, but you can't force it. Llama 3.3-70b regularly returns values like:

| Model returned | We wanted |
|---|---|
| `"annual"` | `"recurring"` |
| `"SaaS"` | `"subscription"` |
| `"customer_data"` | `"personal_data"` |
| `"moderate"` | `"medium"` |

Without normalization, these flow directly into the database. Your policy engine then checks `if data_access == "personal_data"` — and silently skips the personal data rule because the stored value is `"customer_data"`. A legal review that should have been triggered isn't. This is a compliance failure, not just a bug.

Omnea's engineering team would solve this with either:
1. Post-extraction normalization (what we do)
2. Schema validation at the DB layer with strict enum constraints
3. Both

We chose option 1 — keeping the normalization at the service layer where it's testable without touching the DB.

---

## Benefits

**Isolated and independently testable.** All 59 normalizer tests run in 0.04 seconds and require no API keys, no DB, no network. You can run them in a pre-commit hook to catch regressions immediately.

**Explicit fallback behavior.** Every normalizer has a documented safe default: unknown spend types fall back to `"one-time"`, unknown categories to `"Other"`, unknown data access to `"none"`. This means a bad LLM response causes a soft error (a request categorized as "Other") rather than a hard crash or a constraint violation.

**Singleton client.** `get_client()` returns the same `groq.Groq()` instance for the lifetime of the process. HTTP connection pooling works correctly — we reuse the same connection rather than establishing a new TLS handshake for every request.

**Case-insensitive matching.** All lookup tables normalize via `.lower().strip()` before lookup. `"SaaS"`, `"SAAS"`, `"saas"` all map correctly.

---

## Potential Pitfalls

**Lookup table maintenance burden.** When Omnea adds a new category or data access level, someone has to remember to update the lookup table. If you add `"Infrastructure"` to the category enum and forget to add it to `_CATEGORY_MAP`, all infrastructure requests will be silently filed as `"Other"`. Fix: add a test that asserts the lookup table covers all canonical values.

**"Good enough" matching may hide model degradation.** If Llama starts returning `"perpetual_license"` for a subscription purchase, `normalize_spend_type` maps it to `"one-time"` (the fallback). The data in the DB is wrong, but no error is raised. You'd only notice during a quarterly audit. Fix: log a warning when the fallback is triggered — then you can alert on unexpected normalization rates.

**Exponential backoff is synchronous.** `time.sleep()` blocks the FastAPI worker thread during the retry delay. For a 4-second wait on the third retry, that's 4 seconds a thread is held idle. Fix: use `asyncio.sleep()` with an `async def` version of `call_with_retry` — but this requires making the entire chat function async, which is a larger refactor.

**No circuit breaker.** If Groq's API is down for 10 minutes, every request will retry 3 times (1s + 2s + 4s = 7 seconds per request) before failing. With many concurrent users, this creates a thundering herd when the API recovers. Fix: a circuit breaker (e.g., `pybreaker`) that opens after N consecutive failures and rejects requests immediately until the API recovers.

---

## Areas for Improvement

1. **Add telemetry on normalizer fallbacks** — `logger.warning(f"Unknown {field}: {raw!r}, falling back to {default!r}")` allows monitoring model drift
2. **Make `call_with_retry` async** — `asyncio.sleep()` instead of `time.sleep()` to not block the event loop
3. **Add a circuit breaker** around Groq API calls
4. **Expand lookup tables based on real model output** — run the agent on 100 test inputs, collect all raw values, add any missing mappings
5. **Validate canonical values explicitly** — assert that `normalize_category("Software") == "Software"` so any typo in the table is caught

---

## Logic Flow

```
User sends a message → intake_agent.chat() calls call_with_retry()
  ├── Success → returns Groq ChatCompletion object
  │     └── assistant_message_dict(choice) converts to plain dict
  │           └── appended to messages list (safe for next JSON serialization)
  └── RateLimitError
        └── wait 1s, retry
              └── RateLimitError again
                    └── wait 2s, retry
                          └── Success OR raises RateLimitError after 3 attempts

submit_request tool called with raw LLM args
  └── normalize_spend_type("annual") → "recurring"
  └── normalize_category("saas") → "Software"
  └── normalize_data_access("customer_data") → "personal_data"
  └── normalized args passed to _save_request()
        └── DB write with clean, canonical values
```

---

## Code Flow

`_groq_utils.py`:
- `get_client()` — module-level `_client` variable acts as a singleton. First call creates the Groq instance; subsequent calls return the cached one.
- `call_with_retry(client, max_retries=3, **kwargs)` — passes all kwargs to `client.chat.completions.create()`. The `**kwargs` pattern means any Groq API parameter (model, messages, tools, temperature, etc.) can be passed without the retry wrapper knowing about them.
- `assistant_message_dict(choice)` — reads `choice.message.content` (the text) and `choice.message.tool_calls` (list of tool invocations). Converts each `tool_call` to the dict format the Groq API expects: `{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}`.

`_normalizers.py`:
- Four module-level dicts (`_SPEND_TYPE_MAP`, `_CATEGORY_MAP`, `_DATA_ACCESS_MAP`, `_RISK_LABEL_MAP`) — the actual lookup tables.
- Four public functions, each doing the same thing: `raw.lower().strip()` → lookup → return canonical value or default.
- The `.lower().strip()` preprocessing is why `"SaaS"`, `"  SaaS  "`, and `"saas"` all match.
