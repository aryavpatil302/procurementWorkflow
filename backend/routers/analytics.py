import json
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ApprovalStepORM, ProcurementRequestORM

router = APIRouter()

ROLE_SLA_HOURS: dict[str, int] = {
    "manager": 8,
    "finance": 24,
    "it_security": 24,
    "dpo": 48,
    "legal": 48,
    "cfo": 24,
}


def _to_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@router.get("/analytics/summary")
def get_analytics_summary(db: Session = Depends(get_db)) -> dict:
    # ── Totals ────────────────────────────────────────────────────────────────
    total_requests, total_spend = db.query(
        func.count(ProcurementRequestORM.id),
        func.coalesce(func.sum(ProcurementRequestORM.spend_amount), 0.0),
    ).one()

    active_queue_depth = (
        db.query(func.count(ProcurementRequestORM.id))
        .filter(ProcurementRequestORM.status == "in_review")
        .scalar()
    )

    # ── Average cycle time (approved requests only) ───────────────────────────
    last_decision_sub = (
        db.query(
            ApprovalStepORM.request_id,
            func.max(ApprovalStepORM.decided_at).label("last_decided"),
        )
        .filter(ApprovalStepORM.decided_at.isnot(None))
        .group_by(ApprovalStepORM.request_id)
        .subquery()
    )
    timing_rows = (
        db.query(ProcurementRequestORM.created_at, last_decision_sub.c.last_decided)
        .join(last_decision_sub, ProcurementRequestORM.id == last_decision_sub.c.request_id)
        .filter(ProcurementRequestORM.status == "approved")
        .all()
    )
    cycle_times = []
    for row in timing_rows:
        created = _to_naive(row.created_at)
        decided = _to_naive(row.last_decided)
        if created and decided and decided > created:
            cycle_times.append((decided - created).total_seconds() / 86400)
    avg_cycle_time_days = round(sum(cycle_times) / len(cycle_times), 2) if cycle_times else None

    # ── Requests by status ────────────────────────────────────────────────────
    status_defaults = {"pending": 0, "in_review": 0, "approved": 0, "rejected": 0, "cancelled": 0}
    for status, count in db.query(ProcurementRequestORM.status, func.count()).group_by(ProcurementRequestORM.status).all():
        if status in status_defaults:
            status_defaults[status] = count

    # ── Requests by risk label ────────────────────────────────────────────────
    risk_defaults = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for label, count in db.query(ProcurementRequestORM.risk_label, func.count()).group_by(ProcurementRequestORM.risk_label).all():
        if label in risk_defaults:
            risk_defaults[label] = count

    # ── Spend by category ─────────────────────────────────────────────────────
    spend_by_category = [
        {"category": cat or "Other", "total": round(float(total), 2), "count": int(cnt)}
        for cat, total, cnt in db.query(
            ProcurementRequestORM.category,
            func.sum(ProcurementRequestORM.spend_amount),
            func.count(ProcurementRequestORM.id),
        )
        .group_by(ProcurementRequestORM.category)
        .order_by(func.sum(ProcurementRequestORM.spend_amount).desc())
        .all()
    ]

    # ── Spend by department (top 5) ───────────────────────────────────────────
    spend_by_department = [
        {"department": dept or "Unknown", "total": round(float(total), 2), "count": int(cnt)}
        for dept, total, cnt in db.query(
            ProcurementRequestORM.department,
            func.sum(ProcurementRequestORM.spend_amount),
            func.count(ProcurementRequestORM.id),
        )
        .group_by(ProcurementRequestORM.department)
        .order_by(func.sum(ProcurementRequestORM.spend_amount).desc())
        .limit(5)
        .all()
    ]

    # ── Approval step timing by role ──────────────────────────────────────────
    decided_steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.decided_at.isnot(None))
        .all()
    )
    role_buckets: dict[str, dict] = defaultdict(
        lambda: {"display_name": "", "hours": [], "escalations": 0, "over_sla": 0}
    )
    for step in decided_steps:
        b = role_buckets[step.role]
        b["display_name"] = step.role_display_name or step.role
        created = _to_naive(step.created_at)
        decided = _to_naive(step.decided_at)
        if created and decided and decided > created:
            h = (decided - created).total_seconds() / 3600
            b["hours"].append(h)
            sla = ROLE_SLA_HOURS.get(step.role)
            if sla and h > sla:
                b["over_sla"] += 1
        if step.escalated_at:
            b["escalations"] += 1

    step_timing_by_role = sorted(
        [
            {
                "role": role,
                "display_name": b["display_name"],
                "avg_hours": round(sum(b["hours"]) / len(b["hours"]), 2) if b["hours"] else 0.0,
                "median_hours": round(sorted(b["hours"])[len(b["hours"]) // 2], 2) if b["hours"] else 0.0,
                "decided_count": len(b["hours"]),
                "escalation_count": b["escalations"],
                "over_sla_count": b["over_sla"],
                "breach_rate_pct": round(b["over_sla"] / len(b["hours"]) * 100, 1) if b["hours"] else 0.0,
            }
            for role, b in role_buckets.items()
        ],
        key=lambda x: x["avg_hours"],
        reverse=True,
    )

    # ── Policy flag frequency ─────────────────────────────────────────────────
    flag_rows = (
        db.query(ProcurementRequestORM.policy_flags)
        .filter(ProcurementRequestORM.policy_flags.isnot(None))
        .all()
    )
    flag_counter: dict[str, int] = defaultdict(int)
    flagged_count = 0
    for (flags,) in flag_rows:
        parsed = json.loads(flags) if isinstance(flags, str) else (flags or [])
        if any(f for f in parsed):
            flagged_count += 1
        for f in parsed:
            if f:
                flag_counter[f] += 1
    policy_flag_frequency = [
        {"flag": flag, "count": count}
        for flag, count in sorted(flag_counter.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    # ── Clean submission rate ─────────────────────────────────────────────────
    all_flag_rows = db.query(ProcurementRequestORM.policy_flags).all()
    clean_count = sum(
        1 for (flags,) in all_flag_rows
        if not any(f for f in (json.loads(flags) if isinstance(flags, str) else (flags or [])))
    )
    clean_submission_rate = round(clean_count / total_requests * 100, 1) if total_requests else None

    # ── Risk-adjusted spend by tier ───────────────────────────────────────────
    risk_order = ["critical", "high", "medium", "low"]
    risk_spend_raw: dict[str, dict] = {k: {"spend": 0.0, "count": 0} for k in risk_order}
    for label, spend, cnt in db.query(
        ProcurementRequestORM.risk_label,
        func.coalesce(func.sum(ProcurementRequestORM.spend_amount), 0.0),
        func.count(ProcurementRequestORM.id),
    ).group_by(ProcurementRequestORM.risk_label).all():
        if label in risk_spend_raw:
            risk_spend_raw[label] = {"spend": round(float(spend), 2), "count": int(cnt)}
    risk_adjusted_spend = [
        {"label": k, "spend": risk_spend_raw[k]["spend"], "count": risk_spend_raw[k]["count"]}
        for k in risk_order
    ]

    # ── New vs existing supplier ──────────────────────────────────────────────
    new_vs_existing = {"new": 0, "existing": 0}
    for is_new, count in db.query(ProcurementRequestORM.is_new_supplier, func.count()).group_by(ProcurementRequestORM.is_new_supplier).all():
        if is_new:
            new_vs_existing["new"] += count
        else:
            new_vs_existing["existing"] += count

    return {
        "total_requests": total_requests,
        "total_spend": round(float(total_spend), 2),
        "avg_cycle_time_days": avg_cycle_time_days,
        "active_queue_depth": active_queue_depth,
        "requests_by_status": status_defaults,
        "requests_by_risk": risk_defaults,
        "spend_by_category": spend_by_category,
        "spend_by_department": spend_by_department,
        "step_timing_by_role": step_timing_by_role,
        "policy_flag_frequency": policy_flag_frequency,
        "new_vs_existing_supplier": new_vs_existing,
        "clean_submission_rate": clean_submission_rate,
        "risk_adjusted_spend": risk_adjusted_spend,
    }
