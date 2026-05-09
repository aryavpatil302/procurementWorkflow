# Competitive Review: Procurement Intake POC vs. Omnea

**Reviewer: Dr. Priya Nair, Procurement Technology Analyst**
**Date: May 5, 2026**

---

## 1. Feature Parity Scorecard

| Omnea Module | Status | Notes |
|---|---|---|
| **Intake & Orchestration** | ⚠️ Partially Implemented | Conversational intake: yes. Real-time policy surfacing: yes, but prompt-driven only. Duplicate detection: hardcoded name list in prompt — no fuzzy DB query. Multi-channel (Slack, email): not implemented. |
| **Workflow Builder** | ⚠️ Partially Implemented | Rules engine structurally correct — independent evaluation, set union semantics. Missing: no-code UI, SLA timers, escalation paths, mid-flight workflow mutation. Rules are hard-coded Python, not configurable by a non-developer. |
| **Supplier Portal + TPRM** | ❌ Not Implemented | `questionnaire_depth` computed and stored. No portal, no token-gated URL, no questionnaire delivery, no certification upload, no residual risk calculation. |
| **SRM** | ❌ Not Implemented | No supplier profiles. No `SupplierORM` entity. `supplier_name` is a plain string on the request row. |
| **Sourcing / RFx** | 🔲 Out of scope | — |
| **Continuous Risk Monitoring** | ❌ Not Implemented | Risk score computed entirely from requester-provided fields. No external signals. |
| **Omnea AI (Assist/Command/Analyze)** | ❌ Not Implemented | — |
| **MCP Server** | ❌ Not Implemented | Phase 1 scope |
| **Audit Trail** | ✅ Implemented | Written atomically on creation and status change. Limited to those two events — no mid-flight field-level tracking. |
| **Risk Scoring — Inherent** | ✅ Implemented | Correct model. Factors: spend, data access, category, new supplier. |
| **Risk Scoring — Residual** | ❌ Not Implemented | Correctly deferred to POC 2. |
| **Approval Routing** | ⚠️ Partially Implemented | Approvers computed and stored. No notification, no enforced identity, no sequential/parallel track support. |
| **Multi-tenancy** | ⚠️ Partially Implemented | `company_id` column with default `"default"`. No tenant isolation in queries. |

---

## 2. What the POC Gets Right

1. **Inherent/residual risk distinction is architecturally correct.** Separating `score_supplier()` (inherent) from residual (deferred) shows genuine TPRM understanding. Rare at POC stage.

2. **Two-tool architecture is the right abstraction.** `extract_state` (ephemeral, no side effects) vs. `submit_request` (permanent commit) directly mirrors Omnea's separation of conversational collection from workflow instantiation.

3. **Real-time policy surfacing is in the right place.** Flagging during conversation, not post-submission, is exactly Omnea's key UX differentiator.

4. **Policy engine evaluates all rules independently.** Set union semantics, no short-circuit. Matches Omnea's Workflow Builder behavior.

5. **`questionnaire_depth` taxonomy matches Omnea's language exactly.** Right values, right signals, ready to drive POC 2.

---

## 3. Critical Gaps (ranked by business impact)

**Gap 1: No actual approval enforcement.** `PATCH /requests/{id}/status` accepts any `actor` string. Any caller can approve a £500k request with `{"actor": "cfo@company.com"}`. *A procurement system that cannot enforce approvals is a data collection form.*

**Gap 2: No supplier entity.** `supplier_name` is a free-text string. Three spellings of "Salesforce" are three unrelated facts. Every downstream module requires a canonical `SupplierORM`. Adding it later requires rearchitecting the request creation path.

**Gap 3: Duplicate detection is prompt theater.** Hardcoded list of 10 brand names. Won't catch "Canva". Won't catch "Notion.so". Omnea's duplicate detection queries actual procurement records. Without a supplier entity and similarity query, duplicate purchases are approved invisibly.

**Gap 4: Risk score trusts requester self-report.** A requester can answer "none" for data access and get a low score for a high-risk supplier. Omnea's continuous monitoring provides the counterweight.

**Gap 5: No authentication = no audit integrity.** `AuditLogORM.actor` is a free-text string from the request body. Audit trail is inadmissible in any compliance context.

**Gap 6: No workflow state machine.** `status` is a string with no transition enforcement. Parallel approval tracks cannot be represented.

**Gap 7: In-memory sessions fail silently behind a load balancer.** Correctness problem, not just scaling.

**Gap 8: `questionnaire_depth` is a label pointing at a portal that doesn't exist.** Don't call this "TPRM-ready" — call it "TPRM-scoped."

---

## 4. Architectural Divergences

- **No supplier entity** → SRM, TPRM, renewal monitoring all impossible without migration
- **Hard-coded Python rules** → Workflow Builder requires a database-backed rules table with rule interpreter
- **Single `status` string** → Parallel approvals require `ApprovalStepORM` (one row per approver per request)
- **In-memory sessions** → Must externalize to Redis for multi-process deployment
- **Server blind to field state** — `extract_state` payload is discarded server-side; Omnea tracks field state for progress indicators and session resume

---

## 5. Terminology Fixes

| Current | Correct | Why |
|---|---|---|
| "supplier/vendor" | "supplier" only | "Vendor" is legacy; Omnea uses "supplier" exclusively |
| "approval routing" | "workflow orchestration" | Omnea's framing signals full lifecycle |
| "risk score" | "inherent risk score" | Residual distinction is load-bearing |
| `flags` in PolicyResult | `policy_flags` | Standardize to match column name |
| `recurring` vs `subscription` | Collapse or define precisely | These overlap and generate bad data |

---

## 6. Recommended Next Build: Supplier Portal + TPRM

Build the token-gated questionnaire and residual risk recalculation. Why:

- The intake POC's output (`questionnaire_depth = "deep_due_diligence"`) currently sits in a column and does nothing.
- The supplier portal makes it act: token-gated URL, adaptive questionnaire, certification uploads, residual risk recalculation.
- The inherent/residual delta ("inherent: 0.82, residual after SOC 2: 0.41") is Omnea's primary mechanism for reducing approver review time.
- Forces you to build `SupplierORM` — the correct architectural forcing function.
- VEED's "610+ weeks of supplier onboarding time saved" comes from this module.

> *"The POC collects data elegantly. It does not yet enforce anything. Closing that gap is the work of the next three POCs."*
