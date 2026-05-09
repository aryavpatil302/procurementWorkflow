# Phase 3: Supplier Portal & Mini TPRM

## Omnea Product Mapping

| POC Component | Omnea Product | URL |
|--------------|--------------|-----|
| `supplier_portal.html` — magic-link style, no login required | Supplier Portal (magic link, no account, Trust Center URL, colleague delegation) | `omnea.co/products/supplier-portal` |
| Adaptive questionnaire depth (basic / standard / deep) | TPRM (adaptive questionnaires, proportional assessment) | `omnea.co/products/third-party-risk-management` |
| `SupplierAssessmentORM` — supplier-side responses | TPRM data model: certifications, data handling, security architecture | `omnea.co/products/third-party-risk-management` |
| Residual risk recalculation after cert declaration | Omnea's two-layer risk model: inherent → residual | Part of TPRM |
| Remediation task panel | Omnea remediation workflow trigger | TPRM remediation flows |
| `SupplierRecordORM` — created on assessment submit | Supplier 360 profile: unified identity, risk, certs, contracts | SRM + TPRM |

**Case Studies Referenced:**
- **Reach plc**: "70% of supplier risks automatically captured by Omnea AI" — risk review time from 5 hours to 1–2 hours; maverick spend reduced from 30% to 5%
- Supported compliance frameworks: SOC 2 (Types I and II), ISO 27001, DORA, GDPR, NIS2, Cyber Essentials, ISO 27017, ISO 27701, SOX, LkSG

---

## What This Phase Builds

When a request is fully approved (Phase 2 → `status = approved`), a "Send Supplier Assessment" button appears on the Requests tab. Clicking it opens `supplier_portal.html?id={request_id}`, simulating the supplier receiving a magic link — no login, no account creation required.

The portal renders an adaptive questionnaire whose depth (`basic` / `standard` / `deep`) is already stored in `questionnaire_depth` on `ProcurementRequestORM` from Phase 1 policy evaluation. On submission:
1. Certifications declared by the supplier are fed through the existing `compute_residual_risk()` function
2. The residual risk score is updated on the `ProcurementRequestORM`
3. Remediation tasks are surfaced for any outstanding gaps
4. A `SupplierRecordORM` is created — the canonical supplier profile used in Phase 4

**Demo scenario (Workday):** `questionnaire_depth = deep` because personal_data + spend > £50k. All 5 sections are shown. Supplier declares ISO 27001 + SOC 2 Type II → residual risk drops from HIGH to MEDIUM.

---

## 1. New DB Model: `SupplierAssessmentORM`

Add to `backend/models.py`. Note: `_new_id` and `_now` helpers, `Base`, `Column`, `String`, `Boolean`, `Float`, `Text`, `DateTime`, `JSON`, and `ForeignKey` are already imported in `models.py`. Add `SupplierAssessmentORM` as a new class.

```python
class SupplierAssessmentORM(Base):
    __tablename__ = "supplier_assessments"

    id = Column(String, primary_key=True, default=_new_id)
    request_id = Column(String, ForeignKey("procurement_requests.id"), nullable=False, unique=True, index=True)

    # Company identity (Section 1 — always shown)
    supplier_name = Column(String, nullable=True)
    legal_name = Column(String, nullable=True)
    registered_address = Column(Text, nullable=True)
    company_number = Column(String, nullable=True)
    vat_number = Column(String, nullable=True)
    primary_contact_name = Column(String, nullable=True)
    primary_contact_title = Column(String, nullable=True)
    primary_contact_email = Column(String, nullable=True)
    trust_center_url = Column(String, nullable=True)

    # Certifications & Compliance (Section 2 — always shown)
    certifications_held = Column(JSON, nullable=True)   # list[str]
    last_audit_date = Column(String, nullable=True)

    # Data Handling (Section 3 — shown if data_access != 'none')
    data_storage_location = Column(String, nullable=True)   # "UK only" | "EU" | "US" | "Global"
    uses_subprocessors = Column(Boolean, nullable=True)
    encryption_at_rest = Column(Boolean, nullable=True)
    encryption_in_transit = Column(Boolean, nullable=True)
    data_retention_policy = Column(Text, nullable=True)     # standard + deep only

    # Financial & Insurance (Section 4 — standard or deep)
    annual_revenue_range = Column(String, nullable=True)    # "<£1M" | "£1–10M" | etc.
    has_cyber_insurance = Column(Boolean, nullable=True)
    cyber_insurance_coverage = Column(String, nullable=True)
    has_data_breach_24m = Column(Boolean, nullable=True)

    # Security Architecture (Section 5 — deep only)
    has_ciso = Column(Boolean, nullable=True)
    last_pentest_date = Column(String, nullable=True)
    has_incident_response_plan = Column(Boolean, nullable=True)
    security_incidents_24m = Column(Boolean, nullable=True)
    security_incidents_detail = Column(Text, nullable=True)
    compliance_concerns = Column(Text, nullable=True)

    # Portal lifecycle
    portal_status = Column(String, nullable=False, default="pending")  # pending | in_progress | submitted

    submitted_at = Column(DateTime, nullable=True)

    # Risk recalculation after submission
    updated_risk_score = Column(Float, nullable=True)
    updated_risk_label = Column(String, nullable=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
```

---

## 2. New DB Model: `SupplierRecordORM`

Add to `backend/models.py`. Created on assessment submission. Becomes the canonical supplier profile for Phase 4.

```python
class SupplierRecordORM(Base):
    __tablename__ = "supplier_records"

    id = Column(String, primary_key=True, default=_new_id)

    # Identity
    supplier_name = Column(String, nullable=False, index=True)
    supplier_website = Column(String, nullable=True)
    category = Column(String, nullable=True)
    legal_name = Column(String, nullable=True)
    registered_address = Column(Text, nullable=True)
    company_number = Column(String, nullable=True)

    # Risk profile
    risk_tier = Column(String, nullable=True)           # low | medium | high | critical
    inherent_risk_score = Column(Float, nullable=True)
    residual_risk_score = Column(Float, nullable=True)
    certifications = Column(JSON, nullable=True)        # list[str]

    # Contract details
    relationship_owner = Column(String, nullable=True)  # requester_name from intake
    contract_value = Column(Float, nullable=True)       # spend_amount from intake
    contract_start_date = Column(DateTime, nullable=True)
    contract_expiry_date = Column(DateTime, nullable=True)

    # Renewal management (Phase 4) — computed live, stored as write-through cache
    renewal_status = Column(String, nullable=True)      # active | due_90 | due_60 | due_30 | overdue | no_expiry

    # Assessment lifecycle
    assessment_status = Column(String, nullable=True)   # pending | in_progress | completed

    # Contact
    primary_contact_name = Column(String, nullable=True)
    primary_contact_email = Column(String, nullable=True)
    primary_contact_title = Column(String, nullable=True)

    # Geography + data
    geography = Column(String, nullable=True)
    data_access = Column(String, nullable=True)

    first_engaged = Column(DateTime, nullable=True)
    last_reviewed = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
```

---

## 3. `backend/services/supplier_portal_service.py` — Full File

Create this file. It contains `recalculate_risk_after_assessment()` and `generate_remediation_tasks()`. Kept separate from the router for testability.

```python
"""
Supplier portal business logic.

recalculate_risk_after_assessment() — reuses compute_residual_risk() from risk_scorer.py
generate_remediation_tasks()        — checks for outstanding risk gaps post-submission
"""

from backend.models import ProcurementRequestORM, SupplierAssessmentORM
from backend.services.risk_scorer import compute_residual_risk


def recalculate_risk_after_assessment(
    request: ProcurementRequestORM,
    assessment: SupplierAssessmentORM,
) -> tuple[float, str]:
    """
    Recalculate residual risk using certifications declared by the supplier.
    Delegates to the existing compute_residual_risk() which returns (score, label).
    risk_scorer._label() thresholds: >=0.75=critical, >=0.50=high, >=0.25=medium, else low.
    """
    certs = assessment.certifications_held or []
    new_residual, new_label = compute_residual_risk(
        inherent_score=request.risk_score,
        certifications=certs,
    )
    return new_residual, new_label


def generate_remediation_tasks(
    assessment: SupplierAssessmentORM,
    request: ProcurementRequestORM,
    residual_risk_score: float,
    risk_label: str,
) -> list[dict]:
    """
    Return a list of remediation tasks if risk is medium or above.
    Each task: { type, severity, description, action }
    Maps to Omnea's remediation workflow trigger.
    """
    tasks = []
    certs = set(assessment.certifications_held or [])

    if risk_label in ("HIGH", "CRITICAL", "MEDIUM"):
        if "SOC 2 Type II" not in certs and request.data_access in ("personal_data", "confidential", "internal"):
            tasks.append({
                "type": "missing_cert",
                "severity": "high",
                "description": "SOC 2 Type II not declared",
                "action": "Request SOC 2 Type II audit report from supplier",
            })
        if "ISO 27001" not in certs and request.spend_amount and request.spend_amount > 10000:
            tasks.append({
                "type": "missing_cert",
                "severity": "medium",
                "description": "ISO 27001 certification not declared",
                "action": "Request ISO 27001 certificate or ISMS documentation",
            })

    if not assessment.has_cyber_insurance and request.spend_amount and request.spend_amount > 25000:
        tasks.append({
            "type": "insurance",
            "severity": "high",
            "description": "Cyber liability insurance not declared",
            "action": "Flag to security team — request evidence of cyber insurance coverage",
        })

    if request.data_access in ("personal_data", "confidential"):
        if assessment.encryption_at_rest is False:
            tasks.append({
                "type": "security_control",
                "severity": "high",
                "description": "Data not encrypted at rest",
                "action": "Escalate to IT Security — contractual encryption requirement must be met",
            })
        if assessment.encryption_in_transit is False:
            tasks.append({
                "type": "security_control",
                "severity": "high",
                "description": "Data not encrypted in transit",
                "action": "Escalate to IT Security — TLS/HTTPS requirement must be confirmed",
            })

    if (request.data_access == "personal_data"
            and request.geography in ("EU", "Global", "US")
            and assessment.data_storage_location not in ("UK only", "EU")):
        tasks.append({
            "type": "gdpr",
            "severity": "high",
            "description": "Cross-border personal data transfer to non-adequate country",
            "action": "Legal team to confirm Article 46 transfer mechanism (SCCs or BCRs)",
        })

    if assessment.security_incidents_24m:
        tasks.append({
            "type": "incident_history",
            "severity": "medium",
            "description": "Security incidents reported in last 24 months",
            "action": "Request full incident report and remediation evidence from supplier",
        })

    if assessment.has_incident_response_plan is False and risk_label in ("HIGH", "CRITICAL"):
        tasks.append({
            "type": "security_control",
            "severity": "medium",
            "description": "No formal incident response plan declared",
            "action": "Request incident response plan documentation",
        })

    return tasks
```

---

## 4. Register Router in `backend/main.py`

Add after the Phase 2 approvals router registration:

```python
# Add to imports:
from backend.routers import supplier_portal as supplier_portal_router

# Add after existing include_router calls:
app.include_router(supplier_portal_router.router)
```

---

## 5. `backend/routers/supplier_portal.py` — Full File

```python
"""
Supplier portal router.

Routes (all under prefix="/supplier-portal"):
  GET  /supplier-portal/{request_id}         — return portal context (depth, risk, status)
  POST /supplier-portal/{request_id}/submit  — submit assessment, recalculate risk, create supplier record
  GET  /supplier-portal/{request_id}/status  — return current portal status
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    ProcurementRequestORM,
    SupplierAssessmentORM,
    SupplierRecordORM,
)
from backend.services.supplier_portal_service import (
    generate_remediation_tasks,
    recalculate_risk_after_assessment,
)

router = APIRouter(prefix="/supplier-portal", tags=["supplier-portal"])


# ── Get portal context ────────────────────────────────────────────────────────

@router.get("/{request_id}")
def get_portal_context(request_id: str, db: Session = Depends(get_db)):
    """
    Return the data the supplier portal page needs to render.
    Creates a draft assessment row if none exists yet.
    """
    request = db.query(ProcurementRequestORM).filter_by(id=request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    assessment = db.query(SupplierAssessmentORM).filter_by(request_id=request_id).first()

    time_estimate = {"basic": 5, "standard": 10, "deep": 20}.get(
        request.questionnaire_depth or "standard", 10
    )

    if not assessment:
        assessment = SupplierAssessmentORM(
            request_id=request_id,
            supplier_name=request.supplier_name,
            portal_status="in_progress",
        )
        db.add(assessment)
        db.commit()
        db.refresh(assessment)

    return {
        "request_id": request_id,
        "supplier_name": request.supplier_name,
        "supplier_website": request.supplier_website,
        "category": request.category,
        "data_access": request.data_access,
        "questionnaire_depth": request.questionnaire_depth or "standard",
        "time_estimate_minutes": time_estimate,
        "risk_score": request.risk_score,
        "risk_label": request.risk_label,
        "residual_risk_score": request.residual_risk_score,
        "geography": request.geography,
        "portal_status": assessment.portal_status,
        "assessment_id": assessment.id,
        "submitted_at": assessment.submitted_at.isoformat() if assessment.submitted_at else None,
    }


# ── Submit assessment ─────────────────────────────────────────────────────────

class AssessmentSubmission(BaseModel):
    # Company identity
    legal_name: Optional[str] = None
    registered_address: Optional[str] = None
    company_number: Optional[str] = None
    vat_number: Optional[str] = None
    primary_contact_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    primary_contact_email: Optional[str] = None
    trust_center_url: Optional[str] = None

    # Certifications
    certifications_held: Optional[List[str]] = Field(default_factory=list)
    last_audit_date: Optional[str] = None

    # Data handling
    data_storage_location: Optional[str] = None
    uses_subprocessors: Optional[bool] = None
    encryption_at_rest: Optional[bool] = None
    encryption_in_transit: Optional[bool] = None
    data_retention_policy: Optional[str] = None

    # Financial & insurance
    annual_revenue_range: Optional[str] = None
    has_cyber_insurance: Optional[bool] = None
    cyber_insurance_coverage: Optional[str] = None
    has_data_breach_24m: Optional[bool] = None

    # Security architecture (deep only)
    has_ciso: Optional[bool] = None
    last_pentest_date: Optional[str] = None
    has_incident_response_plan: Optional[bool] = None
    security_incidents_24m: Optional[bool] = None
    security_incidents_detail: Optional[str] = None
    compliance_concerns: Optional[str] = None


@router.post("/{request_id}/submit")
def submit_assessment(
    request_id: str,
    body: AssessmentSubmission,
    db: Session = Depends(get_db),
):
    """
    Submit the supplier assessment.
    Steps:
    1. Update SupplierAssessmentORM with submitted data
    2. Recalculate residual risk using declared certifications
    3. Update ProcurementRequestORM.residual_risk_score and risk_label
    4. Generate remediation tasks
    5. Create or update SupplierRecordORM
    """
    request = db.query(ProcurementRequestORM).filter_by(id=request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    assessment = db.query(SupplierAssessmentORM).filter_by(request_id=request_id).first()
    if not assessment:
        assessment = SupplierAssessmentORM(
            request_id=request_id,
            supplier_name=request.supplier_name,
        )
        db.add(assessment)

    # Write submitted fields
    for field_name, value in body.dict(exclude_none=True).items():
        setattr(assessment, field_name, value)

    assessment.portal_status = "submitted"
    assessment.submitted_at = datetime.now(timezone.utc)

    # Recalculate risk — compute_residual_risk returns (score, label)
    new_residual, new_label = recalculate_risk_after_assessment(request, assessment)
    assessment.updated_risk_score = new_residual
    assessment.updated_risk_label = new_label

    # Snapshot old values before overwrite (for response diff)
    old_residual = request.residual_risk_score
    old_label = request.risk_label

    # Update the main request row
    request.residual_risk_score = new_residual
    request.risk_label = new_label

    db.commit()

    # Generate remediation tasks
    remediation_tasks = generate_remediation_tasks(
        assessment=assessment,
        request=request,
        residual_risk_score=new_residual,
        risk_label=new_label,
    )

    # Create or update SupplierRecordORM
    supplier_record = (
        db.query(SupplierRecordORM)
        .filter_by(supplier_name=request.supplier_name)
        .first()
    )
    if not supplier_record:
        supplier_record = SupplierRecordORM(
            supplier_name=request.supplier_name,
            first_engaged=request.created_at,
        )
        db.add(supplier_record)

    supplier_record.supplier_website = request.supplier_website
    supplier_record.category = request.category
    supplier_record.legal_name = body.legal_name
    supplier_record.registered_address = body.registered_address
    supplier_record.company_number = body.company_number
    supplier_record.risk_tier = new_label.lower()
    supplier_record.inherent_risk_score = request.risk_score
    supplier_record.residual_risk_score = new_residual
    supplier_record.certifications = body.certifications_held or []
    supplier_record.relationship_owner = request.requester_name
    supplier_record.contract_value = request.spend_amount
    supplier_record.geography = request.geography
    supplier_record.data_access = request.data_access
    supplier_record.assessment_status = "completed"
    supplier_record.primary_contact_name = body.primary_contact_name
    supplier_record.primary_contact_email = body.primary_contact_email
    supplier_record.primary_contact_title = body.primary_contact_title
    supplier_record.last_reviewed = datetime.now(timezone.utc)

    # Carry over contract_expiry_date from intake if set
    if request.contract_expiry_date:
        from datetime import datetime as dt
        try:
            supplier_record.contract_expiry_date = dt.fromisoformat(request.contract_expiry_date)
        except (ValueError, TypeError):
            pass

    db.commit()

    RISK_TIERS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    risk_improved = (
        new_label != old_label
        and old_label in RISK_TIERS
        and new_label in RISK_TIERS
        and RISK_TIERS.index(new_label) < RISK_TIERS.index(old_label)
    )

    return {
        "success": True,
        "assessment_id": assessment.id,
        "supplier_record_id": supplier_record.id,
        "risk_update": {
            "inherent_score": request.risk_score,
            "old_residual_score": old_residual,
            "new_residual_score": new_residual,
            "old_risk_label": old_label,
            "new_risk_label": new_label,
            "risk_improved": risk_improved,
        },
        "remediation_tasks": remediation_tasks,
    }


# ── Get portal status ─────────────────────────────────────────────────────────

@router.get("/{request_id}/status")
def get_portal_status(request_id: str, db: Session = Depends(get_db)):
    assessment = db.query(SupplierAssessmentORM).filter_by(request_id=request_id).first()
    if not assessment:
        return {"portal_status": "not_started"}
    return {
        "portal_status": assessment.portal_status,
        "submitted_at": assessment.submitted_at.isoformat() if assessment.submitted_at else None,
        "updated_risk_score": assessment.updated_risk_score,
        "updated_risk_label": assessment.updated_risk_label,
    }
```

---

## 6. Frontend: `supplier_portal.html`

Full page at `frontend/supplier_portal.html`. URL: `supplier_portal.html?id={request_id}`

### Page Structure

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  HEADER BANNER — Supplier name, assessment level, estimated time               │
└────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│  Section 1: Company Information (always shown)               │
│  Legal name, address, reg number, VAT, contact, Trust Center │
├──────────────────────────────────────────────────────────────┤
│  Section 2: Certifications & Compliance (always shown)       │
│  Checkboxes: SOC 2, ISO 27001, GDPR, DORA, NIS2, etc.       │
│  Last external audit date (standard + deep)                  │
├──────────────────────────────────────────────────────────────┤
│  Section 3: Data Handling (if data_access != 'none')         │
│  Storage location, subprocessors, encryption                 │
├──────────────────────────────────────────────────────────────┤
│  Section 4: Financial & Insurance (standard or deep)         │
│  Revenue range, cyber insurance, breach history              │
├──────────────────────────────────────────────────────────────┤
│  Section 5: Security Architecture (deep only)                │
│  CISO, pentest, incident response, known incidents           │
└──────────────────────────────────────────────────────────────┘
┌───────────────────────────────────┐
│  SUBMIT BUTTON                    │
└───────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────────────────┐
│  COMPLETION BANNER (shown after submit)                                        │
│  Risk update: HIGH → MEDIUM (inherent 0.780 → residual 0.620)                 │
│  Remediation tasks (if any)                                                   │
└────────────────────────────────────────────────────────────────────────────────┘
```

### Full HTML

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Supplier Due Diligence Assessment</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #F9FAFB; color: #111827; }

    .header-banner { background: #1E40AF; color: white; padding: 24px 32px; }
    .header-banner h1 { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
    .header-banner p { font-size: 14px; opacity: 0.85; }
    .header-meta { display: flex; gap: 24px; margin-top: 12px; font-size: 13px; flex-wrap: wrap; }
    .header-meta span { background: rgba(255,255,255,0.15); padding: 4px 10px; border-radius: 4px; }

    .container { max-width: 760px; margin: 32px auto; padding: 0 16px; }

    .section { background: white; border: 1px solid #E5E7EB; border-radius: 10px; margin-bottom: 20px; overflow: hidden; }
    .section-header { background: #F3F4F6; border-bottom: 1px solid #E5E7EB; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; }
    .section-header h2 { font-size: 15px; font-weight: 600; color: #374151; }
    .section-badge { font-size: 11px; color: #6B7280; background: white; padding: 2px 8px; border-radius: 4px; border: 1px solid #E5E7EB; }
    .section-body { padding: 20px; }

    .field-group { margin-bottom: 16px; }
    .field-group label { display: block; font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 6px; }
    .field-group input, .field-group select, .field-group textarea {
      width: 100%; padding: 9px 12px; border: 1px solid #D1D5DB; border-radius: 6px; font-size: 14px; color: #111827; background: white;
    }
    .field-group input:focus, .field-group select:focus, .field-group textarea:focus {
      outline: none; border-color: #1E40AF; box-shadow: 0 0 0 2px rgba(30,64,175,0.1);
    }
    .helper-text { font-size: 12px; color: #6B7280; margin-top: 4px; }

    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }

    .cert-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
    .cert-item { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 1px solid #E5E7EB; border-radius: 8px; cursor: pointer; transition: all 0.15s; }
    .cert-item:hover { border-color: #93C5FD; background: #EFF6FF; }
    .cert-item input[type="checkbox"] { width: 16px; height: 16px; accent-color: #1E40AF; }
    .cert-item.checked { border-color: #1E40AF; background: #EFF6FF; }
    .cert-name { font-size: 13px; font-weight: 500; }
    .cert-desc { font-size: 11px; color: #6B7280; }

    .radio-group { display: flex; flex-direction: column; gap: 8px; }
    .radio-item { display: flex; align-items: center; gap: 10px; font-size: 14px; cursor: pointer; }
    .radio-item input { width: 16px; height: 16px; accent-color: #1E40AF; }

    .yes-no { display: flex; gap: 12px; }
    .yn-btn { padding: 7px 20px; border: 1px solid #D1D5DB; border-radius: 6px; cursor: pointer; background: white; font-size: 14px; transition: all 0.15s; }
    .yn-btn.selected-yes { background: #D1FAE5; border-color: #10B981; color: #065F46; }
    .yn-btn.selected-no  { background: #FEE2E2; border-color: #EF4444; color: #991B1B; }

    .submit-area { text-align: center; padding: 24px 0; }
    .btn-submit { background: #1E40AF; color: white; border: none; padding: 14px 48px; font-size: 16px; font-weight: 600; border-radius: 8px; cursor: pointer; }
    .btn-submit:hover { background: #1D3A8A; }
    .btn-submit:disabled { background: #9CA3AF; cursor: default; }

    .completion-banner { background: #D1FAE5; border: 1px solid #10B981; border-radius: 10px; padding: 24px; text-align: center; margin-bottom: 20px; display: none; }
    .completion-banner h2 { color: #065F46; font-size: 18px; margin-bottom: 8px; }
    .completion-banner p { color: #047857; }

    .risk-update { display: flex; align-items: center; justify-content: center; gap: 16px; margin: 16px 0; }
    .risk-badge-large { padding: 6px 16px; border-radius: 6px; font-weight: 700; font-size: 16px; }
    .risk-arrow { font-size: 24px; color: #6B7280; }

    .remediation-panel { background: #FFF7ED; border: 1px solid #F97316; border-radius: 10px; padding: 20px; margin-bottom: 20px; display: none; }
    .remediation-panel h3 { color: #C2410C; font-size: 15px; margin-bottom: 12px; }
    .remediation-item { display: flex; gap: 12px; padding: 10px; background: white; border: 1px solid #FED7AA; border-radius: 6px; margin-bottom: 8px; }
    .remediation-icon { color: #F97316; font-size: 18px; }
    .remediation-text { flex: 1; }
    .remediation-desc { font-size: 13px; font-weight: 500; color: #374151; }
    .remediation-action { font-size: 12px; color: #6B7280; margin-top: 2px; }
    .severity-badge { font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px; margin-left: 6px; }
    .severity-high   { background: #FEE2E2; color: #991B1B; }
    .severity-medium { background: #FEF3C7; color: #92400E; }
  </style>
</head>
<body>

<div class="header-banner">
  <h1>Supplier Due Diligence Assessment</h1>
  <p id="header-subtitle">Loading assessment request...</p>
  <div class="header-meta">
    <span id="header-supplier">Loading...</span>
    <span id="header-depth">Loading...</span>
    <span id="header-time">Loading...</span>
  </div>
</div>

<div class="container">

  <div class="completion-banner" id="completion-banner">
    <h2>Assessment Submitted</h2>
    <p id="completion-text">Your risk profile has been updated.</p>
    <div class="risk-update" id="risk-update-display"></div>
  </div>

  <div class="remediation-panel" id="remediation-panel">
    <h3>Outstanding risk items requiring remediation:</h3>
    <div id="remediation-list"></div>
  </div>

  <form id="assessment-form" onsubmit="submitAssessment(event)">

    <!-- Section 1: Company Information -->
    <div class="section">
      <div class="section-header">
        <h2>1. Company Information</h2>
        <span class="section-badge">Required</span>
      </div>
      <div class="section-body">
        <div class="two-col">
          <div class="field-group">
            <label>Legal Company Name *</label>
            <input type="text" name="legal_name" required placeholder="e.g. Workday, Inc.">
          </div>
          <div class="field-group">
            <label>Company Registration Number</label>
            <input type="text" name="company_number" placeholder="e.g. 12345678">
          </div>
        </div>
        <div class="field-group">
          <label>Registered Address</label>
          <input type="text" name="registered_address" placeholder="Street, City, Country">
        </div>
        <div class="two-col">
          <div class="field-group">
            <label>VAT / Tax ID</label>
            <input type="text" name="vat_number" placeholder="e.g. GB123456789">
          </div>
          <div class="field-group">
            <label>Trust Center URL</label>
            <input type="url" name="trust_center_url" placeholder="https://trust.yourcompany.com">
            <div class="helper-text">Public security page — we may pre-populate fields from it.</div>
          </div>
        </div>
        <div class="three-col">
          <div class="field-group">
            <label>Primary Contact Name *</label>
            <input type="text" name="primary_contact_name" required>
          </div>
          <div class="field-group">
            <label>Title / Role</label>
            <input type="text" name="primary_contact_title" placeholder="e.g. Head of Security">
          </div>
          <div class="field-group">
            <label>Email Address *</label>
            <input type="email" name="primary_contact_email" required>
          </div>
        </div>
      </div>
    </div>

    <!-- Section 2: Certifications & Compliance -->
    <div class="section">
      <div class="section-header">
        <h2>2. Certifications &amp; Compliance</h2>
        <span class="section-badge">Required</span>
      </div>
      <div class="section-body">
        <div class="field-group">
          <label>Select all certifications your organisation currently holds:</label>
          <div class="cert-grid" id="cert-grid"></div>
        </div>
        <div class="field-group" id="audit-date-field" style="display:none">
          <label>Date of last external security audit</label>
          <input type="date" name="last_audit_date">
        </div>
      </div>
    </div>

    <!-- Section 3: Data Handling (conditional on data_access) -->
    <div class="section" id="section-data" style="display:none">
      <div class="section-header">
        <h2>3. Data Handling</h2>
        <span class="section-badge">Shown: data access declared</span>
      </div>
      <div class="section-body">
        <div class="field-group">
          <label>Where will company data be stored?</label>
          <div class="radio-group">
            <label class="radio-item"><input type="radio" name="data_storage_location" value="UK only"> UK only</label>
            <label class="radio-item"><input type="radio" name="data_storage_location" value="EU"> EU (including UK)</label>
            <label class="radio-item"><input type="radio" name="data_storage_location" value="US"> United States</label>
            <label class="radio-item"><input type="radio" name="data_storage_location" value="Global"> Global / Multiple regions</label>
          </div>
        </div>
        <div class="field-group">
          <label>Do you use subprocessors with access to company data?</label>
          <div class="yes-no" id="yn-subprocessors">
            <button type="button" class="yn-btn" onclick="setYN('subprocessors', true)">Yes</button>
            <button type="button" class="yn-btn" onclick="setYN('subprocessors', false)">No</button>
          </div>
          <input type="hidden" name="uses_subprocessors" id="val-subprocessors">
        </div>
        <div class="two-col">
          <div class="field-group">
            <label>Data encrypted at rest?</label>
            <div class="yes-no" id="yn-enc-rest">
              <button type="button" class="yn-btn" onclick="setYN('enc-rest', true)">Yes</button>
              <button type="button" class="yn-btn" onclick="setYN('enc-rest', false)">No</button>
            </div>
            <input type="hidden" name="encryption_at_rest" id="val-enc-rest">
          </div>
          <div class="field-group">
            <label>Data encrypted in transit?</label>
            <div class="yes-no" id="yn-enc-transit">
              <button type="button" class="yn-btn" onclick="setYN('enc-transit', true)">Yes</button>
              <button type="button" class="yn-btn" onclick="setYN('enc-transit', false)">No</button>
            </div>
            <input type="hidden" name="encryption_in_transit" id="val-enc-transit">
          </div>
        </div>
        <div class="field-group" id="retention-field" style="display:none">
          <label>Data Retention Policy</label>
          <textarea name="data_retention_policy" rows="2" placeholder="Describe your data retention and deletion policy..."></textarea>
        </div>
      </div>
    </div>

    <!-- Section 4: Financial & Insurance (standard or deep) -->
    <div class="section" id="section-financial" style="display:none">
      <div class="section-header">
        <h2>4. Financial &amp; Insurance</h2>
        <span class="section-badge">Standard assessment</span>
      </div>
      <div class="section-body">
        <div class="two-col">
          <div class="field-group">
            <label>Annual Revenue Range</label>
            <select name="annual_revenue_range">
              <option value="">Select range</option>
              <option>&lt;£1M</option>
              <option>£1–10M</option>
              <option>£10–50M</option>
              <option>£50–100M</option>
              <option>£100M+</option>
            </select>
          </div>
          <div class="field-group">
            <label>Do you hold cyber liability insurance?</label>
            <div class="yes-no" id="yn-cyber">
              <button type="button" class="yn-btn" onclick="setYN('cyber', true, 'cyber-coverage-row')">Yes</button>
              <button type="button" class="yn-btn" onclick="setYN('cyber', false, 'cyber-coverage-row')">No</button>
            </div>
            <input type="hidden" name="has_cyber_insurance" id="val-cyber">
          </div>
        </div>
        <div class="field-group" id="cyber-coverage-row" style="display:none">
          <label>Coverage amount</label>
          <input type="text" name="cyber_insurance_coverage" placeholder="e.g. £5,000,000">
        </div>
        <div class="field-group">
          <label>Any data breaches in the last 24 months?</label>
          <div class="yes-no" id="yn-breach">
            <button type="button" class="yn-btn" onclick="setYN('breach', true)">Yes</button>
            <button type="button" class="yn-btn" onclick="setYN('breach', false)">No</button>
          </div>
          <input type="hidden" name="has_data_breach_24m" id="val-breach">
        </div>
      </div>
    </div>

    <!-- Section 5: Security Architecture (deep only) -->
    <div class="section" id="section-security" style="display:none">
      <div class="section-header">
        <h2>5. Security Architecture</h2>
        <span class="section-badge">Deep assessment</span>
      </div>
      <div class="section-body">
        <div class="two-col">
          <div class="field-group">
            <label>Dedicated CISO or Head of Security?</label>
            <div class="yes-no" id="yn-ciso">
              <button type="button" class="yn-btn" onclick="setYN('ciso', true)">Yes</button>
              <button type="button" class="yn-btn" onclick="setYN('ciso', false)">No</button>
            </div>
            <input type="hidden" name="has_ciso" id="val-ciso">
          </div>
          <div class="field-group">
            <label>Date of last penetration test</label>
            <input type="date" name="last_pentest_date">
          </div>
        </div>
        <div class="two-col">
          <div class="field-group">
            <label>Formal incident response plan?</label>
            <div class="yes-no" id="yn-irp">
              <button type="button" class="yn-btn" onclick="setYN('irp', true)">Yes</button>
              <button type="button" class="yn-btn" onclick="setYN('irp', false)">No</button>
            </div>
            <input type="hidden" name="has_incident_response_plan" id="val-irp">
          </div>
          <div class="field-group">
            <label>Known security incidents in last 24 months?</label>
            <div class="yes-no" id="yn-incidents">
              <button type="button" class="yn-btn" onclick="setYN('incidents', true, 'incidents-detail-row')">Yes</button>
              <button type="button" class="yn-btn" onclick="setYN('incidents', false, 'incidents-detail-row')">No</button>
            </div>
            <input type="hidden" name="security_incidents_24m" id="val-incidents">
          </div>
        </div>
        <div class="field-group" id="incidents-detail-row" style="display:none">
          <label>Please provide details</label>
          <textarea name="security_incidents_detail" rows="3"></textarea>
        </div>
        <div class="field-group">
          <label>Any known compliance concerns or ongoing regulatory actions?</label>
          <textarea name="compliance_concerns" rows="2" placeholder="If none, leave blank"></textarea>
        </div>
      </div>
    </div>

    <div class="submit-area">
      <button type="submit" class="btn-submit" id="submit-btn">Submit Assessment</button>
      <p style="margin-top:12px;font-size:13px;color:#6B7280">Your responses are encrypted in transit and used only for due diligence purposes.</p>
    </div>

  </form>

</div>

<script>
  const CERTS_BY_DEPTH = {
    basic: [
      { value: "SOC 2 Type II",  label: "SOC 2 Type II",  desc: "AICPA security audit" },
      { value: "ISO 27001",      label: "ISO 27001",       desc: "Information security management" },
      { value: "GDPR compliant", label: "GDPR Compliant",  desc: "EU data protection" },
      { value: "None",           label: "None currently",  desc: "" },
    ],
    standard: [
      { value: "SOC 2 Type II",    label: "SOC 2 Type II",    desc: "AICPA security audit" },
      { value: "SOC 2 Type I",     label: "SOC 2 Type I",     desc: "Point-in-time assessment" },
      { value: "ISO 27001",        label: "ISO 27001",         desc: "Information security management" },
      { value: "ISO 27017",        label: "ISO 27017",         desc: "Cloud security controls" },
      { value: "GDPR compliant",   label: "GDPR Compliant",    desc: "EU data protection" },
      { value: "DORA",             label: "DORA",              desc: "Digital operational resilience" },
      { value: "Cyber Essentials", label: "Cyber Essentials",  desc: "UK NCSC baseline" },
      { value: "None",             label: "None currently",    desc: "" },
    ],
    deep: [
      { value: "SOC 2 Type II",    label: "SOC 2 Type II",    desc: "AICPA security audit" },
      { value: "SOC 2 Type I",     label: "SOC 2 Type I",     desc: "Point-in-time assessment" },
      { value: "ISO 27001",        label: "ISO 27001",         desc: "Information security management" },
      { value: "ISO 27017",        label: "ISO 27017",         desc: "Cloud security controls" },
      { value: "ISO 27701",        label: "ISO 27701",         desc: "Privacy information management" },
      { value: "GDPR compliant",   label: "GDPR Compliant",    desc: "EU data protection" },
      { value: "DORA",             label: "DORA",              desc: "Digital operational resilience" },
      { value: "NIS2",             label: "NIS2",              desc: "Network and information security" },
      { value: "Cyber Essentials", label: "Cyber Essentials",  desc: "UK NCSC baseline" },
      { value: "SOX",              label: "SOX",               desc: "Sarbanes-Oxley Act" },
      { value: "LkSG",             label: "LkSG",              desc: "German Supply Chain Act" },
      { value: "None",             label: "None currently",    desc: "" },
    ],
  };

  let requestId = null;
  let portalContext = null;

  async function init() {
    const params = new URLSearchParams(window.location.search);
    requestId = params.get('id');
    if (!requestId) {
      document.body.innerHTML = '<p style="padding:40px;color:red">No request ID provided.</p>';
      return;
    }
    try {
      portalContext = await fetch(`/supplier-portal/${requestId}`).then(r => r.json());
    } catch (e) {
      document.body.innerHTML = `<p style="padding:40px;color:red">Could not load assessment: ${e.message}</p>`;
      return;
    }

    if (portalContext.portal_status === 'submitted') {
      document.getElementById('assessment-form').style.display = 'none';
      showCompletionState({
        risk_update: {
          inherent_score: portalContext.risk_score,
          new_residual_score: portalContext.residual_risk_score,
          old_risk_label: portalContext.risk_label,
          new_risk_label: portalContext.risk_label,
          risk_improved: false,
        },
        remediation_tasks: [],
      });
      return;
    }

    renderHeader();
    renderCertGrid();
    showConditionalSections();
  }

  function renderHeader() {
    const depth = portalContext.questionnaire_depth || 'standard';
    const time = portalContext.time_estimate_minutes || 10;
    document.getElementById('header-subtitle').textContent =
      `Your organisation has been invited to complete a due diligence assessment. Please complete all required fields.`;
    document.getElementById('header-supplier').textContent = `Supplier: ${portalContext.supplier_name}`;
    document.getElementById('header-depth').textContent = `Level: ${depth.charAt(0).toUpperCase() + depth.slice(1)}`;
    document.getElementById('header-time').textContent = `Est. time: ${time} min`;
    document.title = `Due Diligence — ${portalContext.supplier_name}`;
  }

  function renderCertGrid() {
    const depth = portalContext.questionnaire_depth || 'standard';
    const certs = CERTS_BY_DEPTH[depth] || CERTS_BY_DEPTH.standard;
    document.getElementById('cert-grid').innerHTML = certs.map(c => `
      <label class="cert-item">
        <input type="checkbox" name="certifications_held" value="${c.value}"
          onchange="this.closest('.cert-item').classList.toggle('checked', this.checked)">
        <div>
          <div class="cert-name">${c.label}</div>
          ${c.desc ? `<div class="cert-desc">${c.desc}</div>` : ''}
        </div>
      </label>
    `).join('');
    if (depth !== 'basic') {
      document.getElementById('audit-date-field').style.display = 'block';
    }
  }

  function showConditionalSections() {
    const depth = portalContext.questionnaire_depth || 'standard';
    const dataAccess = portalContext.data_access || 'none';
    if (dataAccess !== 'none') {
      document.getElementById('section-data').style.display = 'block';
      if (depth !== 'basic') document.getElementById('retention-field').style.display = 'block';
    }
    if (depth === 'standard' || depth === 'deep') {
      document.getElementById('section-financial').style.display = 'block';
    }
    if (depth === 'deep') {
      document.getElementById('section-security').style.display = 'block';
    }
  }

  function setYN(field, value, revealId = null) {
    document.getElementById(`val-${field}`).value = value;
    const container = document.getElementById(`yn-${field}`);
    container.querySelectorAll('.yn-btn').forEach(btn => btn.classList.remove('selected-yes', 'selected-no'));
    container.querySelectorAll('.yn-btn')[value ? 0 : 1].classList.add(value ? 'selected-yes' : 'selected-no');
    if (revealId) document.getElementById(revealId).style.display = value ? 'block' : 'none';
  }

  async function submitAssessment(event) {
    event.preventDefault();
    const btn = document.getElementById('submit-btn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';

    const formData = new FormData(document.getElementById('assessment-form'));
    const payload = {};
    const certValues = [];

    for (const [key, value] of formData.entries()) {
      if (key === 'certifications_held') {
        certValues.push(value);
      } else if (value === 'true') {
        payload[key] = true;
      } else if (value === 'false') {
        payload[key] = false;
      } else if (value !== '') {
        payload[key] = value;
      }
    }
    payload.certifications_held = certValues.filter(v => v !== 'None');

    try {
      const response = await fetch(`/supplier-portal/${requestId}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Submission failed');
      document.getElementById('assessment-form').style.display = 'none';
      showCompletionState(data);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = 'Submit Assessment';
      alert(`Submission failed: ${e.message}`);
    }
  }

  function showCompletionState(data) {
    document.getElementById('completion-banner').style.display = 'block';

    const ru = data.risk_update;
    const riskColours = { LOW: '#10B981', MEDIUM: '#F59E0B', HIGH: '#EF4444', CRITICAL: '#7C2D12' };

    let completionText = `${portalContext?.supplier_name || 'Supplier'}'s risk profile has been updated.`;
    if (ru.risk_improved) {
      completionText += ` Risk level: ${ru.old_risk_label} → ${ru.new_risk_label} based on declared certifications.`;
    }
    document.getElementById('completion-text').textContent = completionText;

    document.getElementById('risk-update-display').innerHTML = `
      <span class="risk-badge-large" style="background:${riskColours[ru.old_risk_label]||'#9CA3AF'};color:white">${ru.old_risk_label}</span>
      <span class="risk-arrow">→</span>
      <span class="risk-badge-large" style="background:${riskColours[ru.new_risk_label]||'#9CA3AF'};color:white">${ru.new_risk_label}</span>
      <span style="font-size:13px;color:#6B7280">(${ru.inherent_score?.toFixed(3)} inherent → ${ru.new_residual_score?.toFixed(3)} residual)</span>
    `;

    const tasks = data.remediation_tasks || [];
    if (tasks.length > 0) {
      const panel = document.getElementById('remediation-panel');
      panel.style.display = 'block';
      document.getElementById('remediation-list').innerHTML = tasks.map(t => `
        <div class="remediation-item">
          <span class="remediation-icon">⚠</span>
          <div class="remediation-text">
            <div class="remediation-desc">
              ${t.description}
              <span class="severity-badge severity-${t.severity}">${t.severity.toUpperCase()}</span>
            </div>
            <div class="remediation-action">Action: ${t.action}</div>
          </div>
        </div>
      `).join('');
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  init();
</script>

</body>
</html>
```

---

## 7. What This Demonstrates (CSE Interview)

- **"Proportional assessment — the portal adapts its depth to the risk tier."** `questionnaire_depth` is already computed by `policy_engine.py` at intake time and stored on the request. The portal reads it and shows 4, 8, or 20+ fields accordingly. A low-risk SaaS tool gets a 5-minute form; Workday gets a full 20-minute deep assessment. Omnea's philosophy: questionnaire fatigue kills data quality — if every vendor gets the same 80-question assessment, response rates collapse.

- **"The magic link pattern."** The supplier receives a URL with `request_id`. No login, no account, no password. Click and complete. This is exactly the pattern Omnea's Supplier Portal uses — frictionless for the supplier means higher completion rates and faster due diligence.

- **"After the supplier declares SOC 2 and ISO 27001, the residual risk score updates immediately."** `compute_residual_risk()` already exists in `risk_scorer.py`. The portal submission feeds declared certifications through it and shows before/after. The cert credits are deterministic: SOC 2 = 0.08 reduction, ISO 27001 = 0.08, GDPR = 0.05, DORA = 0.05. That two-layer inherent-to-residual model reflects actual supplier posture, not just deal attributes.

- **"If residual risk is still high after certifications, the system surfaces remediation tasks automatically."** `generate_remediation_tasks()` checks for missing SOC 2, missing cyber insurance, unencrypted data, cross-border GDPR gaps, and incident history. Each task has a specific action — "request SOC 2 Type II audit report", "Legal team to confirm Article 46 transfer mechanism". Omnea calls this the remediation workflow.

- **"Compliance frameworks: SOC 2, ISO 27001, GDPR, DORA, NIS2, Cyber Essentials, ISO 27701, SOX, LkSG."** The cert grid for deep assessments shows the full Omnea compliance framework library. This signals enterprise breadth — regulated industries, financial services, German supply chain requirements.

- **"Reach plc: 70% of supplier risks automatically captured. Risk review time from 5 hours to 1–2 hours."** The portal automates collection. A human reviewer no longer emails the supplier, chases responses, copies into a spreadsheet, and manually scores. The system captures, scores, and surfaces exceptions. Human review focuses on the remediation tasks — the 30% that matters.

- **"The supplier record is created on assessment submission."** Every subsequent phase — renewal management, portfolio queries, 360 profiles — is powered by this record. The portal is the data collection event that makes Phase 4 possible.
