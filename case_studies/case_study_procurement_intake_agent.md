# Case Study: Procurement Intake Agent
### Building a POC that mirrors Omnea's AI-native intake system

---

## 1. What Omnea's Intake Agent Does — and Why It Exists

### The problem it solves

Traditional procurement intake is a form-filling exercise bolted onto an ERP or a spreadsheet. An employee who needs to buy a £40,000 security audit tool fills in a static form, attaches a PDF, emails a procurement manager, and waits. The form doesn't know whether the supplier already exists in the approved vendor catalog, doesn't surface policy obligations in real time, doesn't know the risk profile of what's being requested, and gives the requester no feedback until days later when someone reads it.

The result is predictable: requests get submitted incomplete, finance teams chase people for information, legal reviews are triggered too late, and approved-but-duplicated vendor contracts quietly multiply.

Omnea's intake agent replaces that static form with a conversational AI-native intake flow. Instead of asking a requester to know in advance what fields a form needs, the agent asks them, surfaces relevant context as information is collected (policy flags, existing contracts, risk signals), and routes the completed request to the right approvers — all without a human manually triaging it first.

### Where it sits in the Omnea platform

Omnea is a third-party risk management (TPRM) and procurement orchestration platform. The intake agent is the first node in a larger workflow:

```
Requester → [Intake Agent] → Risk Assessment → Approval Routing
                ↓
         Supplier Portal (questionnaire)
                ↓
         Contract + Renewal Management
                ↓
         Ongoing Vendor Monitoring
```

The agent's job is specifically to collect a complete, policy-aware request and hand it off with a calculated risk tier and the correct downstream questionnaire depth. Everything downstream depends on the quality of what it captures.

### Gaps it fills in the market

Most procurement tools (Coupa, SAP Ariba, Zip) are built around structured workflows: forms, approval chains, and contract repositories. They are strong at process enforcement but weak at intelligence at the point of capture. Their intake layers are still fundamentally form-based.

What Omnea does differently:

| Capability | Legacy tools | Omnea |
|---|---|---|
| Intake method | Static form | Conversational AI |
| Duplicate vendor detection | Manual lookup | Real-time during intake |
| Policy surfacing | Post-submission review | Live during conversation |
| Risk tier assignment | Manual or rule-based post-submission | Computed at submission, drives questionnaire depth |
| Questionnaire scoping | Fixed template | Adaptive — depth scales with risk |
| Supplier portal | Separate system | Native, token-gated |

The key competitive differentiator is that intelligence is front-loaded. By the time a request lands in an approver's queue it already has a risk label, a policy flag summary, the correct questionnaire assigned, and a clean data structure — rather than a free-text email or a half-filled form that someone has to interpret.

---

## 2. The POC: What We Built and How It Maps

### Architecture overview

The POC is a Python/FastAPI backend with a JavaScript single-page frontend. It is deliberately thin — no Kubernetes, no message queues, no auth service — because the goal was to mirror the *intelligence layer* of Omnea's intake, not replicate its infrastructure.

```
┌──────────────────────────────────────────────────────┐
│  Frontend (Vanilla JS)                               │
│  chat.html — conversational UI                       │
│  dashboard.html — approval queue + questionnaire     │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP (fetch)
┌────────────────────▼─────────────────────────────────┐
│  FastAPI Backend                                     │
│                                                      │
│  POST /chat           → intake_agent.py              │
│  GET  /requests       → requests router              │
│  GET  /requests/{id}  → requests router              │
│  PATCH /requests/{id}/status → requests router       │
│  GET  /requests/{id}/questionnaire → questionnaire   │
└──────────┬───────────────────┬───────────────────────┘
           │                   │
┌──────────▼──────┐   ┌────────▼──────────────────────┐
│  Groq API       │   │  SQLite (SQLAlchemy 2.0)       │
│  Llama 3.3 70b  │   │  ProcurementRequestORM         │
│  tool-calling   │   │  AuditLogORM                   │
└─────────────────┘   └───────────────────────────────┘
```

### How the agent is configured

The LLM is given a structured system prompt and one tool — `submit_request` — with a strict JSON Schema. The model is never given free-form write access to the database; it can only call `submit_request` when it judges all required fields are collected, and only after the user confirms a summary.

The tool schema defines exactly what must be present before submission:

```python
"required": [
    "supplier_name", "spend_amount", "spend_type", "category",
    "data_access", "business_justification", "requester_name", "department"
]
```

Optional fields (`supplier_website`, `cost_center`, `contract_expiry_date`, `is_new_supplier`) are nullable in the schema — a deliberate decision after discovering that Groq's strict parameter validation rejects `null` against `{"type": "string"}`. The schema must explicitly declare `{"type": ["string", "null"]}` for any field the model might omit.

State across turns is maintained server-side in a TTL-aware in-memory session store (`_sessions: dict[str, dict]`). Sessions expire after 60 minutes via lazy eviction — checked on every `_get_messages()` call, not on a background timer, to avoid threading complexity.

### What the POC hits from Omnea's feature set

**Conversational intake with gap-filling** — the agent reads what's already in the conversation history and asks for missing fields in natural language, batching up to 3 questions per message. This is the core behaviour.

**Trusted supplier catalog check** — before collecting details, the agent checks the requested supplier against a known list of approved vendors and flags renewals vs. new purchases. Omnea does this by querying an actual contract database; our POC embeds the catalog in the system prompt. Same logical behaviour, different implementation depth.

**Real-time policy surfacing** — the agent proactively flags Finance approval thresholds (>£10k) and Legal/DPO review triggers (personal data access) *during* the conversation, not after submission. This is one of Omnea's key differentiators and we replicate it faithfully.

**Inherent risk scoring** — on submission, `risk_scorer.py` computes a 0–100 score and label from spend, data sensitivity, category, and supplier novelty. The logic mirrors Omnea's inherent risk model (pre-certification, pre-audit).

**Adaptive questionnaire depth** — the risk tier (`basic`/`standard`/`deep_due_diligence`) drives the number of supplier due-diligence questions returned (5/10/15). Omnea's questionnaire depth scales the same way — higher risk means more detailed supplier assessment.

**Status transition enforcement** — requests move through a valid state machine (`pending → approved/rejected/cancelled`, `approved → cancelled`). Illegal transitions return 422. Omnea enforces similar approval-state integrity.

**Approval dashboard** — a read-only approval queue with filter by status, detail view, and approve/reject/cancel actions authenticated via API key.

**Audit logging** — every status change writes an `AuditLogORM` entry with actor, old value, new value, and timestamp. This mirrors Omnea's audit trail requirement for compliance.

---

## 3. Learnings from Building It — Relevant to Client Support Engineering

### Where bugs actually live in agentic systems

Building this out exposed exactly the kinds of issues a support engineer would encounter with Omnea customers.

**Schema validation mismatches between model output and API expectations**

The most persistent category of bugs. Groq validates tool call arguments strictly against the JSON Schema. The model would pass `""` for `spend_type` (a field with an enum constraint) when it hadn't collected that value yet, causing a hard 400 rejection:

```
tool call validation failed: parameters for tool extract_state did not match schema:
errors: [/spend_type: value must be one of "one-time", "recurring", "subscription"]
```

The fix was allowing `null` in the schema for fields not yet collected. This is a class of bug that is invisible in testing if you only test the happy path — it only surfaces when the model is mid-conversation with partial state. For a client support engineer, this means that "the agent crashes mid-conversation" reports likely trace back to schema/validation mismatches, not application logic errors.

**The model generating tool calls in a malformed format**

Llama 3.3 occasionally generates `extract_state({"key": "value"})` — embedding arguments inside the function name — instead of the correct format with arguments in a separate field. Groq's API rejects this with a 400 `tool_use_failed`. The model does it more frequently as conversation history grows, because longer context increases the chance of format drift.

The mitigation: catch `BadRequestError` and retry without tools for that turn, letting the model fall back to plain text. Understanding this behaviour is directly applicable to supporting Omnea customers who report "the agent stops responding after a few messages" — it's almost certainly this failure mode.

**SQLAlchemy 2.0 behaviour differences**

SQLAlchemy 2.0 deprecated `.query().get()` in favour of `db.get(Model, id)`, and requires explicit `ForeignKey` declarations for `relationship()` to work — it won't infer them. In our `AuditLogORM`, omitting `ForeignKey("procurement_requests.id")` threw an `InvalidRequestError: Could not determine join condition` at startup, not at query time.

For client support, this is the pattern of "the server starts but certain API calls fail with a 500 and an ORM error" — you need to know to look at the model definitions, not the query logic.

**In-memory SQLite test databases requiring `StaticPool`**

Without `poolclass=StaticPool`, SQLAlchemy opens a new connection for each session. With SQLite in-memory databases, a new connection means a fresh empty database — so `Base.metadata.create_all(engine)` creates tables on connection #1, but the test session opens connection #2 and finds nothing. Tests pass schema setup but throw "no such table" errors on every query.

This mirrors a class of support issue: "tests pass on my machine but fail in CI" — the difference is often connection pooling or database isolation behaviour that only surfaces under specific concurrency or configuration conditions.

**Pydantic serialization of Groq response objects**

Groq returns `ChatCompletionMessage` as a Pydantic model. Appending it directly to the messages list and passing it back to the API works on the first call but fails on the second — the messages list now contains a Pydantic object where the API expects a plain dict, causing a `TypeError` during JSON serialisation. The fix was an explicit `assistant_message_dict()` converter. For support engineers, this appears as "the first message always works, subsequent messages fail" — a subtle stateful bug.

### Architecture decisions and their tradeoffs

**Why `extract_state` was removed**

Initially the agent used two tools: `extract_state` (called after every user message to track partial state) and `submit_request`. This added ~120 tokens per turn to the conversation history as accumulated tool call/response pairs — over an 8-turn conversation, roughly 1,000 tokens of pure overhead. The model already reads collected state from conversation history naturally, so `extract_state` was redundant bookkeeping. Removing it reduced token usage by ~40% per conversation and eliminated an entire class of schema validation errors.

This is a meaningful architectural lesson: tool-calling should be reserved for *actions* (database writes, API calls), not *observation* (reading state the model already has from context).

**Why normalizers are separate from the schema**

The `submit_request` schema defines `spend_type` as an enum `["one-time", "recurring", "subscription"]`. But Llama ignores JSON Schema enum constraints in practice — it will pass `"annual"`, `"per year"`, or `"yearly"` freely. Rather than relying on schema enforcement, a post-extraction normalizer maps free-form values to canonical ones before any DB write:

```python
_SPEND_TYPE_MAP = {
    "one-time": "one-time", "once": "one-time", "single": "one-time",
    "recurring": "recurring", "annual": "recurring", "yearly": "recurring",
    "subscription": "subscription", "monthly": "subscription", "saas": "subscription",
}
```

This is how production LLM pipelines handle enum drift — you can't trust the model to always return exactly what the schema says, so you normalise at the boundary. A support engineer who understands this can immediately identify "data is being stored with unexpected values" as a normalizer gap, not a model failure.

**Enum normalization handles `None` inputs**

All normalizers defensively handle `None`:

```python
def normalize_spend_type(raw) -> str:
    if not isinstance(raw, str):
        return "one-time"
    return _SPEND_TYPE_MAP.get(raw.lower().strip(), "one-time")
```

Without this, a `None` input from a model that omits an optional field causes `AttributeError: 'NoneType' object has no attribute 'lower'` — a crash that looks like an application bug but is actually a model output handling gap.

**Session TTL and memory management**

Sessions are stored in a process-level dict. This means a server restart clears all in-flight conversations. For a POC this is acceptable, but for production it means a deployment or crash mid-conversation silently loses state. The mitigation is externalising session state to Redis or a database — a natural POC-to-production graduation path, and a common source of "the agent forgot what I said" support tickets.

### Token economics as a support concern

The Groq free tier (100k tokens/day) was exhausted in a single multi-turn test session. This exposed how quickly token consumption compounds in agentic systems:

- System prompt: ~180 tokens, sent on every turn
- Tool definitions: ~200 tokens, sent on every turn
- Conversation history: grows by ~100 tokens per turn, and *all previous turns are resent each time*
- Model output: capped at `max_tokens`

A 10-turn conversation with a verbose system prompt and two tools can consume 8,000–12,000 tokens — not because of one large response, but because of compounding context window resending. Understanding this is directly relevant to supporting Omnea customers asking why their token bills are high or why the agent slows down in long conversations.

---

## 4. How Closely It Mirrors Omnea

| Omnea feature | Our POC | Fidelity |
|---|---|---|
| Conversational intake | Yes — multi-turn, session-aware | High |
| Trusted supplier catalog check | Yes — embedded in system prompt | Medium (Omnea queries live contracts DB) |
| Real-time policy flag surfacing | Yes — during conversation | High |
| Inherent risk scoring | Yes — 0–100, 4 labels | High |
| Adaptive questionnaire depth | Yes — 3 tiers, 5/10/15 questions | High |
| Approval workflow | Yes — status machine, API-key auth | Medium (Omnea has role-based approvers) |
| Supplier portal / questionnaire delivery | Stub only — read-only UI | Low (Omnea has token-gated supplier forms) |
| Contract management | Not built | Out of scope |
| Ongoing vendor monitoring | Not built | Out of scope |
| Multi-tenant / company isolation | company_id field present, not enforced | Low |

The core intelligence layer — intake, risk, policy, questionnaire scoping — is faithfully reproduced. The workflow infrastructure (multi-tenant auth, supplier portal, contract lifecycle) is out of POC scope but architecturally anticipated in the data model.

---

## 5. Why This Matters for a Client Support Engineering Role

A client support engineer at Omnea is, in practice, a debugging partner for enterprise customers whose procurement workflows are broken. The failure modes this POC surfaced — schema validation errors, model format drift, stateful session bugs, ORM configuration issues, token exhaustion — are exactly the failure modes that will appear in production Omnea deployments.

Having built the intake layer from scratch means:

- I can read an error traceback from `intake_agent.py` or `_groq_utils.py` and immediately understand what it's telling me, because I wrote equivalent code
- I know where the seams are between the LLM, the tool schema, the normalizers, and the database — and which seam a given error is most likely coming from
- I understand the product's architecture at the level needed to distinguish "model misbehaviour" from "application bug" from "configuration error" — three things that look identical to a customer but require completely different resolutions
- I can have an informed conversation with Omnea's engineering team about a customer issue, because I understand what the system is doing and why

The gap between "I've used this product" and "I've built an equivalent system and debugged it" is significant. This POC represents the latter.
