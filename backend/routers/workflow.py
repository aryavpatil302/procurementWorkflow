"""
Workflow configuration endpoints.

GET /workflow-config  — return the current configuration
PUT /workflow-config  — validate and save an updated configuration

Supports two formats:
  - New: { flows: [...], roles: [...] }
  - Legacy: { stages: [...], approval_rules: [...], roles: [...] }
"""

import json
import os
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ApprovalStepORM, ProcurementRequestORM

router = APIRouter()

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "workflow_config.json")

_BUILTIN_ROLES  = {"manager", "finance", "it_security", "legal", "dpo", "cfo",
                   "director", "fpa", "ceo"}
APPROVAL_TYPES  = {"approval"}
ACTION_TYPES    = {"task", "notify", "milestone", "create_agreement", "create_po", "rfx"}
ALL_BLOCK_TYPES = APPROVAL_TYPES | ACTION_TYPES


def _read_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _write_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _valid_role_ids(roles, existing_config) -> set:
    if roles is not None:
        return {r["id"] for r in roles}
    existing_roles = existing_config.get("roles", [])
    return {r["id"] for r in existing_roles} if existing_roles else _BUILTIN_ROLES


def _validate_roles(roles) -> None:
    if not isinstance(roles, list):
        raise HTTPException(status_code=400, detail="roles must be a list")
    for i, role in enumerate(roles):
        if not isinstance(role.get("id"), str) or not role["id"].strip():
            raise HTTPException(status_code=400, detail=f"Role {i}: id must be a non-empty string")
        if not isinstance(role.get("name"), str) or not role["name"].strip():
            raise HTTPException(status_code=400, detail=f"Role {i}: name must be a non-empty string")


def _validate_rules(rules, valid_role_ids, valid_stage_ids, prefix="Rule") -> None:
    for i, rule in enumerate(rules):
        block_type = rule.get("type", "approval")
        group      = rule.get("sequence_group")

        if block_type not in ALL_BLOCK_TYPES:
            raise HTTPException(status_code=400, detail=f"{prefix} {i}: unknown block type '{block_type}'")
        if valid_stage_ids and group not in valid_stage_ids:
            raise HTTPException(
                status_code=400,
                detail=f"{prefix} {i}: sequence_group {group} does not match any stage id",
            )

        if block_type == "approval":
            role = rule.get("role")
            if role not in valid_role_ids:
                raise HTTPException(status_code=400, detail=f"{prefix} {i}: unknown role '{role}'")
            if not rule.get("always") and not rule.get("condition"):
                raise HTTPException(
                    status_code=400,
                    detail=f"{prefix} {i}: approval block must have 'always' or a condition",
                )
        else:
            if not rule.get("label"):
                raise HTTPException(status_code=400, detail=f"{prefix} {i}: action block must have a label")


def _validate_stages(stages, prefix="Stage") -> set:
    if not isinstance(stages, list):
        raise HTTPException(status_code=400, detail=f"{prefix}s must be a list")
    valid_ids = set()
    for i, stage in enumerate(stages):
        if not isinstance(stage.get("id"), int):
            raise HTTPException(status_code=400, detail=f"{prefix} {i}: id must be an integer")
        if not isinstance(stage.get("name"), str) or not stage["name"].strip():
            raise HTTPException(status_code=400, detail=f"{prefix} {i}: name must be a non-empty string")
        valid_ids.add(stage["id"])
    return valid_ids


def _find_best_flow(config: dict, req: ProcurementRequestORM) -> str | None:
    """Return the id of the best-matching flow for a request under the given config."""
    flows = config.get("flows", [])
    best_flow, best_score = None, -1
    for flow in flows:
        trigger = flow.get("trigger")
        if trigger is None:
            if best_flow is None:
                best_flow = flow  # default fallback
            continue
        score = 0
        cats = trigger.get("category") or []
        if cats and req.category not in cats:
            continue
        score += 1
        if "is_new_supplier" in trigger:
            if bool(trigger["is_new_supplier"]) != bool(req.is_new_supplier):
                continue
            score += 1
        if "spend_amount_gt" in trigger:
            if not (req.spend_amount and req.spend_amount > trigger["spend_amount_gt"]):
                continue
            score += 1
        if score > best_score:
            best_flow, best_score = flow, score
    return best_flow["id"] if best_flow else None


def _retry_fallback_summaries(request_ids: list) -> None:
    """Background thread: retry AI summary generation for steps that got fallback text.
    Polls every 60 s until all summaries are real, or gives up after 10 attempts."""
    from backend.database import SessionLocal
    from backend.services.approval_engine import generate_role_summary

    for attempt in range(10):
        time.sleep(60)
        try:
            db = SessionLocal()
            remaining = 0
            for rid in request_ids:
                req = db.query(ProcurementRequestORM).filter_by(id=rid).first()
                if not req:
                    continue
                fallback_prefix = f"{req.supplier_name} —"
                steps = db.query(ApprovalStepORM).filter(
                    ApprovalStepORM.request_id == rid,
                ).all()
                for step in steps:
                    is_fallback = (
                        step.ai_summary is None
                        or step.ai_summary.startswith(fallback_prefix)
                    )
                    if not is_fallback:
                        continue
                    summary = generate_role_summary(req, step.role)
                    if not summary.startswith(fallback_prefix):
                        step.ai_summary = summary
                        db.commit()
                    else:
                        remaining += 1
            db.close()
            if remaining == 0:
                break
        except Exception:
            pass


def _reroute_active_requests(config: dict, db: Session) -> int:
    """Re-evaluate all in_review requests against the new config. Regenerate steps for
    any request whose best-matching flow has changed. Returns count of rerouted requests."""
    from backend.services.approval_engine import generate_approval_steps  # avoid circular import

    active_reqs = db.query(ProcurementRequestORM).filter(
        ProcurementRequestORM.status.in_(["in_review", "pending"])
    ).all()

    rerouted_ids = []
    for req in active_reqs:
        new_flow_id = _find_best_flow(config, req)
        if not new_flow_id:
            continue

        new_flow = next((f for f in config.get("flows", []) if f["id"] == new_flow_id), None)
        if not new_flow:
            continue

        existing = db.query(ApprovalStepORM).filter(
            ApprovalStepORM.request_id == req.id,
            ApprovalStepORM.status.in_(["active", "pending"]),
        ).all()

        new_roles = {r["role"] for r in (new_flow.get("approval_rules") or [])}
        existing_roles = {s.role for s in existing}

        if new_roles != existing_roles:
            db.query(ApprovalStepORM).filter(
                ApprovalStepORM.request_id == req.id,
                ApprovalStepORM.status.in_(["active", "pending"]),
            ).delete(synchronize_session=False)
            db.commit()
            generate_approval_steps(req, db)
            db.commit()
            rerouted_ids.append(req.id)

    if rerouted_ids:
        threading.Thread(
            target=_retry_fallback_summaries,
            args=(rerouted_ids,),
            daemon=True,
        ).start()

    return len(rerouted_ids)


@router.get("/workflow-config")
def get_config():
    return _read_config()


@router.put("/workflow-config")
def put_config(body: dict[str, Any], db: Session = Depends(get_db)):
    flows  = body.get("flows")
    roles  = body.get("roles")
    stages = body.get("stages")
    rules  = body.get("approval_rules")

    existing = _read_config()

    if roles is not None:
        _validate_roles(roles)

    valid_ids = _valid_role_ids(roles, existing)

    if flows is not None:
        # ── New multi-flow format ──────────────────────────────────────────────
        if not isinstance(flows, list):
            raise HTTPException(status_code=400, detail="flows must be a list")
        if not flows:
            raise HTTPException(status_code=400, detail="flows must not be empty")

        for fi, flow in enumerate(flows):
            if not isinstance(flow.get("id"), str) or not flow["id"].strip():
                raise HTTPException(status_code=400, detail=f"Flow {fi}: id must be a non-empty string")
            if not isinstance(flow.get("name"), str) or not flow["name"].strip():
                raise HTTPException(status_code=400, detail=f"Flow {fi}: name must be a non-empty string")

            flow_stages = flow.get("stages") or []
            flow_rules  = flow.get("approval_rules") or []
            stage_ids   = _validate_stages(flow_stages, prefix=f"Flow {fi} Stage")
            _validate_rules(flow_rules, valid_ids, stage_ids, prefix=f"Flow {fi} Rule")

        merged = {**existing, "flows": flows}
        if roles is not None:
            merged["roles"] = roles
        # Strip legacy top-level keys if present
        merged.pop("stages", None)
        merged.pop("approval_rules", None)
        _write_config(merged)
        rerouted = _reroute_active_requests(merged, db)
        return {"ok": True, "flow_count": len(flows), "rerouted": rerouted}

    else:
        # ── Legacy single-flow format ─────────────────────────────────────────
        if not isinstance(rules, list):
            raise HTTPException(status_code=400, detail="approval_rules must be a list")

        stage_ids = set()
        if stages is not None:
            stage_ids = _validate_stages(stages)

        _validate_rules(rules, valid_ids, stage_ids)

        merged = {**existing, "approval_rules": rules}
        if stages is not None:
            merged["stages"] = stages
        if roles is not None:
            merged["roles"] = roles
        _write_config(merged)
        return {"ok": True, "rule_count": len(rules), "stage_count": len(stages) if stages else None}
