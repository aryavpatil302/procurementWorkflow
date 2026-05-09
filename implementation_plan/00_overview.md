# Omnea Procurement Lifecycle POC — Full Overview

## The Problem This Solves

Omnea's founding insight: the average enterprise procurement request touches **11 stakeholders over 6 months**. Not because the work takes that long, but because the process is a relay race where the baton keeps getting dropped — an employee emails a form, a manager forwards it to Finance, Finance waits for IT Security, IT Security asks for a questionnaire the supplier fills in two weeks later, Legal reviews the output, and so on. Each handoff is manual, context is lost at every step, and nobody knows where the request actually is.

Omnea collapses this to **days** by making the relay race automatic:

> "Employees submit → AI structures → policy flags → routed → approved/rejected/escalated"

The POC in this repository demonstrates exactly that collapse, phase by phase, across the full procurement lifecycle.

---

## Full Lifecycle Diagram

```
                        ┌─────────────────────────────────────────────────────────────────────────┐
                        │                    OMNEA PROCUREMENT LIFECYCLE                          │
                        └─────────────────────────────────────────────────────────────────────────┘

  ┌───────────┐    ┌────────────────────┐    ┌────────────────────┐    ┌──────────────────────┐
  │ Employee  │───▶│  Aria: Conv Intake │───▶│   AI Discovery     │───▶│ Adaptive Questionnaire│
  │ has a     │    │  (chat.html)       │    │   Card             │    │ (3 steps: General /  │
  │ need      │    │  "What are you     │    │   Supplier name,   │    │  Commercial / Risk)  │
  └───────────┘    │   buying?"         │    │   spend, category  │    └──────────┬───────────┘
                   └────────────────────┘    └────────────────────┘               │
                                                                                   │
                   ┌───────────────────────────────────────────────────────────────▼───────────┐
                   │              SUBMIT → Risk Score + Policy Evaluation                      │
                   │   score_supplier() → compute_residual_risk() → policy_engine.evaluate()  │
                   │   ProcurementRequestORM written → generate_approval_steps() called       │
                   │   request.status set to 'in_review'                                      │
                   └───────────────────────────────┬───────────────────────────────────────────┘
                                                   │
                   ┌───────────────────────────────▼───────────────────────────────────────────┐
                   │                   APPROVAL ORCHESTRATION (Phase 2)                        │
                   │   Sequential gate (Manager) → Parallel group (Finance / IT Sec / Legal)  │
                   │   → Post-parallel (CFO if >£50k) → request.status = approved/rejected    │
                   │   Each approver gets pre-generated role-specific AI summary (Omnea Analyze)│
                   └───────────────────────────────┬───────────────────────────────────────────┘
                                                   │ approved
                   ┌───────────────────────────────▼───────────────────────────────────────────┐
                   │               SUPPLIER PORTAL / MINI TPRM (Phase 3)                      │
                   │   Magic-link style portal → adaptive depth questionnaire                  │
                   │   Supplier declares certs → residual risk recalculated                   │
                   │   Remediation tasks surfaced if risk still high                           │
                   └───────────────────────────────┬───────────────────────────────────────────┘
                                                   │ submitted
                   ┌───────────────────────────────▼───────────────────────────────────────────┐
                   │                  SUPPLIER RECORD (Phase 3 → 4)                           │
                   │   SupplierRecordORM created: identity, risk, certs, contract, contacts   │
                   └───────────────────────────────┬───────────────────────────────────────────┘
                                                   │
                   ┌───────────────────────────────▼───────────────────────────────────────────┐
                   │             RENEWAL MANAGEMENT + INTELLIGENCE QUERY (Phase 4)            │
                   │   90/60/30-day renewal alerts → auto-launch renewal chat session         │
                   │   Natural language query: "Which suppliers renew in 60 days?"            │
                   │   Supplier 360 profile: identity, risk, contracts, spend, certs          │
                   └───────────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

No framework changes. No new dependencies beyond what already exists. Everything runs locally.

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend API | FastAPI | Already running on `uvicorn` |
| ORM | SQLAlchemy | Already wired; new models added to `backend/models.py` |
| Database | SQLite | `procurement.db` — single file, zero setup |
| LLM | Groq / Llama 3.3 70B | Already in `_groq_utils.py` (`MODEL`, `get_client()`, `call_with_retry()`); reused for summaries and query |
| Frontend | Vanilla HTML + JS | `fetch()` calls to the FastAPI backend; no build step |
| Config | JSON | `backend/workflow_config.json` for approval rules |

---

## The Spine: `request_id`

Every object in the system is connected by a single `request_id` (UUID string, PK on `ProcurementRequestORM`). This is the audit trail backbone.

```
ProcurementRequestORM.id
    ├── ApprovalStepORM.request_id         (1 request → N approval steps)
    ├── SupplierAssessmentORM.request_id   (1 request → 1 assessment)
    └── SupplierRecordORM                  (created from assessment, links back by supplier_name)
```

Every API call, every LLM summary, every portal link uses this `request_id`. The demo can open the same request in 5 different views and they all stay consistent.

---

## New Database Tables Summary

| Table | Purpose | Phase |
|-------|---------|-------|
| `procurement_requests` | Already exists — the intake record | Phase 1 (existing) |
| `audit_log` | Already exists — change history | Phase 1 (existing) |
| `approval_steps` | One row per approval step; tracks status per role; stores pre-generated AI summary | Phase 2 |
| `supplier_assessments` | Supplier-side questionnaire responses, portal status | Phase 3 |
| `supplier_records` | Consolidated 360 supplier profile, renewal tracking | Phase 3/4 |

Full schemas are defined in each phase's implementation file.

---

## Status Transition Matrix

The `ProcurementRequestORM.status` field follows this state machine:

| From | To | Trigger |
|------|----|---------|
| `pending` | `in_review` | `generate_approval_steps()` called after intake submit |
| `in_review` | `approved` | All approval steps approved (`advance_workflow()`) |
| `in_review` | `rejected` | Any approval step rejected (`advance_workflow()`) |
| `approved` | (no transition) | Terminal state; "Send Supplier Assessment" button appears |
| `rejected` | (no transition) | Terminal state |

**Valid status values:** `pending`, `in_review`, `approved`, `rejected`

The frontend uses these values for badge colours:

```javascript
const STATUS_COLOURS = {
  pending:   { bg: '#E5E7EB', text: '#374151' },
  in_review: { bg: '#DBEAFE', text: '#1D4ED8' },
  approved:  { bg: '#D1FAE5', text: '#065F46' },
  rejected:  { bg: '#FEE2E2', text: '#991B1B' },
};
```

---

## Omnea Product Mapping

| POC Phase | Omnea Product | URL |
|-----------|--------------|-----|
| Phase 1 (existing): Conversational intake, adaptive questionnaire, risk scoring | Omnea Assist / Intelligent Intake | `omnea.co/products/intake-management` |
| Phase 2: Approval orchestration, configurable rules, role-specific AI summaries | Workflow Builder + Approval Workflows + Omnea Analyze | `omnea.co/products/workflow-builder`, `omnea.co/products/approval-workflows` |
| Phase 3: Supplier portal, adaptive TPRM questionnaire, remediation | Supplier Portal + TPRM | `omnea.co/products/supplier-portal`, `omnea.co/products/third-party-risk-management` |
| Phase 4: 360 supplier profile, renewal alerts, natural language query | SRM + Renewal Management + MCP Server | `omnea.co/products/supplier-relationship-management`, `omnea.co/products/renewal-management` |

---

## End-to-End Demo Walkthrough

The primary demo scenario is **Workday** (£75,000/year HR platform, personal data, EU/Global geography). This scenario hits the most policy flags — Finance, IT Security, Legal, DPO, and CFO — and produces a deep TPRM questionnaire. It is the richest path through the system.

### Step 1: Employee Intake (Phase 1 — existing)
- Open `chat.html`
- Type: *"I need to procure Workday for our HR team — we're replacing our current payroll system"*
- Aria extracts: supplier=Workday, category=Software, spend=~£75,000/year
- Aria surfaces real-time policy alerts inline: Finance approval (>£10k), CFO approval (>£50k), Legal + DPO (personal data)
- Discovery card appears with pre-populated fields
- Walk through the 3-step questionnaire (General → Commercial → Risk & Compliance)
- On the Risk step: data_access=personal_data, geography=EU/Global, no certifications yet
- **Talking point:** "Aria replaces the email + PDF form. It surfaces policy alerts in real time — 'this spend level requires CFO approval' — before the request is even submitted. That's Omnea Assist."

### Step 2: Risk Score + Policy Evaluation (Phase 1 — existing)
- On submit, show the risk score calculation: inherent risk HIGH (personal_data + new_supplier + EU/Global + >£50k spend)
- No certifications declared → residual = inherent
- Policy engine flags: Finance, IT Security, Legal, DPO required; CFO required (>£50k); GDPR Article 46
- questionnaire_depth = deep (personal data + high spend)
- **Talking point:** "The policy engine is deterministic — it reads structured data and outputs required approvers. No AI hallucination in the routing decision. AI touches intake and summaries; the routing engine is pure rules."

### Step 3: Approval Queue Appears (Phase 2)
- Switch to `dashboard.html` → Approvals tab
- Select role: Manager
- Show the active approval card for the Workday request
- Pre-generated role-specific summary: "Sarah Chen needs to approve a £75,000/year Workday HR platform purchase for the HR team. This is a new supplier engagement involving personal data across EU/Global geography."
- Click Approve — summary loads instantly (pre-generated at step creation time, no LLM call needed)
- **Talking point:** "The manager is the sequential gate. Once approved, Finance, IT Security, Legal, and DPO activate simultaneously — parallel review. The AI summary was generated when the steps were created, so it loads instantly."

### Step 4: Parallel Approvals (Phase 2)
- Switch to Finance role — show their queue: different summary focused on £75k spend, TCV, cost centre
- Switch to IT Security — summary shows cert gaps flagged, risk score 0.8 (HIGH)
- Switch to Legal — summary shows GDPR flags, cross-border transfer obligations
- Switch to DPO — summary shows Article 46 cross-border transfer note specifically
- After all group-2 approvals done, CFO step activates (>£50k gate)
- Approve CFO → request.status = approved
- **Talking point:** "Omnea's biggest throughput win is parallelisation. Finance and IT Security review simultaneously — that's weeks of sequential waiting compressed into one window. Entrust saw 62.5% reduction in procurement cycle time."

### Step 5: Supplier Portal (Phase 3)
- Click "Send Supplier Assessment" on the approved Workday request (button appears because status=approved)
- Open `supplier_portal.html?id={request_id}` — simulating Workday receiving a magic link
- Because this is a deep-depth request: all 5 sections are shown (Company, Certifications, Data Handling, Financial, Security Architecture)
- Supplier declares: ISO 27001, SOC 2 Type II, encrypted at rest + in transit, EU data storage, CISO in place
- Submit → residual risk drops from HIGH to MEDIUM (cert credits applied)
- Remediation panel: "Request SOC 2 Type II audit report" (cert declared but documentation not provided)
- **Talking point:** "The supplier gets a magic link — no account, no login. The questionnaire depth is driven by the risk tier — Workday gets 20+ questions; a low-risk SaaS tool gets 5. Reach plc cut risk review time from 5 hours to 1–2 hours with this approach."

### Step 6: Supplier Record Created (Phase 3 → 4)
- Switch to Suppliers tab in dashboard
- Workday now appears as a supplier record
- 360 profile: risk (inherent → residual), certs, contract value, expiry date, contact, geography, data access
- **Talking point:** "Every field in this record was captured automatically — from the intake conversation and the supplier's own assessment. No manual data entry. This is Omnea SRM."

### Step 7: Renewal Management (Phase 4)
- Set Workday's contract_expiry_date to 45 days from today (edit directly in SQLite for demo)
- Renewal calendar shows Workday highlighted amber: "45 DAYS"
- Click "Launch renewal review" → new chat session opens in `chat.html` pre-populated with Workday context
- **Talking point:** "Renewals happen because nobody cancelled in time. 90/60/30-day alerts with auto-launch fix that. The renewal chat pre-fills with last year's spend, geography, certifications, and risk tier — the requester just confirms what's changed."

### Step 8: Intelligence Query (Phase 4)
- In the query bar, type: *"Which suppliers have personal data access and are missing SOC 2?"*
- System fetches all supplier records, sends to Groq with the question
- Returns: "Workday — personal data access declared; SOC 2 Type II declared but audit report not confirmed per assessment."
- Type: *"What is our total annual spend across approved suppliers?"*
- Type: *"Which suppliers renew in the next 60 days?"*
- **Talking point:** "Omnea launched the industry's first procurement MCP Server on April 30, 2026 — making Omnea data queryable from Claude, ChatGPT, or Copilot. This is the local equivalent: natural language queries against live supplier data. The Adecco Group VP of Procurement Strategy said Omnea was 'the first tool in our stack that actually plugged into the AI tools we were already using.'"

---

## Key Interview Positioning Statements

### Phase 1 (Existing)
- "I built the intake layer the same way Omnea Assist works — conversational AI that extracts structured data, not a form. The structure is what makes everything downstream deterministic."
- "The risk scorer has two layers: inherent risk from the request attributes, and residual risk after certification credits. That's exactly Omnea's TPRM model — inherent to residual."
- "Real-time policy alerts during intake — 'this requires CFO approval' — surface before submit, not after. That's how you stop bad requests at the source."

### Phase 2 (Approval Orchestration)
- "I modelled the approval rules as JSON configuration, not hardcoded Python. Omnea's Workflow Builder gives customers a drag-and-drop canvas for the same thing — I built the engine underneath it."
- "Parallel approvals are the biggest throughput win. Entrust cut their procurement cycle by 62.5% — that's what happens when Finance and Legal review simultaneously instead of sequentially."
- "Omnea Analyze generates role-specific summaries. Finance sees budget. IT Security sees cert gaps. Legal sees GDPR flags. DPO sees Article 46 obligations. Same request, different lens per stakeholder."
- "AI summaries are pre-generated at step creation time — not fetched on click. The demo is instant. In production, this is the difference between 'click and wait 3 seconds' and 'click and see.'"

### Phase 3 (Supplier Portal / TPRM)
- "Questionnaire fatigue kills data quality. The portal adapts its depth to the risk tier — a low-risk tool gets 5 questions, Workday gets 20+. That's Omnea's proportional assessment philosophy."
- "When the supplier declares their certifications, the residual risk score updates immediately. That two-layer model — inherent to residual — is what makes the risk score meaningful."
- "Reach plc: 70% of supplier risks automatically captured by Omnea AI. Risk review time from 5 hours to 1–2 hours. The portal automates collection; human review focuses on the remediation exceptions."

### Phase 4 (Supplier Intelligence)
- "Omnea launched the industry's first procurement MCP Server on April 30, 2026 — read-only natural language queries against Omnea data from any AI tool. I built the local equivalent."
- "The renewal module solves a real problem: renewals happen by default because nobody cancelled. 90/60/30-day alerts with automatic workflow launch make renewal an active decision, not an oversight."
- "The supplier 360 profile is Omnea's repositioning: from procurement workflow tool to system of record for supplier intelligence. Identity, risk, certs, contracts, spend, all in one place — built automatically from the procurement process."

---

## Repository Structure After All Phases

```
procurement-intake-agent/
├── backend/
│   ├── models.py                        # + ApprovalStepORM, SupplierAssessmentORM, SupplierRecordORM
│   ├── workflow_config.json             # NEW: configurable approval rules
│   ├── main.py                          # UPDATED: register approvals, supplier_portal, suppliers routers
│   ├── services/
│   │   ├── _groq_utils.py               # existing — get_client(), call_with_retry(), MODEL
│   │   ├── intake_agent.py              # existing — UPDATED: calls generate_approval_steps() after save
│   │   ├── risk_scorer.py               # existing
│   │   ├── policy_engine.py             # existing
│   │   ├── approval_engine.py           # NEW: generate_approval_steps(), advance_workflow()
│   │   ├── supplier_portal_service.py   # NEW: recalculate_risk_after_assessment(), generate_remediation_tasks()
│   │   └── supplier_intelligence.py     # NEW: compute_renewal_status(), supplier_record_to_dict(), query_supplier_portfolio()
│   └── routers/
│       ├── requests.py                  # existing
│       ├── approvals.py                 # NEW: approval queue, decide, escalate routes
│       ├── supplier_portal.py           # NEW: portal context, submit routes
│       └── suppliers.py                 # NEW: SRM list, 360 profile, renewals, NL query routes
├── frontend/
│   ├── chat.html                        # existing + renewal pre-fill detection
│   ├── dashboard.html                   # ENHANCED: Requests + Approvals + Suppliers tabs
│   └── supplier_portal.html             # NEW: supplier-facing adaptive assessment page
└── implementation_plan/
    ├── 00_overview.md                   # this file
    ├── 01_phase2_approval_orchestration.md
    ├── 02_phase3_supplier_portal.md
    └── 03_phase4_supplier_intelligence.md
```
