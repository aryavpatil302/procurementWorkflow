# Phase 2: Approval Orchestration

## Omnea Product Mapping

| POC Component | Omnea Product | URL |
|--------------|--------------|-----|
| `workflow_config.json` — JSON rules driving step generation | Workflow Builder (drag-and-drop canvas, configurable rules, no-code) | `omnea.co/products/workflow-builder` |
| Sequential gate + parallel groups + escalation | Approval Workflows (parallel vs sequential, escalation, Slack/Teams) | `omnea.co/products/approval-workflows` |
| `ai_summary` stored per step at creation time | Omnea Analyze (Finance sees budget; Security sees certs; Legal sees GDPR) | Part of Omnea platform |
| Mock Slack approval panel in the card | VEED case study: approvals actioned directly from Slack notifications | Omnea Approval Workflows |

**Case Studies Referenced:**
- **Entrust**: "62.5% reduction in procurement cycle time" through automated routing with appropriate due diligence levels
- **VEED**: 1,695 hours of manual work eliminated; approvals actioned directly from Slack notifications — zero context switching; deployed in 3 weeks

---

## What This Phase Builds

After `_save_request()` writes the `ProcurementRequestORM`, a second function fires: `generate_approval_steps()`. This reads a JSON config file, evaluates each rule against the request, creates `ApprovalStepORM` rows, and calls `generate_role_summary()` for each step — storing the AI summary immediately so it is available without a live LLM call at review time.

From that point, the request enters a state machine driven by approver decisions. The state machine advances groups, triggers parallel execution, and finalises the request status.

This is the core of Omnea's value proposition: **configurable orchestration, not custom code.**

---

## 1. Workflow Configuration File

Create `backend/workflow_config.json`. This file is the single source of truth for all approval routing logic. Changing it changes the workflow — no Python edits required. This directly demonstrates Omnea's "configurability over custom code" philosophy.

```json
{
  "approval_rules": [
    {
      "always": true,
      "role": "manager",
      "sequence_group": 1,
      "description": "Manager approval always required as initial gate"
    },
    {
      "condition": {
        "category": ["Software", "Services", "Hardware"]
      },
      "role": "it_security",
      "sequence_group": 2,
      "description": "IT Security review for technology purchases"
    },
    {
      "condition": {
        "spend_amount_gt": 10000
      },
      "role": "finance",
      "sequence_group": 2,
      "description": "Finance review above £10,000"
    },
    {
      "condition": {
        "data_access": ["personal_data", "confidential"]
      },
      "role": "legal",
      "sequence_group": 2,
      "description": "Legal review for sensitive data access"
    },
    {
      "condition": {
        "data_access": "personal_data"
      },
      "role": "dpo",
      "sequence_group": 2,
      "description": "DPO review required under GDPR for personal data"
    },
    {
      "condition": {
        "spend_amount_gt": 50000
      },
      "role": "cfo",
      "sequence_group": 3,
      "description": "CFO approval for strategic spend above £50,000"
    }
  ],
  "escalation_minutes": 60,
  "parallel_group": 2
}
```

**Rule evaluation semantics:**
- `"always": true` — rule always applies regardless of request fields
- `"condition": { "field": "value" }` — field must equal value (string match)
- `"condition": { "field": ["v1", "v2"] }` — field must be one of the listed values
- `"condition": { "spend_amount_gt": N }` — `spend_amount > N`
- Multiple keys in `condition` are evaluated as AND (all must match)
- `sequence_group` controls ordering: group 1 runs first (sequential gate), group 2 runs in parallel, group 3 runs after group 2 completes

**Workday demo result:** Manager (group 1) → Finance + IT Security + Legal + DPO in parallel (group 2) → CFO (group 3). Five distinct approver perspectives, two of which are DPO and Legal for GDPR compliance — the richest possible approval path.

---

## 2. New DB Model: `ApprovalStepORM`

Add to `backend/models.py`. The `ai_summary` column stores the role-specific LLM summary generated at step creation time — not fetched on demand.

```python
# Add to backend/models.py — imports already exist in the file; add only the class

class ApprovalStepORM(Base):
    __tablename__ = "approval_steps"

    id = Column(String, primary_key=True, default=_new_id)
    request_id = Column(String, ForeignKey("procurement_requests.id"), nullable=False, index=True)

    # Role identity
    role = Column(String, nullable=False)
    role_display_name = Column(String, nullable=False)

    # Workflow position
    # 1 = sequential gate (must complete before group 2 starts)
    # 2 = parallel group (all run simultaneously after group 1 approves)
    # 3 = post-parallel (runs after all group 2 steps complete)
    sequence_group = Column(Integer, nullable=False)

    # State machine values:
    # pending  → waiting for previous group to complete
    # active   → ready for the assigned approver to act
    # approved → approver clicked approve
    # rejected → approver clicked reject (triggers request-level rejection)
    # escalated → approver did not respond within escalation_minutes
    status = Column(String, nullable=False, default="pending")

    # Pre-generated AI summary (Omnea Analyze) — stored at step creation time
    ai_summary = Column(Text, nullable=True)

    # Approver info — simulated for demo
    approver_name = Column(String, nullable=True)
    decision_note = Column(Text, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    escalated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
```

**Add `Integer` and `Text` to the existing imports in `models.py`:**

```python
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
```

---

## 3. `backend/services/approval_engine.py` — Full File

Create this file in full. It contains step generation, the workflow state machine, and role-specific AI summary generation.

```python
"""
Approval orchestration engine.

Reads workflow_config.json to determine which approval steps are required,
creates ApprovalStepORM rows, generates pre-stored AI summaries per role,
and implements the advance_workflow() state machine.
"""

import json
import os
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from backend.models import ApprovalStepORM, ProcurementRequestORM
from backend.services._groq_utils import MODEL, call_with_retry, get_client

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "workflow_config.json")

ROLE_DISPLAY_NAMES = {
    "manager":     "Line Manager",
    "finance":     "Finance Team",
    "it_security": "IT Security",
    "legal":       "Legal Team",
    "dpo":         "Data Protection Officer",
    "cfo":         "CFO",
}

# Simulated approver names — for demo purposes only
ROLE_APPROVERS = {
    "manager":     "Sarah Chen (Manager)",
    "finance":     "James Okafor (Finance)",
    "it_security": "Priya Mehta (IT Security)",
    "legal":       "Tom Whitfield (Legal)",
    "dpo":         "Anna Kowalski (DPO)",
    "cfo":         "David Harrington (CFO)",
}

ROLE_SUMMARY_PROMPTS = {
    "manager": (
        "You are summarising a procurement request for the requester's line manager. "
        "Focus on: who is requesting this, what business problem it solves, whether the "
        "justification is credible, and whether this aligns with team priorities. "
        "Be concise — 2 to 3 sentences maximum."
    ),
    "finance": (
        "You are summarising a procurement request for the Finance team. "
        "Focus on: total spend amount, annualised vs one-off, contract duration, "
        "total contract value, cost centre, and any budget risk. "
        "Be concise — 2 to 3 sentences maximum."
    ),
    "it_security": (
        "You are summarising a procurement request for the IT Security team. "
        "Focus on: what data the supplier will access, certifications held vs missing, "
        "overall risk score, deployment geography, and any technical risk flags. "
        "Be concise — 2 to 3 sentences maximum."
    ),
    "legal": (
        "You are summarising a procurement request for the Legal team. "
        "Focus on: whether personal or confidential data is involved, any GDPR implications, "
        "contract duration, cross-border data transfer risks, and policy flags raised. "
        "Be concise — 2 to 3 sentences maximum."
    ),
    "dpo": (
        "You are summarising a procurement request for the Data Protection Officer. "
        "Focus on: the specific categories of personal data involved, whether data crosses EU borders, "
        "GDPR Article 46 transfer mechanism requirements, and DPO obligations triggered. "
        "Be concise — 2 to 3 sentences maximum."
    ),
    "cfo": (
        "You are summarising a procurement request for the CFO. "
        "Focus on: total strategic spend, multi-year financial commitment, ROI or cost justification, "
        "and total financial exposure including renewal risk. "
        "Be concise — 2 to 3 sentences maximum."
    ),
}


def _load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def _evaluate_rule(rule: dict, request: ProcurementRequestORM) -> bool:
    """Return True if the rule applies to this request."""
    if rule.get("always"):
        return True

    condition = rule.get("condition", {})
    for field, value in condition.items():
        if field == "spend_amount_gt":
            if not (request.spend_amount and request.spend_amount > value):
                return False
        elif isinstance(value, list):
            field_val = getattr(request, field, None)
            if field_val not in value:
                return False
        else:
            field_val = getattr(request, field, None)
            if field_val != value:
                return False
    return True


def generate_role_summary(request: ProcurementRequestORM, role: str) -> str:
    """
    Generate a role-specific 2–3 sentence summary using Groq/Llama.
    Uses get_client() and call_with_retry() from _groq_utils — never imports Groq directly.
    Called at step creation time; result is stored in ApprovalStepORM.ai_summary.
    """
    client = get_client()

    system_prompt = ROLE_SUMMARY_PROMPTS.get(
        role,
        "Summarise this procurement request in 2–3 sentences."
    )

    certs = request.security_certifications or []
    flags = request.policy_flags or []

    request_context = (
        f"Supplier: {request.supplier_name}\n"
        f"Website: {request.supplier_website or 'not provided'}\n"
        f"Category: {request.category}\n"
        f"Spend Amount: £{request.spend_amount:,.2f} ({request.spend_type})\n"
        f"Contract Duration: {request.contract_duration or 'not specified'}\n"
        f"Geography: {request.geography}\n"
        f"Data Access: {request.data_access}\n"
        f"New Supplier: {'Yes' if request.is_new_supplier else 'No'}\n"
        f"Security Certifications: {', '.join(certs) if certs else 'None declared'}\n"
        f"Inherent Risk Score: {request.risk_score:.3f} ({request.risk_label})\n"
        f"Residual Risk Score: {request.residual_risk_score:.3f}\n"
        f"Policy Flags: {', '.join(flags) if flags else 'None'}\n"
        f"Requester: {request.requester_name} ({request.department})\n"
        f"Cost Centre: {request.cost_center or 'not specified'}\n"
        f"Business Justification: {request.business_justification}\n"
        f"Service Description: {request.service_description or 'not specified'}"
    )

    try:
        response = call_with_retry(
            client,
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Please summarise this procurement request:\n\n{request_context}"},
            ],
            temperature=0.3,
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        # Fallback: return a plain-text summary so the demo never breaks
        return (
            f"{request.supplier_name} — £{request.spend_amount:,.0f} {request.spend_type} "
            f"({request.category}). Data access: {request.data_access}. "
            f"Risk: {request.risk_label}. Requester: {request.requester_name} ({request.department})."
        )


def generate_approval_steps(
    request: ProcurementRequestORM,
    db: Session,
) -> List[ApprovalStepORM]:
    """
    Read workflow_config.json, evaluate each rule against the request, create
    ApprovalStepORM rows, and pre-generate AI summaries for each step.

    Group 1 steps start as 'active' (ready for the manager to act).
    Group 2+ steps start as 'pending' (blocked until previous group completes).

    Call this immediately after _save_request() commits the ProcurementRequestORM.
    The caller must also set request.status = 'in_review' and db.commit() after this returns.
    """
    config = _load_config()
    rules = config.get("approval_rules", [])

    steps = []
    for rule in rules:
        if not _evaluate_rule(rule, request):
            continue

        role = rule["role"]
        group = rule["sequence_group"]

        # Pre-generate the role-specific AI summary now, store it on the step row
        summary = generate_role_summary(request=request, role=role)

        step = ApprovalStepORM(
            request_id=request.id,
            role=role,
            role_display_name=ROLE_DISPLAY_NAMES.get(role, role.replace("_", " ").title()),
            sequence_group=group,
            status="active" if group == 1 else "pending",
            approver_name=ROLE_APPROVERS.get(role),
            ai_summary=summary,
        )
        db.add(step)
        steps.append(step)

    db.commit()
    return steps


def advance_workflow(request_id: str, db: Session) -> None:
    """
    Called after every approve/reject decision. Implements the state machine:

    1. If any step is 'rejected':
       → Set request.status = 'rejected'
       → Set all remaining 'pending' steps to 'skipped'

    2. If all steps in the current lowest active group are 'approved':
       → Find the next group (lowest sequence_group where status = 'pending')
       → Set those steps to 'active'

    3. If all non-skipped steps are 'approved' and no pending steps remain:
       → Set request.status = 'approved'
    """
    steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.request_id == request_id)
        .all()
    )
    request = db.query(ProcurementRequestORM).filter_by(id=request_id).first()

    if not steps or not request:
        return

    # 1. Check for any rejection
    if any(s.status == "rejected" for s in steps):
        request.status = "rejected"
        for s in steps:
            if s.status == "pending":
                s.status = "skipped"
        db.commit()
        return

    # 2. Find the current lowest group that has active steps
    active_groups = sorted(set(s.sequence_group for s in steps if s.status == "active"))
    pending_groups = sorted(set(s.sequence_group for s in steps if s.status == "pending"))

    if active_groups:
        current_group = active_groups[0]
        group_steps = [s for s in steps if s.sequence_group == current_group]

        if all(s.status == "approved" for s in group_steps):
            if pending_groups:
                # Activate the next pending group
                next_group = pending_groups[0]
                for s in steps:
                    if s.sequence_group == next_group and s.status == "pending":
                        s.status = "active"
                db.commit()
                return
            else:
                # No more pending groups — check overall completion
                decidable = [s for s in steps if s.status not in ("skipped", "pending")]
                if decidable and all(s.status == "approved" for s in decidable):
                    request.status = "approved"
                    db.commit()
                    return

    # Edge case: no active groups but steps remain pending
    non_skipped = [s for s in steps if s.status not in ("skipped", "pending")]
    if non_skipped and all(s.status == "approved" for s in non_skipped):
        if not any(s.status == "pending" for s in steps):
            request.status = "approved"
            db.commit()
```

---

## 4. Wire `generate_approval_steps()` into `_save_request()`

Edit `backend/services/intake_agent.py`. Add the import at the top of the file (after the existing imports) and the two lines at the end of `_save_request()`, replacing the existing `db.commit()` + `db.refresh(req)` block:

```python
# Add to imports at top of intake_agent.py:
from backend.services.approval_engine import generate_approval_steps

# Replace the final lines of _save_request() — after db.add(audit) — with:
    db.commit()
    db.refresh(req)

    # Generate approval steps and pre-compute AI summaries for each approver
    generate_approval_steps(request=req, db=db)

    # Transition status from 'pending' to 'in_review' now that steps exist
    req.status = "in_review"
    db.commit()
    db.refresh(req)
    return req
```

**Status transition:** `pending` → `in_review` happens here. This is the only place `in_review` is written. From this point, `advance_workflow()` drives the transition to `approved` or `rejected`.

---

## 5. Register Routers in `backend/main.py`

Add at the end of the imports and `include_router` calls in `backend/main.py`:

```python
# Add to imports:
from backend.routers import approvals as approvals_router

# Add after the existing include_router calls:
app.include_router(approvals_router.router)
```

Phase 3 and 4 routers are registered in their respective phase files.

---

## 6. `backend/routers/approvals.py` — Full File

All approval routes live under the `/approvals` prefix. Sub-paths for request-specific operations use `/approvals/steps/{request_id}/...` to avoid any ambiguity with the existing `/requests/{id}` routes in `requests.py`.

```python
"""
Approval orchestration router.

Route structure (all under prefix="/approvals"):
  GET  /approvals                                    — list all steps (filterable by role/status)
  GET  /approvals/steps/{request_id}                 — all steps for a request (timeline)
  POST /approvals/steps/{request_id}/{step_id}/decide — approve or reject a step
  POST /approvals/steps/{request_id}/{step_id}/escalate — escalate a step
  GET  /approvals/steps/{request_id}/summary/{role}  — return stored ai_summary for a role
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ApprovalStepORM, ProcurementRequestORM
from backend.services.approval_engine import advance_workflow

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    decision: str            # "approved" or "rejected"
    note: Optional[str] = None
    approver_name: Optional[str] = None


# ── List all approval steps ───────────────────────────────────────────────────

@router.get("")
def list_approvals(
    role: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Return all approval steps with their associated request context.
    Filter by role and/or status for the per-role approval queue.
    Example: GET /approvals?role=finance&status=active
    """
    query = db.query(ApprovalStepORM)
    if role:
        query = query.filter(ApprovalStepORM.role == role)
    if status:
        query = query.filter(ApprovalStepORM.status == status)

    steps = query.order_by(ApprovalStepORM.created_at.desc()).all()

    result = []
    for step in steps:
        request = db.query(ProcurementRequestORM).filter_by(id=step.request_id).first()
        result.append({
            "step_id": step.id,
            "request_id": step.request_id,
            "role": step.role,
            "role_display_name": step.role_display_name,
            "sequence_group": step.sequence_group,
            "status": step.status,
            "approver_name": step.approver_name,
            "ai_summary": step.ai_summary,
            "decision_note": step.decision_note,
            "decided_at": step.decided_at.isoformat() if step.decided_at else None,
            "escalated_at": step.escalated_at.isoformat() if step.escalated_at else None,
            "created_at": step.created_at.isoformat() if step.created_at else None,
            "request": {
                "supplier_name": request.supplier_name if request else None,
                "spend_amount": request.spend_amount if request else None,
                "category": request.category if request else None,
                "risk_label": request.risk_label if request else None,
                "risk_score": request.risk_score if request else None,
                "residual_risk_score": request.residual_risk_score if request else None,
                "data_access": request.data_access if request else None,
                "geography": request.geography if request else None,
                "requester_name": request.requester_name if request else None,
                "department": request.department if request else None,
                "status": request.status if request else None,
                "policy_flags": request.policy_flags if request else None,
                "security_certifications": request.security_certifications if request else None,
                "spend_type": request.spend_type if request else None,
                "contract_duration": request.contract_duration if request else None,
                "cost_center": request.cost_center if request else None,
                "business_justification": request.business_justification if request else None,
                "is_new_supplier": request.is_new_supplier if request else None,
                "created_at": request.created_at.isoformat() if request and request.created_at else None,
            } if request else None,
        })
    return result


# ── Steps for a specific request ──────────────────────────────────────────────

@router.get("/steps/{request_id}")
def get_request_steps(request_id: str, db: Session = Depends(get_db)):
    """
    Return all approval steps for a request, ordered by sequence_group.
    Used for the approval timeline visualisation.
    Includes ai_summary — no LLM call needed at read time.
    """
    steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.request_id == request_id)
        .order_by(ApprovalStepORM.sequence_group, ApprovalStepORM.created_at)
        .all()
    )
    return [
        {
            "step_id": s.id,
            "role": s.role,
            "role_display_name": s.role_display_name,
            "sequence_group": s.sequence_group,
            "status": s.status,
            "approver_name": s.approver_name,
            "ai_summary": s.ai_summary,
            "decision_note": s.decision_note,
            "decided_at": s.decided_at.isoformat() if s.decided_at else None,
        }
        for s in steps
    ]


# ── Get stored AI summary for a role ─────────────────────────────────────────

@router.get("/steps/{request_id}/summary/{role}")
def get_step_summary(request_id: str, role: str, db: Session = Depends(get_db)):
    """
    Return the pre-generated AI summary for this request + role combination.
    The summary was generated when the step was created — no LLM call here.
    Example: GET /approvals/steps/{request_id}/summary/finance
    """
    step = (
        db.query(ApprovalStepORM)
        .filter_by(request_id=request_id, role=role)
        .first()
    )
    if not step:
        raise HTTPException(status_code=404, detail=f"No approval step found for role '{role}'")
    return {
        "request_id": request_id,
        "role": role,
        "role_display_name": step.role_display_name,
        "summary": step.ai_summary or f"No summary available for {role}.",
    }


# ── Decide on a step ─────────────────────────────────────────────────────────

@router.post("/steps/{request_id}/{step_id}/decide")
def decide_step(
    request_id: str,
    step_id: str,
    body: DecisionRequest,
    db: Session = Depends(get_db),
):
    """
    Record an approve or reject decision for a step.
    After recording, advance_workflow() is called to progress the state machine.
    """
    step = db.query(ApprovalStepORM).filter_by(id=step_id, request_id=request_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.status != "active":
        raise HTTPException(status_code=400, detail=f"Cannot decide on a step with status '{step.status}'")
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be 'approved' or 'rejected'")

    step.status = body.decision
    step.decision_note = body.note
    step.decided_at = datetime.now(timezone.utc)
    if body.approver_name:
        step.approver_name = body.approver_name
    db.commit()

    advance_workflow(request_id=request_id, db=db)

    request = db.query(ProcurementRequestORM).filter_by(id=request_id).first()
    return {
        "step_id": step.id,
        "step_status": step.status,
        "request_status": request.status if request else None,
    }


# ── Escalate a step ───────────────────────────────────────────────────────────

@router.post("/steps/{request_id}/{step_id}/escalate")
def escalate_step(
    request_id: str,
    step_id: str,
    db: Session = Depends(get_db),
):
    """
    Mark a step as escalated (approver has not responded within SLA).
    In production this would notify the approver's manager via Slack/Teams.
    """
    step = db.query(ApprovalStepORM).filter_by(id=step_id, request_id=request_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.status != "active":
        raise HTTPException(status_code=400, detail="Only active steps can be escalated")

    step.status = "escalated"
    step.escalated_at = datetime.now(timezone.utc)
    db.commit()
    return {"step_id": step.id, "status": step.status}
```

---

## 7. Frontend: Enhanced `dashboard.html`

### Overall Layout

Replace the existing single-table layout with a tabbed interface. Three tabs: **Requests**, **Approvals**, **Suppliers** (Suppliers is a placeholder until Phase 4).

```html
<!-- Tab bar -->
<div class="tabs">
  <button class="tab active" data-tab="requests">Requests</button>
  <button class="tab" data-tab="approvals">Approvals</button>
  <button class="tab" data-tab="suppliers">Suppliers</button>
</div>

<div id="tab-requests" class="tab-content active"><!-- Requests tab --></div>
<div id="tab-approvals" class="tab-content"><!-- Approvals tab --></div>
<div id="tab-suppliers" class="tab-content"><!-- Phase 4 placeholder --></div>
```

Tab switching JS:

```javascript
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'approvals') loadApprovals(currentRole);
    if (tab.dataset.tab === 'requests') loadRequests();
    if (tab.dataset.tab === 'suppliers') { loadRenewalCalendar(); loadSuppliers(); }
  });
});
```

---

### Requests Tab (Enhanced)

Keep the existing table but add two new columns: **Approval Progress** and **Status**. Status badge uses the full transition matrix colours.

```javascript
const STATUS_COLOURS = {
  pending:   { bg: '#E5E7EB', text: '#374151' },
  in_review: { bg: '#DBEAFE', text: '#1D4ED8' },
  approved:  { bg: '#D1FAE5', text: '#065F46' },
  rejected:  { bg: '#FEE2E2', text: '#991B1B' },
};

async function renderApprovalProgress(requestId) {
  const steps = await fetch(`/approvals/steps/${requestId}`).then(r => r.json());
  const total = steps.length;
  const approved = steps.filter(s => s.status === 'approved').length;
  const pct = total > 0 ? Math.round((approved / total) * 100) : 0;
  return `
    <div class="progress-cell">
      <span>${approved}/${total} approved</span>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    </div>
  `;
}
```

**Approval Timeline** (expandable row showing the step sequence):

```javascript
function renderTimeline(steps) {
  const groups = {};
  steps.forEach(s => {
    if (!groups[s.sequence_group]) groups[s.sequence_group] = [];
    groups[s.sequence_group].push(s);
  });

  const icons = { approved: '✓', active: '⏳', pending: '🔒', rejected: '✗', escalated: '⚠', skipped: '—' };
  const colours = { approved: '#10B981', active: '#F59E0B', pending: '#9CA3AF', rejected: '#EF4444', escalated: '#F97316', skipped: '#D1D5DB' };

  const parts = Object.keys(groups).sort().map(g => {
    const dots = groups[g].map(s =>
      `<span style="color:${colours[s.status] || '#9CA3AF'}">${icons[s.status] || '○'} ${s.role_display_name}</span>`
    ).join(' | ');
    return groups[g].length > 1 ? `[${dots}]` : dots;
  });

  return `<div class="timeline">${parts.join(' → ')}</div>`;
}
```

**"Send Supplier Assessment" button** (visible only when status === 'approved'):

```javascript
if (request.status === 'approved') {
  actionsHtml += `<a href="supplier_portal.html?id=${request.id}" class="btn btn-primary btn-sm">
    Send Supplier Assessment
  </a>`;
}
```

---

### Approvals Tab (New)

**Role selector at top — no `privacy` role:**

```html
<div class="role-selector">
  <span>View as:</span>
  <button class="role-btn active" data-role="manager">Manager</button>
  <button class="role-btn" data-role="finance">Finance</button>
  <button class="role-btn" data-role="it_security">IT Security</button>
  <button class="role-btn" data-role="legal">Legal</button>
  <button class="role-btn" data-role="dpo">DPO</button>
  <button class="role-btn" data-role="cfo">CFO</button>
</div>
<div id="approvals-queue"></div>
```

**Loading the queue:**

```javascript
let currentRole = 'manager';

async function loadApprovals(role) {
  currentRole = role;
  const steps = await fetch(`/approvals?role=${role}&status=active`).then(r => r.json());
  const container = document.getElementById('approvals-queue');

  if (steps.length === 0) {
    container.innerHTML = `<div class="empty-state">No active items in your queue.</div>`;
    return;
  }
  container.innerHTML = steps.map(step => renderQueueItem(step)).join('');
}
```

**Queue item card — collapsed view:**

```javascript
function renderQueueItem(step) {
  const req = step.request;
  const riskColour = { LOW: '#10B981', MEDIUM: '#F59E0B', HIGH: '#EF4444', CRITICAL: '#7C2D12' };
  return `
    <div class="queue-item" id="queue-${step.step_id}">
      <div class="queue-item-header" onclick="toggleApprovalCard('${step.step_id}', '${step.request_id}', '${currentRole}', this)">
        <span class="supplier-name">${req.supplier_name}</span>
        <span class="spend">£${Number(req.spend_amount).toLocaleString()}</span>
        <span class="risk-badge" style="background:${riskColour[req.risk_label] || '#9CA3AF'};color:white">
          ${req.risk_label || 'UNKNOWN'}
        </span>
        <span class="requester">${req.requester_name} · ${req.department}</span>
        <span class="date">${new Date(req.created_at).toLocaleDateString('en-GB')}</span>
        <span class="chevron">▼</span>
      </div>
      <div class="approval-card" id="card-${step.step_id}" style="display:none"></div>
    </div>
  `;
}
```

**Expanded approval card** — note that `ai_summary` is already on the `step` object returned by `GET /approvals`. No separate summary fetch needed:

```javascript
async function toggleApprovalCard(stepId, requestId, role, headerEl) {
  const card = document.getElementById(`card-${stepId}`);
  if (card.style.display === 'block') {
    card.style.display = 'none';
    return;
  }

  card.innerHTML = `<div class="loading">Loading...</div>`;
  card.style.display = 'block';

  // Fetch full request details — step already has ai_summary from the list endpoint
  const step = await fetch(`/approvals?role=${role}&status=active`)
    .then(r => r.json())
    .then(steps => steps.find(s => s.step_id === stepId));

  const requestData = await fetch(`/requests/${requestId}`).then(r => r.json());

  const summary = step?.ai_summary || 'Summary not available.';
  const roleFields = getRoleFields(requestData, role);
  const slackCard = renderMockSlackCard(requestData, summary);

  card.innerHTML = `
    <div class="approval-card-inner">
      <div class="card-header">
        <h3>${requestData.supplier_name}</h3>
        <span>£${Number(requestData.spend_amount).toLocaleString()} · ${requestData.category} · ${requestData.risk_label} risk</span>
      </div>
      <div class="card-body">
        <div class="card-section summary-section">
          <h4>AI Summary — ${role.replace('_', ' ').toUpperCase()}</h4>
          <p class="ai-summary">${summary}</p>
        </div>
        <div class="card-section fields-section">
          <h4>Key Details</h4>
          ${roleFields}
        </div>
        <div class="card-section slack-section">
          <h4>Slack Notification Preview</h4>
          ${slackCard}
        </div>
      </div>
      <div class="card-actions">
        <textarea id="note-${stepId}" placeholder="Add a comment (optional)..." rows="2"></textarea>
        <div class="action-buttons">
          <button class="btn-approve" onclick="submitDecision('${stepId}', '${requestId}', 'approved')">✓ Approve</button>
          <button class="btn-reject" onclick="submitDecision('${stepId}', '${requestId}', 'rejected')">✗ Reject</button>
          <button class="btn-escalate" onclick="escalateStep('${stepId}', '${requestId}')">⚠ Escalate</button>
        </div>
      </div>
    </div>
  `;
}
```

**Role-specific field panels:**

```javascript
function getRoleFields(req, role) {
  const field = (label, value) =>
    value ? `<div class="field"><span class="label">${label}</span><span class="value">${value}</span></div>` : '';

  const tcv = req.spend_amount && req.contract_duration
    ? `£${(req.spend_amount * (parseInt(req.contract_duration) || 1)).toLocaleString()}`
    : 'N/A';

  const fieldSets = {
    manager: [
      field('Requester', `${req.requester_name} (${req.department})`),
      field('Supplier', req.supplier_name),
      field('Business Justification', req.business_justification),
      field('Service Description', req.service_description),
      field('New Supplier?', req.is_new_supplier ? 'Yes' : 'No'),
    ],
    finance: [
      field('Spend Amount', `£${Number(req.spend_amount).toLocaleString()}`),
      field('Spend Type', req.spend_type),
      field('Contract Duration', req.contract_duration),
      field('Estimated TCV', tcv),
      field('Cost Centre', req.cost_center),
      field('Department', req.department),
    ],
    it_security: [
      field('Data Access', req.data_access),
      field('Certifications Held', (req.security_certifications || []).join(', ') || 'None declared'),
      field('Inherent Risk', `${req.risk_score?.toFixed(3)} (${req.risk_label})`),
      field('Residual Risk', req.residual_risk_score?.toFixed(3)),
      field('Geography', req.geography),
      field('Policy Flags', (req.policy_flags || []).join(', ') || 'None'),
    ],
    legal: [
      field('Data Access', req.data_access),
      field('Geography', req.geography),
      field('Contract Duration', req.contract_duration),
      field('Policy Flags', (req.policy_flags || []).join(', ') || 'None'),
      field('GDPR Relevant?', req.data_access === 'personal_data' ? 'Yes — personal data involved' : 'No'),
    ],
    dpo: [
      field('Data Access', req.data_access),
      field('Geography', req.geography),
      field('Cross-border Transfer?', ['EU', 'Global', 'US'].includes(req.geography) ? 'Yes — Article 46 mechanism required' : 'No'),
      field('Certifications', (req.security_certifications || []).join(', ') || 'None declared'),
      field('Policy Flags', (req.policy_flags || []).join(', ') || 'None'),
    ],
    cfo: [
      field('Total Spend', `£${Number(req.spend_amount).toLocaleString()}`),
      field('Spend Type', req.spend_type),
      field('Contract Duration', req.contract_duration),
      field('Total Contract Value', tcv),
      field('Business Justification', req.business_justification),
    ],
  };

  return (fieldSets[role] || fieldSets.manager).join('');
}
```

**Mock Slack card** (the VEED story):

```javascript
function renderMockSlackCard(req, summary) {
  return `
    <div class="slack-card">
      <div class="slack-header">
        <span class="slack-app-name">Omnea</span>
        <span class="slack-time">just now</span>
      </div>
      <div class="slack-body">
        <div class="slack-title">New approval request: ${req.supplier_name}</div>
        <div class="slack-summary">${summary}</div>
        <div class="slack-meta">
          💰 £${Number(req.spend_amount).toLocaleString()} · ⚠ ${req.risk_label} risk · 📁 ${req.category}
        </div>
      </div>
      <div class="slack-actions">
        <button class="slack-btn slack-approve">✓ Approve</button>
        <button class="slack-btn slack-reject">✗ Reject</button>
        <button class="slack-btn slack-view">View Details</button>
      </div>
    </div>
  `;
}
```

**Submit decision:**

```javascript
async function submitDecision(stepId, requestId, decision) {
  const note = document.getElementById(`note-${stepId}`)?.value || '';
  const response = await fetch(`/approvals/steps/${requestId}/${stepId}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, note }),
  });
  const data = await response.json();

  await loadApprovals(currentRole);

  if (data.request_status === 'approved') {
    showToast('Request fully approved. Send supplier assessment from the Requests tab.', 'success');
  } else if (data.request_status === 'rejected') {
    showToast('Request rejected.', 'error');
  }
}
```

---

## 8. CSS Additions

Add to `dashboard.html` `<style>` section:

```css
/* Tabs */
.tabs { display: flex; gap: 4px; margin-bottom: 20px; border-bottom: 2px solid #E5E7EB; }
.tab { padding: 10px 20px; border: none; background: none; cursor: pointer; font-size: 14px; color: #6B7280; border-bottom: 2px solid transparent; margin-bottom: -2px; }
.tab.active { color: #1E40AF; border-bottom-color: #1E40AF; font-weight: 600; }
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Timeline */
.timeline { display: flex; align-items: center; gap: 8px; padding: 8px 0; font-size: 13px; flex-wrap: wrap; }

/* Progress bar */
.progress-cell { display: flex; flex-direction: column; gap: 4px; }
.progress-bar { width: 80px; height: 6px; background: #E5E7EB; border-radius: 3px; }
.progress-fill { height: 100%; background: #10B981; border-radius: 3px; transition: width 0.3s; }

/* Role selector */
.role-selector { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.role-btn { padding: 6px 12px; border: 1px solid #D1D5DB; border-radius: 6px; cursor: pointer; background: white; font-size: 13px; }
.role-btn.active { background: #1E40AF; color: white; border-color: #1E40AF; }

/* Queue item */
.queue-item { border: 1px solid #E5E7EB; border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
.queue-item-header { display: flex; align-items: center; gap: 16px; padding: 12px 16px; cursor: pointer; flex-wrap: wrap; }
.queue-item-header:hover { background: #F9FAFB; }

/* Approval card */
.approval-card-inner { padding: 16px; border-top: 1px solid #E5E7EB; }
.card-body { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin: 16px 0; }
.card-section h4 { font-size: 12px; font-weight: 600; color: #6B7280; text-transform: uppercase; margin-bottom: 8px; }
.ai-summary { font-size: 14px; color: #374151; line-height: 1.6; background: #F0FDF4; padding: 12px; border-radius: 6px; }
.field { margin-bottom: 8px; }
.field .label { font-size: 12px; color: #6B7280; display: block; }
.field .value { font-size: 14px; color: #111827; }

/* Slack mock card */
.slack-card { border: 1px solid #E5E7EB; border-left: 4px solid #4A154B; border-radius: 6px; padding: 12px; font-size: 13px; }
.slack-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; color: #6B7280; }
.slack-app-name { font-weight: 600; color: #4A154B; }
.slack-title { font-weight: 600; margin-bottom: 6px; }
.slack-summary { color: #374151; margin-bottom: 8px; line-height: 1.5; }
.slack-meta { color: #6B7280; margin-bottom: 10px; font-size: 12px; }
.slack-actions { display: flex; gap: 8px; }
.slack-btn { padding: 4px 10px; border: 1px solid #D1D5DB; border-radius: 4px; font-size: 12px; cursor: default; background: #F9FAFB; }
.slack-approve { color: #065F46; border-color: #10B981; }
.slack-reject { color: #991B1B; border-color: #EF4444; }

/* Action buttons */
.card-actions { border-top: 1px solid #E5E7EB; padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.action-buttons { display: flex; gap: 8px; }
.btn-approve  { background: #10B981; color: white; border: none; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-weight: 500; }
.btn-reject   { background: #EF4444; color: white; border: none; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-weight: 500; }
.btn-escalate { background: #F59E0B; color: white; border: none; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-weight: 500; }

/* Empty state */
.empty-state { text-align: center; padding: 40px; color: #9CA3AF; font-size: 14px; }
```

---

## 9. What This Demonstrates (CSE Interview)

- **"Omnea is not just AI chat; it's a configurable orchestration engine."** The `workflow_config.json` file drives all routing logic. Changing one JSON value changes the entire workflow — no code edits. That's what Omnea's Workflow Builder does with a drag-and-drop canvas. I built the engine underneath it.

- **"I modelled business rules as JSON configuration, not hardcoded Python."** The evaluator reads the config at runtime and applies conditions against structured request data. Finance appears because spend > £10,000. DPO appears because data_access = personal_data. CFO appears because spend > £50,000. The rules are transparent and auditable.

- **"Parallel approvals are the biggest throughput win."** Finance and IT Security review simultaneously in sequence group 2. That's weeks of sequential waiting compressed into one parallel review window. Entrust saw 62.5% reduction in procurement cycle time. VEED eliminated 1,695 hours of manual work.

- **"Each approver gets a role-specific AI summary — Omnea Analyze."** Finance sees budget impact and TCV. IT Security sees cert gaps and risk score. Legal sees GDPR flags. DPO sees Article 46 cross-border obligations. CFO sees total financial exposure. Same request, surfaced through each stakeholder's lens.

- **"AI summaries are pre-generated at step creation time."** When `generate_approval_steps()` runs, it calls the LLM once per step and stores the result in `ai_summary`. When the approver opens their card, it loads instantly. This is a deliberate design choice: demo speed matters, and on-demand LLM calls introduce latency that can break the flow.

- **"The sequential gate prevents premature parallel work."** The manager must approve before Finance, Legal, and IT Security are even notified. No email chains where IT Security reviews something the manager would have rejected in 30 seconds.

- **"VEED's approvals happen in Slack."** The mock Slack card in every approval panel demonstrates the integration story — the approver never has to open a procurement tool. In production Omnea, this Slack card is real and interactive.

- **"The state machine is deterministic."** Once intake produces structured data, every downstream routing decision is rules-based. The LLM (Groq/Llama) touches intake conversation and role summaries only — it never makes routing decisions. This is a design principle: AI augments, humans (and rules) decide.
