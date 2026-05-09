# Demo Scenarios — Procurement Intake Agent POC

Three scenarios designed to demonstrate every major feature of the intake agent.
Each one hits a distinct risk tier, approval path, and questionnaire depth.

---

## Before You Start

1. Stop the backend if running
2. Delete `procurement.db` (old schema — new columns won't exist otherwise)
3. Restart: `python -m uvicorn backend.main:app --port 8080 --reload`
4. Open `frontend/chat.html`

---

## Scenario 1 — Low Risk, Existing Approved Supplier

**Story:** A designer wants access to Figma. It's already on the approved list.

**What this demonstrates:**
- Trusted supplier deduplication (Omnea's shadow IT elimination)
- Basic questionnaire depth (streamlined form — fewer questions)
- Low risk score, fast approval path
- The agent redirecting rather than onboarding a duplicate

### Exact Script

**Message 1:**
```
I need Figma for my design work
```

**What to expect:** Aria flags that Figma is already approved under the Design team and asks if this is a separate purchase or a request for access to the existing licence.

**Message 2:**
```
It's a separate purchase — we need our own workspace for the brand team
```

**What to expect:** Aria accepts this and starts collecting missing fields. She should ask for spend, justification, and your details.

**Message 3:**
```
It's about £600 a year, subscription. Sarah Chen, Brand team. We need it for creating brand assets and style guides. EU deployment. Internal data access only.
```

**What to expect:** Aria surfaces no major policy flags (spend is under £10k). She should immediately present the summary and trigger the discovery screen.

### What the questionnaire shows
- Depth label: **Basic Review** (low spend, existing supplier category)
- Contract duration and budget fields are hidden
- Personal data and legal consultation fields are hidden
- Pre-filled: Figma, Software, Sarah Chen, Brand, subscription, £600, Internal, EU

### What the completion card shows
- Risk: LOW
- No residual risk reduction (no certs claimed)
- No policy flags
- Review depth: basic
- Approvers: manager only

---

## Scenario 2 — Medium Risk, New Supplier, Finance Approval Triggered

**Story:** Engineering wants to replace Jira with Linear. New supplier, £4,800/year subscription.

**What this demonstrates:**
- Replacement logic (Linear not Jira)
- is_new_supplier auto-set without asking
- Finance approval flag triggered at £10k+ (just below, but new supplier flag fires)
- Standard questionnaire depth
- Real-time policy surfacing during chat
- Discovery screen excluding Linear from alternatives

### Exact Script

**Message 1:**
```
I want to replace Jira with Linear for our engineering team
```

**What to expect:** Aria correctly identifies Linear as the new supplier (not Jira). She should note Jira is on the approved list and confirm this is a replacement purchase. She should automatically set is_new_supplier = true for Linear.

**Message 2:**
```
Yes, it's a new purchase. Linear is faster and more modern — we want to migrate our project tracking.
```

**What to expect:** Aria asks for spend, geography, data access, requester details.

**Message 3:**
```
£4,800 per year as a subscription. UK only. Internal data access. Alex Johnson, Engineering department. linear.app is the website.
```

**What to expect:** Aria notes "New supplier — enhanced due diligence required." and then presents the full summary. Discovery triggers automatically.

### What the questionnaire shows
- Depth label: **Standard Review** (new supplier)
- Contract duration, budget, personal data, legal consultation fields are visible
- Pre-filled: Linear, Software, Alex Johnson, Engineering, subscription, £4,800, UK, Internal
- Website pre-filled: linear.app
- "Yes, new supplier" radio pre-selected
- Discovery alternatives exclude Linear

### What the completion card shows
- Risk: MEDIUM (new supplier premium + software category)
- Policy flags: "New supplier — enhanced due diligence required."
- Review depth: standard
- Approvers: manager, it_security

---

## Scenario 3 — High Risk, Personal Data, EU Deployment, Multi-Flag

**Story:** The HR team wants to onboard Workday as a new HRIS platform. £75,000/year, processes employee personal data, EU deployment across UK and Germany.

**What this demonstrates:**
- Strategic/high spend threshold (Finance + Legal approvers surfaced live)
- Personal data access → GDPR + DPO review triggered in chat
- EU geography → GDPR Article 46 data transfer flag triggered
- Deep due diligence questionnaire depth
- Multiple policy flags stacking
- Residual risk reduction via certifications (Workday has SOC 2 + ISO 27001)
- Certification credit reducing residual score vs inherent score

### Exact Script

**Message 1:**
```
We want to onboard Workday as our new HR platform — it'll replace our current spreadsheet-based HR process
```

**What to expect:** Aria asks for spend, geography, and data access details.

**Message 2:**
```
It's £75,000 a year on a subscription basis
```

**What to expect:** Aria immediately says "Note: this requires Finance and Legal approval." — real-time policy surfacing.

**Message 3:**
```
It will process employee personal data — names, salaries, performance reviews. We're deploying across UK and Germany so the geography is EU and Global.
```

**What to expect:** Aria fires TWO more real-time policy flags:
1. "Note: personal data access triggers a Legal and DPO review under GDPR."
2. "Note: EU/Global data transfer — GDPR Article 46 transfer mechanisms apply."

**Message 4:**
```
Jamie Lee, HR department. Business justification is to centralise employee data and automate HR workflows across the company. workday.com is the site.
```

**What to expect:** Aria presents the full summary and triggers discovery.

### What the questionnaire shows
- Depth label: **Deep Due Diligence** (£75k + personal_data)
- ALL fields visible including security certifications and compliance concerns
- Pre-filled: Workday, Services, Jamie Lee, HR, subscription, £75,000, Global, personal_data
- "Yes, new supplier" pre-selected
- Personal data: "Yes" pre-selected (inferred from data_access)

**In the certifications field, select:**
- ✓ SOC 2
- ✓ ISO 27001
- ✓ GDPR compliant

### What the completion card shows
- Inherent risk: HIGH (e.g. 0.72) → after certs: MEDIUM (e.g. 0.51)
- The card shows the arrow reduction: HIGH → MEDIUM
- Policy flags (4 flags stacked):
  - "High spend — Finance and Legal approval required."
  - "Personal data access — Legal and DPO review required (GDPR)."
  - "EU/Global data transfer — GDPR Article 46 transfer mechanisms required."
  - "New supplier — enhanced due diligence required."
- Review depth: deep due diligence
- Approvers: cfo, dpo, finance, it_security, legal, manager

---

## Feature Coverage Matrix

| Feature | S1 Figma | S2 Linear | S3 Workday |
|---|:---:|:---:|:---:|
| Trusted supplier deduplication | ✓ | | |
| Replacement logic (replace X with Y) | | ✓ | |
| is_new_supplier auto-inference | | ✓ | ✓ |
| Real-time spend threshold alerts | | | ✓ |
| Real-time personal data alert | | | ✓ |
| Real-time GDPR data transfer alert | | | ✓ |
| Basic questionnaire depth | ✓ | | |
| Standard questionnaire depth | | ✓ | |
| Deep due diligence depth | | | ✓ |
| Discovery screen (supplier options) | ✓ | ✓ | ✓ |
| Discovery excludes requested supplier | | ✓ | |
| Finance approval routing | | | ✓ |
| DPO/Legal approval routing | | | ✓ |
| Certification credit (residual risk) | | | ✓ |
| Inherent → residual risk reduction | | | ✓ |
| Multi-flag policy stacking | | | ✓ |
| Audit trail written on submission | ✓ | ✓ | ✓ |

---

## What Each Scenario Proves You Know About Omnea

**Scenario 1** — You understand Omnea's deduplication feature (shadow IT elimination). VEED saved 1,695 hours of manual work partly because Omnea stops people from buying tools the company already has. You built the same logic.

**Scenario 2** — You understand Omnea Assist's natural language intake — "replace Jira with Linear" is exactly the kind of messy human input the intake AI has to handle cleanly. You also understand approval routing: new suppliers trigger IT Security review by design, not by accident.

**Scenario 3** — You understand Omnea's two-layer risk model (inherent vs. residual), the compliance framework integration (GDPR, DORA, SOC 2), and why proportional assessment depth matters — a £600 Figma add-on and a £75k HRIS platform with EU personal data should not get the same questionnaire. The fact that your implementation actually adjusts the form depth based on the risk profile mirrors Omnea's core product philosophy: proportional assessment, not one-size-fits-all.
