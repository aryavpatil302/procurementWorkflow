# Case Study: Layer 0 — Project Scaffold

## What We Built

A bare FastAPI application with the full directory structure, dependency manifest, environment variable handling, and a `GET /health` endpoint. No business logic yet — this is the skeleton the rest of the system hangs on.

**Files created:**
- `requirements.txt` — pinned dependencies
- `.env` — secret storage (never committed)
- `.gitignore` — prevents secrets and build artifacts from reaching git
- `backend/main.py` — FastAPI app factory, CORS config, startup hook
- `backend/database.py` — SQLAlchemy engine + session factory stub
- Stub files for all planned modules (empty but importable)

---

## How It Mirrors Omnea

Omnea's backend is a multi-service Python monolith (FastAPI-based, PostgreSQL, background workers). The structure we're creating deliberately mirrors that shape:

| Our POC | Omnea Production |
|---|---|
| `backend/routers/` | Separate route modules per domain (intake, suppliers, sourcing) |
| `backend/services/` | Service layer isolating business logic from HTTP layer |
| `backend/database.py` | SQLAlchemy with connection pooling |
| `CORS allow_origins` | Allowlist by environment (dev/staging/prod) |
| `.env` / `GROQ_API_KEY` | Secrets manager (AWS SSM / Vault) |

The startup hook (`@app.on_event("startup")`) calling `init_db()` mirrors Omnea's Alembic migration check on boot — ensuring the DB schema is always current before the app starts accepting traffic.

---

## Benefits

**Fast time-to-value.** FastAPI generates an OpenAPI spec automatically at `/docs` — you can test every endpoint through a browser UI without writing a single line of frontend code. For a POC, this halves the iteration time.

**SQLite for zero friction.** No Docker, no Postgres install, no connection strings. The entire DB is a single file (`procurement.db`) in the project root. A developer can clone the repo and be running in 60 seconds.

**Pinned dependencies.** `requirements.txt` with exact versions (e.g. `sqlalchemy==2.0.36`) ensures the project doesn't silently break when a library releases a new major version. This is especially important for SQLAlchemy — the `.get()` API change between 1.4 and 2.0 would otherwise cause silent failures months later.

**`.gitignore` first.** By writing `.gitignore` before any other file, we guarantee that `.env` (which will contain the real Groq API key) can never accidentally be committed. This is a discipline habit — the same order every project.

---

## Potential Pitfalls

**SQLite is not production-ready for concurrent writes.** SQLite uses file-level locking. If two requests hit the API at the same time and both try to write, one will block. Fine for a single-developer POC; catastrophic for 100 concurrent users. The fix is a `DATABASE_URL` environment variable and swapping to Postgres — the rest of the code doesn't change because SQLAlchemy abstracts the difference.

**`check_same_thread=False` is required but silently unsafe.** FastAPI uses an async worker (Uvicorn). SQLite's default is to refuse connections from threads other than the one that created the engine. `check_same_thread=False` disables that guard. In a single-worker, low-concurrency POC this is fine. In multi-worker production, you'd hit race conditions. Postgres doesn't have this problem.

**In-memory session store won't survive a restart.** The `_sessions` dict in `intake_agent.py` lives in the Python process. If Uvicorn restarts (code change, crash, deployment), all active conversations are lost. Production fix: Redis with a 60-minute TTL per key.

**`@app.on_event("startup")` is deprecated in FastAPI 0.111+.** The new pattern is `lifespan` context managers. We use the old API here for readability; a production codebase should migrate to `@asynccontextmanager`.

---

## Areas for Improvement

1. **Replace SQLite with Postgres** via `DATABASE_URL` env var and `asyncpg` driver for async DB access
2. **Add Alembic** for schema migrations — `CREATE TABLE IF NOT EXISTS` (what `Base.metadata.create_all` does) is not reversible and doesn't handle column additions cleanly
3. **Replace in-memory sessions with Redis** — `redis-py` with `SETEX` for TTL-based expiry
4. **Add structured logging** (JSON format, request-id header propagation) — Omnea almost certainly uses Datadog or similar
5. **Move to `lifespan` startup** — cleaner async lifecycle management

---

## Logic Flow

```
Developer runs: uvicorn backend.main:app --reload

  1. FastAPI app factory created (main.py)
  2. CORS middleware registered (origin whitelist: localhost:3000)
  3. Startup event fires → init_db()
     └── imports backend.models (registers ORM classes with Base.metadata)
     └── Base.metadata.create_all(engine)
         └── SQLAlchemy emits CREATE TABLE IF NOT EXISTS for each ORM class
  4. Router included (requests.py)
  5. Server ready — listening on 0.0.0.0:8000

GET /health
  └── Returns {"status": "ok"} — used by load balancers and health checks
```

---

## Code Flow

`main.py` is the composition root. It:
1. Instantiates `FastAPI()`
2. Attaches `CORSMiddleware` with the origin whitelist
3. Registers the startup hook that creates DB tables
4. Includes the router from `routers/requests.py`

`database.py` owns the engine. It exposes:
- `init_db()` — called once at startup
- `get_db()` — a generator yielded via FastAPI's dependency injection (`Depends(get_db)`) in every endpoint that needs a DB session. The `try/finally` ensures the session is always closed even if the handler raises.

The `Base` class (from `DeclarativeBase`) is the shared metadata registry. All ORM classes in `models.py` inherit from it — that's how `create_all` knows which tables to create.
