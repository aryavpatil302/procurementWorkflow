import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, JSON, String
from sqlalchemy.orm import relationship

from backend.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProcurementRequestORM(Base):
    __tablename__ = "procurement_requests"

    id = Column(String, primary_key=True, default=_new_id)
    session_id = Column(String, nullable=False, index=True)
    company_id = Column(String, nullable=False, default="default")

    # Supplier
    supplier_name = Column(String, nullable=False)
    supplier_website = Column(String, nullable=True)
    is_new_supplier = Column(Boolean, default=True)

    # Spend
    spend_amount = Column(Float, nullable=False)
    spend_type = Column(String, nullable=False)       # one-time | recurring | subscription
    category = Column(String, nullable=False)          # Software | Hardware | Services | ...
    cost_center = Column(String, nullable=True)
    contract_expiry_date = Column(String, nullable=True)

    # Risk
    data_access = Column(String, nullable=False)       # none | internal | confidential | personal_data
    business_justification = Column(String, nullable=False)
    service_description = Column(String, nullable=True)
    geography = Column(String, nullable=True)          # UK | EU | US | Global
    contract_duration = Column(String, nullable=True)  # Under 6 months | 6–12 months | 1–2 years | Ongoing
    security_certifications = Column(JSON, nullable=True)  # list[str]
    residual_risk_score = Column(Float, nullable=True)

    # Requester
    requester_name = Column(String, nullable=False)
    department = Column(String, nullable=False)

    # Risk scoring output
    risk_score = Column(Float, nullable=True)
    risk_label = Column(String, nullable=True)         # low | medium | high | critical

    # Policy engine output
    required_approvers = Column(JSON, nullable=True)   # list[str]
    policy_flags = Column(JSON, nullable=True)         # list[str]
    questionnaire_depth = Column(String, nullable=True)  # basic | standard | deep_due_diligence

    # Lifecycle
    status = Column(String, nullable=False, default="pending")  # pending | approved | rejected | cancelled
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    audit_logs = relationship("AuditLogORM", back_populates="request", cascade="all, delete-orphan")


class AuditLogORM(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=_new_id)
    request_id = Column(String, ForeignKey("procurement_requests.id"), nullable=False, index=True)
    action = Column(String, nullable=False)
    field_name = Column(String, nullable=True)
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    actor = Column(String, nullable=False)
    timestamp = Column(DateTime, default=_now)

    request = relationship("ProcurementRequestORM", back_populates="audit_logs")
