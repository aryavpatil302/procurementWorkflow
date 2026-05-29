# Omnea Procurement POC

A working proof-of-concept for an intelligent procurement intake and approval orchestration platform. Built to demonstrate how AI-driven intake, dynamic risk scoring, and configurable approval workflows can replace fragmented procurement processes in enterprise environments.

---

## Demos

| Demo 1: Procurement Request | Demo 2: Routing Engine | Demo 3: Workflow Builder |
|---|---|---|
| [![Demo 1: Procurement Request](https://img.youtube.com/vi/lY636nzE_Wc/maxresdefault.jpg)](https://www.youtube.com/watch?v=lY636nzE_Wc) | [![Demo 2: Routing Engine](https://img.youtube.com/vi/79Zs1l1TxvA/maxresdefault.jpg)](https://www.youtube.com/watch?v=79Zs1l1TxvA) | [![Demo 3: Workflow Builder](https://img.youtube.com/vi/WVoXOFQmJKM/maxresdefault.jpg)](https://www.youtube.com/watch?v=WVoXOFQmJKM) |

---

## What it does

**Intake** — An AI agent guides employees through a new supplier request via natural conversation. It infers fields like category, geography, and spend type from context, surfaces real-time policy alerts, and produces a structured summary when all required information is collected.

**Risk scoring** — Each request is automatically scored for inherent risk based on spend amount, data access level, supplier category, geography, and whether the supplier is new. Policy flags are raised where approvals or reviews are required.

**Approval orchestration** — Requests are routed through configurable, multi-stage approval workflows. Stages run sequentially, with parallel reviewers within a stage. Each approver receives an AI-generated briefing tailored to their role (Finance, Legal, DPO, IT Security, etc.).

**Workflow configuration** — Approval flows are fully configurable through a visual builder. Flows are triggered by conditions (category, spend threshold, new supplier status) and apply automatically to in-flight requests when published.

**Analytics** — A real-time dashboard shows pipeline spend, request status breakdown, risk distribution, spend by category, and active policy flags.

---

## Architecture

```
procurementWorkflow/
├── backend/
│   ├── main.py                  FastAPI app entry point
│   ├── models.py                SQLAlchemy ORM models
│   ├── database.py              DB session and init
│   ├── workflow_config.json     Approval flow definitions
│   └── services/
│       ├── intake_agent.py      Conversational AI intake (Groq/LLaMA)
│       ├── approval_engine.py   Workflow matching, step generation, AI summaries
│       ├── risk_scorer.py       Inherent and residual risk scoring
│       ├── policy_engine.py     Policy flag evaluation and approver routing
│       └── _groq_utils.py       Groq client and retry logic
│   └── routers/
│       ├── chat.py              POST /chat
│       ├── requests.py          GET/POST /requests
│       ├── approvals.py         Approval queue and decisions
│       ├── workflow.py          GET/PUT /workflow-config
│       ├── questionnaire.py     POST /submit-request
│       └── analytics.py         GET /analytics/summary
└── frontend/
    ├── chat.html                Intake UI (AI chat + questionnaire form)
    └── dashboard.html           Approver dashboard, workflow builder, analytics
```

**Stack:** Python 3.13, FastAPI, SQLAlchemy, SQLite, Groq API (LLaMA 3.3 70B)

---

## Getting started

**Prerequisites:** Python 3.11+, a free [Groq API key](https://console.groq.com/keys)

```bash
cd procurementWorkflow

# 1. Install dependencies
pip install -r requirements.txt

# 2. Add your Groq API key
cp .env.example .env
# Edit .env and set GROQ_API_KEY=your_key_here

# 3. Start the server
python3 -m uvicorn backend.main:app --port 8080
```

The database is created automatically on first run. No migrations needed.

Open **http://localhost:8080/chat** to start a procurement request, or **http://localhost:8080/dashboard** to view the approver dashboard.

---

## Usage

### Submitting a request
Navigate to `/chat` and describe your procurement need in plain language. The AI agent will ask follow-up questions, infer fields where possible, and produce a structured summary. You can then confirm details in the form before submitting.

### Approving requests
Open `/dashboard` and go to Pending Approvals. Each step shows an AI-generated briefing for that reviewer's role alongside the request details. Approvers can approve, reject, or escalate with a note.

### Configuring workflows
In the dashboard, go to Workflow Rules. You can create new flows, define trigger conditions (category, spend threshold, new supplier), and add sequential or parallel approval stages. Publishing a flow automatically re-routes any in-flight requests that match the new conditions.

---

## Notes

- The Groq free tier has a 100,000 token per day limit. The app handles rate limiting gracefully and retries failed AI summary generation in the background.
- `workflow_config.json` is the source of truth for approval flows and is committed to the repo. Changes made through the UI write directly to this file.
- The SQLite database is gitignored. It is created fresh on each new setup.
