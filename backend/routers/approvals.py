"""
Approval workflow endpoints.

GET  /approvals/queue                      — all active steps (My Tasks view)
GET  /approvals/history                    — all decided/completed steps
GET  /requests/{request_id}/approval-steps — all steps for a request (timeline view)
GET  /approvals/{step_id}                  — single step with request context
POST /approvals/{step_id}/decide           — record approved/rejected/escalated/request_info decision
POST /approvals/{step_id}/escalate         — flag for escalation (sets escalated_at)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import AuditLogORM, ApprovalStepORM, ProcurementRequestORM
from backend.services.approval_engine import advance_workflow

router = APIRouter()

_VALID_DECISIONS = {"approved", "rejected", "escalated", "request_info"}
_TERMINAL = {"approved", "rejected", "escalated", "skipped"}


class DecideRequest(BaseModel):
    decision: str  # "approved" | "rejected" | "escalated" | "request_info"
    note: Optional[str] = None
    actor: Optional[str] = None


class EscalateRequest(BaseModel):
    note: Optional[str] = None
    actor: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/approvals/queue")
def get_approval_queue(db: Session = Depends(get_db)):
    """All active approval steps with their request context — the My Tasks table."""
    steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.status == "active")
        .order_by(ApprovalStepORM.created_at.asc())
        .all()
    )
    return [_serialize_step(s, include_request=True) for s in steps]


@router.get("/approvals/history")
def get_approval_history(db: Session = Depends(get_db)):
    """All decided/completed approval steps with request context."""
    steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.status.in_(list(_TERMINAL)))
        .order_by(ApprovalStepORM.decided_at.desc())
        .limit(200)
        .all()
    )
    return [_serialize_step(s, include_request=True) for s in steps]


@router.get("/requests/{request_id}/approval-steps")
def get_request_steps(request_id: str, db: Session = Depends(get_db)):
    """All approval steps for a request, ordered by group then creation time."""
    req = db.get(ProcurementRequestORM, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.request_id == request_id)
        .order_by(ApprovalStepORM.sequence_group, ApprovalStepORM.created_at)
        .all()
    )
    return [_serialize_step(s) for s in steps]


@router.get("/approvals/{step_id}")
def get_step(step_id: str, db: Session = Depends(get_db)):
    step = db.get(ApprovalStepORM, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    return _serialize_step(step, include_request=True)


@router.post("/approvals/{step_id}/decide")
def decide(step_id: str, body: DecideRequest, db: Session = Depends(get_db)):
    """
    Record a decision on an active approval step and advance the workflow.
    Accepted decisions: approved | rejected | escalated | request_info.
    request_info pauses the workflow pending more information.
    """
    step = db.get(ApprovalStepORM, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.status != "active":
        raise HTTPException(
            status_code=422,
            detail=f"Step is '{step.status}', not 'active'. Cannot record decision.",
        )
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Decision must be one of: {sorted(_VALID_DECISIONS)}",
        )

    now = datetime.now(timezone.utc)
    old_status = step.status
    step.status = body.decision
    step.decision_note = body.note
    step.decided_at = now
    if body.decision == "escalated":
        step.escalated_at = now

    _write_audit(
        db,
        request_id=step.request_id,
        action="approval_decision",
        field_name="status",
        old_value=old_status,
        new_value=body.decision,
        actor=body.actor or step.role_display_name,
        note=body.note,
    )
    db.commit()

    if body.decision != "request_info":
        advance_workflow(step.request_id, db)

    db.refresh(step)
    return _serialize_step(step)


@router.post("/approvals/{step_id}/escalate")
def escalate(step_id: str, body: EscalateRequest = EscalateRequest(), db: Session = Depends(get_db)):
    """
    Flag an active step as escalated (sets escalated_at for urgency banner display).
    Treated as non-blocking — the workflow advances past this step.
    """
    step = db.get(ApprovalStepORM, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.status != "active":
        raise HTTPException(
            status_code=422,
            detail=f"Step is '{step.status}', not 'active'. Cannot escalate.",
        )

    now = datetime.now(timezone.utc)
    step.status = "escalated"
    step.escalated_at = now
    step.decided_at = now
    if body.note:
        step.decision_note = body.note

    _write_audit(
        db,
        request_id=step.request_id,
        action="escalated",
        field_name="status",
        old_value="active",
        new_value="escalated",
        actor=body.actor or step.role_display_name,
        note=body.note,
    )
    db.commit()

    advance_workflow(step.request_id, db)
    db.refresh(step)
    return _serialize_step(step)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_audit(
    db: Session,
    request_id: str,
    action: str,
    field_name: str,
    old_value: str,
    new_value: str,
    actor: str,
    note: Optional[str] = None,
) -> None:
    db.add(AuditLogORM(
        request_id=request_id,
        action=action,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        actor=actor or "system",
    ))
    if note:
        db.add(AuditLogORM(
            request_id=request_id,
            action="decision_note",
            field_name="note",
            old_value=None,
            new_value=note,
            actor=actor or "system",
        ))


# ── Serializer ─────────────────────────────────────────────────────────────────

def _serialize_step(step: ApprovalStepORM, include_request: bool = False) -> dict:
    result = {
        "id": step.id,
        "request_id": step.request_id,
        "role": step.role,
        "role_display_name": step.role_display_name,
        "sequence_group": step.sequence_group,
        "status": step.status,
        "ai_summary": step.ai_summary,
        "approver_name": step.approver_name,
        "decision_note": step.decision_note,
        "decided_at": step.decided_at.isoformat() if step.decided_at else None,
        "escalated_at": step.escalated_at.isoformat() if step.escalated_at else None,
        "created_at": step.created_at.isoformat() if step.created_at else None,
        "updated_at": step.updated_at.isoformat() if step.updated_at else None,
    }
    if include_request and step.request:
        req = step.request
        result["request"] = {
            "supplier_name": req.supplier_name,
            "supplier_website": req.supplier_website,
            "spend_amount": req.spend_amount,
            "spend_type": req.spend_type,
            "category": req.category,
            "data_access": req.data_access,
            "risk_label": req.risk_label,
            "risk_score": req.risk_score,
            "residual_risk_score": req.residual_risk_score,
            "requester_name": req.requester_name,
            "department": req.department,
            "status": req.status,
            "policy_flags": req.policy_flags or [],
            "business_justification": req.business_justification,
            "service_description": req.service_description,
            "geography": req.geography,
            "contract_duration": req.contract_duration,
            "security_certifications": req.security_certifications or [],
            "cost_center": req.cost_center,
            "created_at": req.created_at.isoformat() if req.created_at else None,
        }
    return result
