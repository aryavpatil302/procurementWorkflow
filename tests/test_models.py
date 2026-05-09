"""Layer 1 tests: ORM round-trip for ProcurementRequestORM and AuditLogORM."""
from backend.models import AuditLogORM, ProcurementRequestORM


def _base_request(**overrides) -> dict:
    defaults = {
        "session_id": "sess-001",
        "supplier_name": "Figma Inc.",
        "spend_amount": 200.0,
        "spend_type": "subscription",
        "category": "Software",
        "data_access": "internal",
        "business_justification": "Design team needs collaborative tooling.",
        "requester_name": "Alice",
        "department": "Design",
    }
    return {**defaults, **overrides}


def test_create_request_basic(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()
    db.refresh(req)

    fetched = db.get(ProcurementRequestORM, req.id)
    assert fetched is not None
    assert fetched.supplier_name == "Figma Inc."
    assert fetched.spend_amount == 200.0
    assert fetched.status == "pending"


def test_request_id_is_uuid(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()
    assert len(req.id) == 36  # UUID4 string: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx


def test_request_default_status(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()
    assert req.status == "pending"


def test_request_optional_fields_null(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()
    db.refresh(req)
    assert req.supplier_website is None
    assert req.cost_center is None
    assert req.contract_expiry_date is None
    assert req.risk_score is None
    assert req.questionnaire_depth is None


def test_request_json_fields(db):
    req = ProcurementRequestORM(
        **_base_request(),
        required_approvers=["finance", "it_security"],
        policy_flags=["High spend — Finance approval required"],
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    assert req.required_approvers == ["finance", "it_security"]
    assert "High spend" in req.policy_flags[0]


def test_request_all_fields_roundtrip(db):
    req = ProcurementRequestORM(
        **_base_request(
            supplier_website="https://figma.com",
            is_new_supplier=False,
            cost_center="CC-1234",
            contract_expiry_date="2025-12-31",
            risk_score=0.35,
            risk_label="medium",
            required_approvers=["manager"],
            policy_flags=[],
            questionnaire_depth="standard",
            status="approved",
        )
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    assert req.supplier_website == "https://figma.com"
    assert req.is_new_supplier is False
    assert req.cost_center == "CC-1234"
    assert req.risk_score == 0.35
    assert req.risk_label == "medium"
    assert req.questionnaire_depth == "standard"
    assert req.status == "approved"


def test_audit_log_linked_to_request(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()

    log = AuditLogORM(
        request_id=req.id,
        action="status_change",
        field_name="status",
        old_value="pending",
        new_value="approved",
        actor="manager@company.com",
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    assert log.request_id == req.id
    assert log.action == "status_change"
    assert log.new_value == "approved"


def test_audit_log_id_is_uuid(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()

    log = AuditLogORM(request_id=req.id, action="created", actor="system")
    db.add(log)
    db.commit()
    assert len(log.id) == 36


def test_request_relationship_loads_audit_logs(db):
    req = ProcurementRequestORM(**_base_request())
    db.add(req)
    db.commit()

    for action in ("created", "submitted", "approved"):
        db.add(AuditLogORM(request_id=req.id, action=action, actor="system"))
    db.commit()
    db.refresh(req)

    assert len(req.audit_logs) == 3
    actions = {log.action for log in req.audit_logs}
    assert actions == {"created", "submitted", "approved"}
