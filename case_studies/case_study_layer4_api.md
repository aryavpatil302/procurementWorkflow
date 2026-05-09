# Case Study: Layer 4 — API Router & HTTP Endpoints

## What We Built

The HTTP layer that wires the intake agent to the outside world. Five endpoints cover the full request lifecycle: chat, list, get, approve/reject, and health.

**Files:**
- `backend/routers/requests.py` — all five endpoints + serializer
- `backend/main.py` — app factory, CORS, startup hook
- `tests/test_api.py` — 12 end-to-end HTTP tests, all passing

**Bug found during testing**: `StaticPool` is required for SQLite in-memory test databases. Without it, each new SQLAlchemy session creates a fresh, empty in-memory database — tables from `create_all` are invisible to subsequent sessions. This is a well-known SQLAlchemy testing gotcha that bites anyone moving from file-based SQLite to in-memory for tests.

---

## How It Mirrors Omnea

### The API Surface

Omnea's API (exposed via their MCP server and documented in their developer portal) follows the same REST patterns:

| Our endpoint | Omnea equivalent |
|---|---|
| `POST /chat` | Intake chat API (AI-native) |
| `GET /requests` | "All Requests" list view |
| `GET /requests/{id}` | Request detail view |
| `PATCH /requests/{id}/status` | Approval action (approve/reject) |
| `GET /health` | Load balancer health check |

The `/chat` endpoint accepts `session_id` — a client-generated UUID that identifies the ongoing conversation. This is how the frontend associates multiple messages with the same intake conversation. Omnea uses the same pattern — their chat sessions are tied to a specific intake event, not to a user login.

### PATCH vs PUT for Status Updates

We use `PATCH /requests/{id}/status` (not `PUT /requests/{id}`). `PATCH` signals "partial update" — we're only changing the `status` field and writing an audit log entry. `PUT` would imply replacing the entire resource. Using `PATCH` is more semantically correct and is what Omnea uses in their own API (visible in network requests from their UI).

### CORS Lockdown

```python
allow_origins=["http://localhost:3000"]
```

The FastAPI docs example uses `allow_origins=["*"]` — it's copy-pasted into every tutorial. We explicitly restrict to the frontend origin. In production, this list would be driven by an environment variable: `CORS_ORIGINS=https://app.company.com`. The wildcard `*` means any website can make authenticated API calls to your backend — a significant security risk in a procurement context where request data is sensitive.

### FastAPI Dependency Injection for DB Sessions

Every endpoint that needs a DB session takes `db: Session = Depends(get_db)`. This means:
1. FastAPI calls `get_db()` before the handler runs
2. `get_db()` creates a session, yields it to the handler
3. After the handler returns (or raises), `get_db()` closes the session in `finally`

The test fixture overrides `get_db` with `override_get_db` — which yields a session connected to the in-memory test DB instead. This is FastAPI's intended testing pattern and it works seamlessly as long as you're consistent about using `Depends(get_db)` everywhere rather than calling `get_db()` directly.

### The Serializer Pattern

`_serialize(req)` is a plain function that converts `ProcurementRequestORM` to a dict. We avoid using FastAPI's Pydantic response schemas here because:
1. The ORM model already has all the fields
2. Adding a separate Pydantic schema would be 30 lines of duplication for a POC
3. The `created_at.isoformat()` pattern handles the datetime → string conversion that Pydantic would do automatically anyway

In production, you'd want Pydantic response schemas for documentation (they appear in the `/docs` OpenAPI UI) and runtime validation.

---

## Benefits

**The `/docs` endpoint.** FastAPI auto-generates an OpenAPI spec from the route definitions and Pydantic schemas. Point a browser at `http://localhost:8000/docs` and you get an interactive API explorer where you can send real POST /chat requests without writing any frontend code. For a demo to a procurement team, this is invaluable.

**Status update writes audit log in the same DB call.** The `PATCH /requests/{id}/status` handler updates the request status and writes an `AuditLogORM` entry in the same session, committed together. Either both succeed or neither does. This is atomicity — a critical property for compliance systems where "status changed but audit log not written" would be a regulatory problem.

**Dependency injection for easy testing.** The `app.dependency_overrides` pattern means the same application code runs in tests with an in-memory DB and in production with a real DB. Zero conditional logic in the handlers.

**Explicit 404 behavior.** `db.get(ProcurementRequestORM, request_id)` returns `None` if the ID doesn't exist. We explicitly raise `HTTPException(status_code=404)` rather than letting Python raise an `AttributeError` on `req.supplier_name`. The difference: a 404 is a client error (wrong ID), a 500 is a server error. Getting this distinction right matters for API consumers who need to handle "not found" differently from "server crashed."

**Input validation on status.** `PATCH /requests/{id}/status` validates that the new status is in `{"pending", "approved", "rejected", "cancelled"}` before hitting the DB. A request like `{"status": "flying"}` returns 422 immediately, without touching the database. The test `test_update_status_invalid` verifies this.

---

## Potential Pitfalls

**No authentication.** Any caller with network access to port 8000 can read all requests (`GET /requests`) and approve anything (`PATCH`). In a real procurement system, this would be catastrophic. Fix: add OAuth2/JWT authentication via FastAPI's `Depends(get_current_user)` pattern. The `actor` field in `PATCH /requests/{id}/status` should come from the authenticated user, not from the request body.

**`GET /requests` has no pagination.** With 10 requests, this is fine. With 100,000 requests, this returns a response that could be hundreds of MB. Fix: add `?limit=50&offset=0` query parameters and cap at a maximum page size.

**No filtering on `GET /requests`.** You can't query "all pending requests" or "all requests over £10k." Fix: add query parameters like `?status=pending&min_spend=10000`.

**The serializer calls `.isoformat()` without checking for None.** `req.created_at.isoformat() if req.created_at else None` is correct — but it relies on the programmer to remember this pattern every time a new datetime field is added. Fix: use a Pydantic response schema that handles the conversion automatically and raises a `ValidationError` if a required field is None.

**`@app.on_event("startup")` is deprecated.** FastAPI 0.111 still supports it but shows a deprecation warning (visible in test output). The modern pattern uses `@asynccontextmanager` with `lifespan`. We kept the old pattern for readability, but a production codebase should migrate.

---

## Areas for Improvement

1. **Authentication** — FastAPI OAuth2 with Bearer tokens, `get_current_user` dependency
2. **Pagination** — `GET /requests?limit=50&offset=0` with a response envelope `{items: [...], total: N}`
3. **Filtering** — `?status=pending`, `?department=Engineering`, `?min_spend=10000`
4. **Pydantic response schemas** — typed response models that document the API shape in `/docs`
5. **Migrate to `lifespan`** — replace `@app.on_event("startup")` with the modern FastAPI lifespan context manager
6. **Add `GET /requests/{id}/audit-log`** — endpoint to fetch the full audit trail for a request
7. **Rate limiting** — `slowapi` middleware to prevent abuse of `POST /chat` (which calls a paid LLM API)

---

## Logic Flow

```
POST /chat { session_id: "s1", message: "I need Figma" }
  └── FastAPI routes to chat_endpoint()
  └── Depends(get_db) → yields a Session from the pool
  └── chat("s1", "I need Figma", db) → intake_agent
  └── Returns ChatResponse(reply="...", is_complete=False, request_id=None)
  └── Session closed (get_db finally block)

GET /requests
  └── FastAPI routes to list_requests()
  └── Depends(get_db) → yields a Session
  └── db.query(ProcurementRequestORM).order_by(created_at.desc()).all()
  └── [_serialize(r) for r in rows] → list of dicts
  └── FastAPI serializes to JSON response

PATCH /requests/{id}/status { status: "approved", actor: "manager@co.com" }
  └── validate status is in valid set
  └── db.get(ProcurementRequestORM, id) → fetch row
  └── req.status = "approved"
  └── db.add(AuditLogORM(action="status_change", ...))
  └── db.commit() → both changes committed atomically
  └── db.refresh(req) → reload from DB
  └── _serialize(req) → return updated state
```

---

## Code Flow

`main.py` is the application factory:
- Instantiates `FastAPI()` with title and version (shown in `/docs`)
- Adds `CORSMiddleware` with the origin whitelist
- Registers `on_startup` → `init_db()`
- Includes `requests_router` (all 5 endpoints)
- Defines `GET /health` inline (no router needed for a single system endpoint)

`routers/requests.py`:
- `ChatRequest` / `ChatResponse` — Pydantic models for POST /chat body and response
- `StatusUpdate` — Pydantic model for PATCH body
- `chat_endpoint` — delegates to `intake_agent.chat()`, returns `ChatResponse`
- `list_requests` — SQLAlchemy query, sorted by `created_at DESC`, serialized
- `get_request` — `db.get()` (SQLAlchemy 2.0 API), raises 404 on None
- `update_status` — validates status string, updates row, writes audit log, commits atomically
- `_serialize(req)` — converts ORM → dict. Called from all read endpoints.

The key design: **all business logic stays in `services/`**. The router handlers are thin — they receive HTTP input, call a service function or write directly to the DB, and return HTTP output. No risk scoring, no policy evaluation, no LLM calls in the router.
