# POC Review — Procurement Intake Agent
## Strengths for the Client Support Engineer Role

---

### What this POC demonstrates well

**1. Product understanding, not just API knowledge**

The two-layer risk model (inherent vs. residual), the questionnaire depth tiers (basic / standard / deep due diligence), and the approval routing logic directly mirror Omnea's core value proposition. These weren't bolted on for show — they reflect the *why* behind the product. A CSE spends most of their time helping customers configure rules, troubleshoot edge cases, and explain why a decision was made. Demonstrating that you already think in those terms is the primary signal this role requires.

**2. Conversational intake designed around the right constraint**

The `VIEW_OPTIONS_READY` sentinel design — where the agent never auto-submits and the frontend detects completion intent from the AI's text — shows a specific architectural decision: the AI should guide, not decide. This maps directly to Omnea Assist's philosophy: the agent accelerates the human workflow, it doesn't replace the approver. Getting this right without it being spelled out demonstrates product instincts.

**3. Real-time policy surfacing mid-conversation**

Most candidates build a form. This POC surfaces GDPR Article 46 flags as soon as EU + personal data appears, mid-conversation. This is demonstrable live and maps to a specific Omnea capability that customers care about — the VEED case study explicitly mentions compliance coverage as a driver of adoption. It's a talking point that can be tied directly to customer outcomes.

**4. Discovery and trusted supplier deduplication**

The approved supplier deduplication (approved list → "we already have this, request access instead") is the shadow IT elimination use case Omnea sells. Having it built correctly — including category-aware alternatives, interactive supplier switching, and selective field-clearing when a different supplier is chosen — shows the intake workflow was understood deeply, not just the feature checklist.

**5. Adaptive questionnaire depth**

A £600 Figma add-on and a £75k HRIS platform with EU personal data get different form depths. The implementation adjusts dynamically based on spend and data access level, which mirrors Omnea's proportional assessment philosophy. This is a product decision, not just a UI nicety — and explaining it in a demo conversation is a strong CSE moment.

**6. Technical range appropriate for the role**

The stack (FastAPI, SQLAlchemy, LLM intake loop, vanilla JS frontend) is lean and readable. A CSE needs to understand and explain customer integrations, debug API calls, and sometimes modify demo environments under time pressure. This codebase is the right complexity: real enough to be credible, simple enough to demo without a runbook.

---

### Gaps — and why they're reasonable for a POC

| Gap | Why it's reasonable |
|---|---|
| No webhook / notification layer | Outbound Slack/email approval notifications aren't shown. A stub would take a few hours; the intake → risk → routing story is complete without it. |
| No multi-tenant isolation | All requests share one DB. The `company_id` column exists on the model, showing awareness of the concept — just not enforced at the query layer. |
| Approval workflow is routing metadata, not a live chain | `required_approvers` is stored and shown, but no approval UI exists. The intake agent's job is to determine *who* should approve and *why* — the workflow itself is downstream Omnea product territory (Workflows module). Stopping at the boundary is correct. |
| LLM field extraction brittleness | `parseFromSummary()` does defensive regex extraction, but a bad AI response will surface incomplete pre-fills. In production, fields would be validated server-side before display. Worth flagging proactively in a live demo. |

---

### What each demo scenario proves to a client

**Scenario 1 — Figma (Basic Review)**
You understand Omnea's deduplication feature. VEED saved 1,695 hours of manual work partly because Omnea stops people from buying tools the company already has. The POC implements the same deduplication logic and redirects users to request access rather than create a duplicate vendor record.

**Scenario 2 — Linear replacing Jira (Standard Review)**
You understand Omnea Assist's natural language intake. "Replace Jira with Linear" is exactly the kind of messy human input the intake AI has to handle cleanly. Replacement logic, is_new_supplier auto-inference, and IT Security approval routing all trigger correctly from a single conversational message.

**Scenario 3 — Workday (Deep Due Diligence)**
You understand Omnea's two-layer risk model, the GDPR / DORA compliance framework integration, and why proportional assessment depth matters. The cert credit reducing the inherent risk score (HIGH → MEDIUM after SOC 2 + ISO 27001) mirrors Omnea's residual risk capability and is directly demonstrable in the completion card.
