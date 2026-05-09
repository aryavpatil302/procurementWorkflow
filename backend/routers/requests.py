import json
import os
import uuid as _uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import AuditLogORM, ProcurementRequestORM
from backend.services._normalizers import normalize_category, normalize_data_access, normalize_spend_type
from backend.services.intake_agent import _save_request, chat
from backend.services.risk_scorer import compute_residual_risk

router = APIRouter()

# ── Auth ──────────────────────────────────────────────────────────────────────

_API_KEY = os.getenv("API_KEY", "dev-key-change-me")


def require_api_key(x_api_key: Annotated[Optional[str], Header()] = None) -> str:
    """Simple API key gate. Derives actor identity from the key value."""
    if x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key header")
    return x_api_key


# ── Status transition matrix ──────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":   {"approved", "rejected", "cancelled"},
    "approved":  {"cancelled"},
    "rejected":  set(),
    "cancelled": set(),
}

# ── Request / response schemas ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: Optional[str]
    is_complete: bool
    request_id: Optional[str]
    risk_label: Optional[str] = None
    risk_score: Optional[float] = None
    residual_risk_score: Optional[float] = None
    residual_risk_label: Optional[str] = None
    policy_flags: Optional[list] = None
    questionnaire_depth: Optional[str] = None
    supplier_name: Optional[str] = None
    spend_amount: Optional[float] = None
    spend_type: Optional[str] = None
    category: Optional[str] = None
    data_access: Optional[str] = None
    business_justification: Optional[str] = None
    requester_name: Optional[str] = None
    department: Optional[str] = None


class DirectCreateRequest(BaseModel):
    supplier_name: str
    supplier_website: Optional[str] = None
    spend_amount: float
    spend_type: str
    category: str
    data_access: str
    business_justification: str
    requester_name: str
    department: str
    cost_center: Optional[str] = None
    contract_expiry_date: Optional[str] = None
    is_new_supplier: Optional[bool] = None
    service_description: Optional[str] = None
    geography: Optional[str] = "UK"
    contract_duration: Optional[str] = None
    security_certifications: Optional[list] = None


class StatusUpdate(BaseModel):
    status: str
    # actor is derived from the API key in protected endpoints, not from body
    # kept here for the unprotected /status endpoint signature compatibility
    actor: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(body: ChatRequest, db: Session = Depends(get_db)):
    reply, is_complete, request_id = chat(body.session_id, body.message, db)
    extra = {}
    if request_id:
        req = db.get(ProcurementRequestORM, request_id)
        if req:
            flags = json.loads(req.policy_flags) if isinstance(req.policy_flags, str) else (req.policy_flags or [])
            extra = {
                "risk_label": req.risk_label,
                "risk_score": req.risk_score,
                "policy_flags": flags,
                "questionnaire_depth": req.questionnaire_depth,
                "supplier_name": req.supplier_name,
                "spend_amount": req.spend_amount,
                "spend_type": req.spend_type,
                "category": req.category,
                "data_access": req.data_access,
                "business_justification": req.business_justification,
                "requester_name": req.requester_name,
                "department": req.department,
            }
    return ChatResponse(reply=reply, is_complete=is_complete, request_id=request_id, **extra)


@router.post("/requests/create", response_model=ChatResponse)
def create_request_direct(body: DirectCreateRequest, db: Session = Depends(get_db)):
    session_id = str(_uuid.uuid4())
    data = body.model_dump()
    data["spend_type"] = normalize_spend_type(data.get("spend_type") or "one-time")
    data["category"] = normalize_category(data.get("category") or "Other")
    data["data_access"] = normalize_data_access(data.get("data_access") or "none")
    try:
        req = _save_request(data, session_id, db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    certs = data.get("security_certifications") or []
    residual_score, residual_label = compute_residual_risk(req.risk_score or 0.0, certs)
    req.residual_risk_score = residual_score
    db.commit()
    db.refresh(req)
    flags = json.loads(req.policy_flags) if isinstance(req.policy_flags, str) else (req.policy_flags or [])
    return ChatResponse(
        reply=None,
        is_complete=True,
        request_id=req.id,
        risk_label=req.risk_label,
        risk_score=req.risk_score,
        residual_risk_score=req.residual_risk_score,
        residual_risk_label=residual_label,
        policy_flags=flags,
        questionnaire_depth=req.questionnaire_depth,
        supplier_name=req.supplier_name,
        spend_amount=req.spend_amount,
        spend_type=req.spend_type,
        category=req.category,
        data_access=req.data_access,
        business_justification=req.business_justification,
        requester_name=req.requester_name,
        department=req.department,
    )


@router.get("/requests")
def list_requests(db: Session = Depends(get_db)):
    rows = db.query(ProcurementRequestORM).order_by(ProcurementRequestORM.created_at.desc()).all()
    return [_serialize(r) for r in rows]


@router.get("/requests/{request_id}")
def get_request(request_id: str, db: Session = Depends(get_db)):
    req = db.get(ProcurementRequestORM, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return _serialize(req)


@router.patch("/requests/{request_id}/status")
def update_status(
    request_id: str,
    body: StatusUpdate,
    db: Session = Depends(get_db),
    actor: str = Depends(require_api_key),
):
    req = db.get(ProcurementRequestORM, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    allowed = _VALID_TRANSITIONS.get(req.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition from '{req.status}' to '{body.status}'. "
                   f"Allowed transitions: {sorted(allowed) or 'none'}",
        )

    old_status = req.status
    req.status = body.status

    audit = AuditLogORM(
        request_id=req.id,
        action="status_change",
        field_name="status",
        old_value=old_status,
        new_value=body.status,
        actor=actor,  # derived from verified API key, not request body
    )
    db.add(audit)
    db.commit()
    db.refresh(req)
    return _serialize(req)


# ── Serializer ────────────────────────────────────────────────────────────────

def _serialize(req: ProcurementRequestORM) -> dict:
    return {
        "id": req.id,
        "session_id": req.session_id,
        "company_id": req.company_id,
        "supplier_name": req.supplier_name,
        "supplier_website": req.supplier_website,
        "is_new_supplier": req.is_new_supplier,
        "spend_amount": req.spend_amount,
        "spend_type": req.spend_type,
        "category": req.category,
        "data_access": req.data_access,
        "business_justification": req.business_justification,
        "requester_name": req.requester_name,
        "department": req.department,
        "cost_center": req.cost_center,
        "contract_expiry_date": req.contract_expiry_date,
        "service_description": req.service_description,
        "geography": req.geography,
        "contract_duration": req.contract_duration,
        "security_certifications": req.security_certifications or [],
        "residual_risk_score": req.residual_risk_score,
        "inherent_risk_score": req.risk_score,
        "risk_label": req.risk_label,
        "required_approvers": req.required_approvers,
        "policy_flags": json.loads(req.policy_flags) if isinstance(req.policy_flags, str) else (req.policy_flags or []),
        "questionnaire_depth": req.questionnaire_depth,
        "status": req.status,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "updated_at": req.updated_at.isoformat() if req.updated_at else None,
    }
