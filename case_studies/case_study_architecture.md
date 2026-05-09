# Procurement Intake Agent — Architecture & Build

---

## What the Application Does

The procurement intake agent replaces a static form with a conversational AI. An employee types something like "I need to onboard CyberShield Ltd for a penetration testing engagement, £40k a year" — the agent asks follow-up questions for anything missing, surfaces policy obligations in real time ("this will require Finance approval"), checks whether the supplier already has an approved contract, and when all required information is confirmed, submits a structured request to a database with a computed risk score, approval routing, and a questionnaire assigned.

The goal is to mirror Omnea's core AI-native intake flow: intake, risk assessment, approval routing, and supplier due-diligence questionnaire — all driven by a conversational LLM rather than a form.

---

## How It's Structured — The Layers and How They Connect

The application has five layers that build on each other. Understanding what each layer does and why it exists makes the data flow much easier to follow.

```
Layer 0 — Scaffold        FastAPI app, CORS, DB connection, startup hook
    ↓
Layer 1 — Data Model      ORM tables, audit log, SQLAlchemy session management
    ↓
Layer 2 — Utilities       Groq client, retry logic, enum normalizers
    ↓
Layer 3 — Intake Agent    LLM conversation loop, risk scoring, policy engine
    ↓
Layer 4 — API Router      HTTP endpoints, serialization, authentication
    ↓
Layer 5 — Test Suite      Full coverage, mocked LLM, in-memory DB
```

Each layer only depends on the layers below it. The router calls the agent; the agent calls the utilities; the utilities call Groq. Nothing in the utilities knows about HTTP, and nothing in the router does risk scoring. This separation is what makes the system testable and debuggable.

---

## The Data Flow — What Actually Happens

When a user sends a message, here's the full journey:

**1. HTTP request arrives at `POST /chat`**

The FastAPI router receives `{ session_id: "abc", message: "I need to buy Figma" }`. The router's only job is to validate the input shape (Pydantic does this automatically), inject a database session via dependency injection, and call `intake_agent.chat()`.

**2. Session state is fetched or created**

The intake agent maintains a process-level dictionary (`_sessions`) keyed by `session_id`. Each session holds the full conversation history as a list of plain dicts — every user message, every assistant reply, every tool call and its result. If this is the first message, a new session is created and the system prompt is prepended as the first message.

This is how the LLM "remembers" the conversation: the entire history is sent to Groq on every call. There's no summarization or compression — each API call resends everything.

**3. Groq is called with the full conversation + tool definitions**

The agent calls Groq's API with the message history and a single tool: `submit_request`. The model reads the conversation, decides what's still missing, and either asks a follow-up question (plain text response) or calls `submit_request` when everything is confirmed.

The system prompt tells the model what's required, what the trusted supplier catalog looks like, and how to behave — batch up to 3 missing fields per message, flag Finance approval for spend over £10k, flag Legal/DPO review for personal data access.

**4. The loop handles the response**

The agent runs a loop (capped at 8 iterations to prevent runaway behaviour). If the model returns plain text, that's the reply — store it in the session, return it to the router. If the model calls `submit_request`, move to step 5.

The Groq SDK returns a Pydantic object. Before appending it to the message history, it's converted to a plain dict. This matters because the next Groq API call will try to JSON-serialize the history — Pydantic objects aren't JSON-serializable by default, which would crash the second message in every conversation.

**5. Submission — risk scoring and policy evaluation**

When `submit_request` is called, the raw LLM arguments are passed through normalizers before anything is written to the database. The model might return `"annual"` for spend type — the normalizer maps that to `"recurring"`. It might return `"customer records"` for data access — the normalizer maps that to `"personal_data"`. This normalization happens at the boundary between the LLM and the database, every time.

After normalization, `risk_scorer.score_supplier()` computes an inherent risk score (0–100) from spend amount, data access level, category, and whether the supplier is new. This uses the same "inherent risk" terminology as Omnea — risk before the supplier provides certifications or mitigations.

Then `policy_engine.evaluate()` applies rules: spend over £10k triggers Finance approval, personal data access triggers Legal and DPO review, high risk score triggers IT Security review. The output includes a list of required approvers, any policy flags to surface, and a questionnaire depth — `basic` (5 questions), `standard` (10), or `deep_due_diligence` (15).

All of this is written to the database in a single transaction: the `ProcurementRequestORM` row and an `AuditLogORM` entry for the creation event.

**6. The response travels back up**

The router fetches the newly created record from the DB, serializes it to a dict (parsing `policy_flags` from its stored JSON string back to a list), and returns it as a `ChatResponse`. The chat UI renders the reply text and, if a request was submitted, displays a card showing the risk assessment, questionnaire depth, policy flags, and all the collected field values.

---

## Key Design Decisions

**The LLM only has one tool**

Originally the agent had two tools: `extract_state` (called every turn to track partial information) and `submit_request`. `extract_state` was removed because it was wasteful — the model already has access to the full conversation history and can read what's been collected from it. Every `extract_state` call added ~120 tokens to the history, and those accumulated across turns. A 10-turn conversation was burning ~1,500 extra tokens just on bookkeeping. The model doesn't need a tool to track state it can already see.

This is a broader design principle: tool-calling should be reserved for *actions* that have side effects (writing to a database, calling an external API), not for *observation* (reading information the model already has).

**Normalization at the boundary, not at the schema**

The `submit_request` tool schema defines `spend_type` as an enum with three valid values. But Groq validates this strictly — if the model returns `"annual"` (which it does, regularly), the API rejects the entire request with a 400 error. The fix is to make optional enum fields nullable in the schema and apply normalization in Python after the model's output arrives. Schema enforcement catches format errors; normalization corrects semantic drift.

This is a general pattern for LLM pipelines: you can't fully trust the model to follow schema constraints, so you validate at the application layer, not just at the API layer.

**Dependency injection for testability**

Every endpoint that needs a database session receives it via `Depends(get_db)`. In tests, `app.dependency_overrides[get_db]` swaps the real database for an in-memory one. This means the same application code runs in tests and in production — no conditional logic, no environment flags, no separate test paths.

The test database uses `StaticPool` (a SQLAlchemy option that forces all connections to share one underlying connection). Without it, each database operation opens a fresh connection to the in-memory database — and SQLite in-memory databases are per-connection, so a second connection sees an empty database with no tables.

**Audit log in the same transaction**

When a request is submitted or a status changes, the application writes both the change and an `AuditLogORM` entry in the same database commit. Either both succeed or neither does. This means there's no window where a status change exists without a record of who made it — a requirement in any compliance context.

---

## How It Maps to Omnea

The core intelligence of Omnea's intake agent is faithfully reproduced:

**Conversational intake** — the same model: ask questions, fill gaps, surface policy implications in real time during the conversation rather than after submission.

**Trusted supplier catalog** — before collecting details, the agent checks whether the requested supplier already has an approved contract. In our POC this catalog lives in the system prompt. Omnea queries a live contracts database. Same logical behaviour, different implementation depth.

**Inherent risk scoring** — Omnea's TPRM framework distinguishes inherent risk (pre-certification) from residual risk (post-certification). We compute inherent risk only — residual risk would require the supplier questionnaire responses, which is Omnea's Supplier Portal feature (out of scope for this POC).

**Adaptive questionnaire depth** — the risk tier drives how many due-diligence questions the supplier receives. Low-risk: 5 basic questions. High-risk with personal data: 15 questions covering GDPR compliance, penetration testing, financial stability, and business continuity. This is exactly how Omnea's questionnaire scoping works.

**Approval routing** — the policy engine outputs a list of required approvers (Finance, Legal, DPO, IT Security, CFO) based on spend thresholds and data sensitivity. Omnea's Workflow Builder implements the same condition/action rule evaluation.

**Audit trail** — every status change writes an immutable log entry. Omnea surfaces this as a timeline in the request detail view, used by procurement teams to demonstrate compliance to auditors.

What the POC doesn't have: multi-tenant isolation (company_id is in the schema but not enforced), a live supplier portal for questionnaire delivery (the frontend shows the questionnaire as read-only), and contract lifecycle management.

---

## What Building It Revealed

**The failure modes that support engineers will encounter**

The most common class of errors in production agentic systems is the schema mismatch — the model generates output that doesn't match the expected format, causing a hard API rejection. This is invisible in happy-path testing because a well-constructed test will always provide exactly the right format. It only surfaces mid-conversation when the model has partial state and makes assumptions.

The second class: stateful bugs. "Works on first message, breaks on second" is almost always a serialization or session management issue. In this system, appending the Pydantic response object directly to the message list worked on the first call but crashed on the second, because the second call tried to JSON-serialize an object that wasn't serializable.

**Token economics matter architecturally**

A naive implementation of this system burned 100k free-tier tokens (Groq's daily limit) in a single test session. The reason isn't long responses — it's that every message resends the entire conversation history plus all tool definitions. Architectural decisions like removing `extract_state`, shortening the system prompt, and capping `max_tokens` reduced per-conversation consumption by roughly 40%. Understanding this is directly relevant to supporting customers who report slow responses or unexpected API costs.

**The seam between LLM and application is where most bugs live**

The LLM, the tool schema, the normalizers, and the database writer are four distinct components. Each seam between them is a potential failure point: the model produces output that doesn't match the schema (schema seam), the schema produces a value the normalizer doesn't recognise (normalizer seam), the normalizer produces a value the database rejects (DB seam). Being able to identify which seam a given error is coming from is the core diagnostic skill for supporting an AI-native procurement system.
