"""
Approval orchestration engine.

generate_approval_steps() — reads workflow_config.json, evaluates rules against the
request, creates ApprovalStepORM rows, and pre-generates role-specific AI summaries
stored immediately so approval cards load instantly with no live LLM call.

advance_workflow() — the state machine that drives requests through
pending → in_review → approved / rejected after every approver decision.
"""

import json
import os
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from backend.models import ApprovalStepORM, ProcurementRequestORM
from backend.services._groq_utils import MODEL, call_with_retry, get_client

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "workflow_config.json")

_TERMINAL = {"approved", "skipped", "escalated"}

ROLE_DISPLAY_NAMES = {
    "manager":     "Line Manager",
    "finance":     "Finance Team",
    "it_security": "IT Security",
    "legal":       "Legal Team",
    "dpo":         "Data Protection Officer",
    "cfo":         "CFO",
    "director":    "Director",
    "fpa":         "FP&A",
    "ceo":         "CEO",
}

ROLE_APPROVERS = {
    "manager":     "Sarah Chen (Manager)",
    "finance":     "James Okafor (Finance)",
    "it_security": "Priya Mehta (IT Security)",
    "legal":       "Tom Whitfield (Legal)",
    "dpo":         "Anna Kowalski (DPO)",
    "cfo":         "David Harrington (CFO)",
    "director":    "Emma Wilson (Director)",
    "fpa":         "Marcus Lee (FP&A)",
    "ceo":         "Robert Fox (CEO)",
}

# Role-specific system prompts for AI summary generation (Omnea Analyze).
# Each prompt asks for exactly N structured bullet points using '• ' as the marker
# so the frontend can split on sentences for display.
ROLE_SUMMARY_PROMPTS = {
    "manager": (
        "You are a procurement AI assistant briefing a line manager on a purchase request they must approve.\n"
        "Write exactly 3 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Who is requesting this, from which department, and what specific business problem the supplier solves.\n"
        "2. Whether the business justification is specific and credible, whether this replaces existing tooling "
        "or is a net-new capability, and your brief assessment of alignment with typical team priorities.\n"
        "3. The spend amount in plain terms (one-time or ongoing), which cost centre bears it, and any "
        "policy flags or approval thresholds the manager should be aware of.\n"
        "Output bullet points only. Do not use headers, bold, or any other formatting."
    ),
    "finance": (
        "You are a procurement AI assistant briefing the Finance team on a purchase request.\n"
        "Write exactly 4 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Spend breakdown: exact amount, whether recurring/subscription/one-time, annualised cost, and "
        "total contract value over the full term where duration is known — use actual pound figures.\n"
        "2. Budget impact: whether this spend level triggers any approval thresholds, and any "
        "multi-year or commitment implications if applicable.\n"
        "3. Financial commitment risk: lock-in period, auto-renewal exposure, early termination risk, and "
        "any multi-year liability this creates.\n"
        "4. Value assessment: brief view on whether the business justification supports the financial outlay, "
        "and whether alternatives or competitive quotes should be obtained at this spend level.\n"
        "Output bullet points only. Include specific pound amounts wherever available."
    ),
    "it_security": (
        "You are a procurement AI assistant briefing the IT Security team on a purchase request.\n"
        "Write exactly 4 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Data exposure: exactly what level of data the supplier accesses, whether that includes employee "
        "or customer personal data, and what that implies for access governance and data handling obligations.\n"
        "2. Security posture gap analysis: certifications the supplier has declared versus what is typically "
        "required for this data access level and category — explicitly name any missing certifications "
        "(e.g. SOC 2 Type II, ISO 27001, Cyber Essentials Plus).\n"
        "3. Risk score context: the inherent and residual risk scores, which specific factors are driving "
        "the scores highest (data type, new supplier status, geography, category), and whether the residual "
        "score adequately reflects the declared certifications.\n"
        "4. Technical due diligence flags: cross-border data flows, cloud deployment geography, sub-processor "
        "risk, or missing controls that IT Security must probe before approving.\n"
        "Output bullet points only. Name missing certifications explicitly."
    ),
    "legal": (
        "You are a procurement AI assistant briefing the Legal team on a purchase request.\n"
        "Write exactly 4 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Data protection obligations: whether personal or confidential data is involved, which GDPR "
        "provisions are triggered (cite Article numbers — Articles 28, 46, 35 as relevant), and what "
        "legal basis for processing must be documented.\n"
        "2. Required contractual protections: the specific agreements needed before contract execution — "
        "Data Processing Agreement (DPA), Standard Contractual Clauses (SCCs), data transfer impact "
        "assessment — based on the data type and whether data crosses EU/UK borders.\n"
        "3. Contract risk: duration, lock-in period, auto-renewal clauses, limitation of liability terms "
        "to negotiate, and whether the contract length creates disproportionate long-term legal exposure.\n"
        "4. Outstanding Legal actions: the concrete steps Legal must complete before this can proceed — "
        "drafting the DPA, reviewing the supplier's standard terms, adding data protection schedules, "
        "or flagging red-line positions.\n"
        "Output bullet points only. Cite GDPR article numbers where relevant."
    ),
    "dpo": (
        "You are a procurement AI assistant briefing the Data Protection Officer on a purchase request.\n"
        "Write exactly 4 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Personal data scope: the specific categories of personal data the supplier will process (from "
        "data access level and service description), the likely type and number of data subjects affected, "
        "and the lawful basis for processing under GDPR Article 6 (and Article 9 if special categories apply).\n"
        "2. Cross-border transfer assessment: whether data will be transferred outside the EU/EEA, which "
        "countries are involved, and which transfer mechanism under GDPR Article 46 is required — Standard "
        "Contractual Clauses, adequacy decision, Binding Corporate Rules, or other safeguard.\n"
        "3. DPIA requirement: whether a Data Protection Impact Assessment is required under GDPR Article 35 "
        "given the nature, scope, and risk of processing, what the key risks to data subjects are, and whether "
        "prior supervisory authority consultation under Article 36 may be triggered.\n"
        "4. DPO recommended actions: specific steps — updating the Record of Processing Activities (RoPA), "
        "issuing a consultation to the controller, reviewing the supplier DPA and privacy notice, and any "
        "conditions or restrictions to attach to approval.\n"
        "Output bullet points only. Cite GDPR article numbers precisely."
    ),
    "cfo": (
        "You are a procurement AI assistant briefing the CFO on a purchase request that has cleared all "
        "specialist reviews.\n"
        "Write exactly 4 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Strategic financial exposure: exact spend amount, annualised cost, total contract value over the "
        "full term, and how this compares to the £10,000 CFO approval threshold — make clear the full "
        "multi-year financial commitment in pound figures.\n"
        "2. Commitment and renewal risk: whether this creates a long-term obligation, what the financial "
        "exposure is if the business exits early, whether there are automatic renewal or price escalation "
        "clauses, and what the renewal risk profile looks like.\n"
        "3. Business case: whether the stated business justification supports the spend at CFO level, what "
        "ROI or cost-avoidance case is being made, and whether alternatives or competitive quotes should "
        "have been obtained at this spend level.\n"
        "4. Approval chain summary: which approvals have already been completed (Manager, Finance, IT Security, "
        "Legal, DPO as applicable), whether any specialist reviewers raised outstanding concerns, and what "
        "residual risk the CFO is being asked to accept.\n"
        "Output bullet points only. Include specific pound figures. Be direct about financial risk."
    ),
    "director": (
        "You are a procurement AI assistant briefing a department Director on a purchase request requiring senior sign-off.\n"
        "Write exactly 3 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Business case and strategic fit: who is requesting this, what problem it solves, and whether it aligns "
        "with known departmental priorities and current budget cycles.\n"
        "2. Spend and commitment: the full financial exposure, contract duration if known, and any "
        "lock-in or renewal risk the Director should be aware of.\n"
        "3. Risk summary: the overall risk rating, any unresolved specialist concerns, and what the Director "
        "is being asked to accept in approving this request.\n"
        "Output bullet points only."
    ),
    "fpa": (
        "You are a procurement AI assistant briefing the FP&A team on a purchase request.\n"
        "Write exactly 4 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Budget alignment: the spend amount, cost centre, and whether this purchase appears in the current "
        "approved budget or represents unplanned spend.\n"
        "2. Financial modelling impact: annualised cost, total contract value, and how this affects the "
        "department's run-rate and year-end forecast.\n"
        "3. Commitment risk: lock-in period, auto-renewal exposure, and whether the financial commitment "
        "creates multi-year budget obligations that constrain future flexibility.\n"
        "4. FP&A recommendation: whether the business justification and spend level are consistent with "
        "financial planning targets, and what financial conditions should be attached to approval.\n"
        "Output bullet points only. Include specific pound figures."
    ),
    "ceo": (
        "You are a procurement AI assistant briefing the CEO on a high-value purchase request that has cleared "
        "all specialist and executive reviews.\n"
        "Write exactly 3 bullet points using '• ' as the bullet character. Each bullet is 1–2 sentences.\n"
        "Cover these topics in order:\n"
        "1. Strategic rationale: the business problem this solves, whether it is aligned with company strategy, "
        "and the full multi-year financial commitment in plain pound figures.\n"
        "2. Risk and governance: the residual risk rating after specialist review, any outstanding concerns "
        "raised by Legal, DPO, or IT Security, and what governance controls are in place post-approval.\n"
        "3. Approval chain: which executives and specialists have already reviewed and approved, whether any "
        "conditions were attached, and what the CEO is being asked to ratify.\n"
        "Output bullet points only. Be direct and concise."
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
            if request.spend_amount is None or request.spend_amount <= value:
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


_SUMMARY_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "summary_cache.json")


def _load_cached_summary(request_id: str, role: str) -> str | None:
    """Return a pre-generated summary from the cache file, or None if not found."""
    try:
        with open(_SUMMARY_CACHE_PATH) as f:
            cache = json.load(f)
        return cache.get(request_id, {}).get(role)
    except Exception:
        return None


def generate_role_summary(request: ProcurementRequestORM, role: str) -> str:
    """
    Generate a role-specific 2-3 sentence summary via Groq/Llama.
    Called once at step creation time; result is stored in ApprovalStepORM.ai_summary.
    Checks a local cache first so pre-generated summaries survive demo resets.
    Falls back to a plain-text summary so the demo never breaks on an API error.
    """
    cached = _load_cached_summary(str(request.id), role)
    if cached:
        return cached

    client = get_client()
    system_prompt = ROLE_SUMMARY_PROMPTS.get(
        role,
        "Summarise this procurement request in 3 bullet points using '• ' as the bullet character."
    )

    certs = request.security_certifications or []
    flags = request.policy_flags or []

    # Compute Total Contract Value for financial context
    tcv_note = "not calculable"
    if request.spend_amount:
        dur = (request.contract_duration or "").lower()
        months = (
            36 if any(x in dur for x in ["36", "3 year", "3-year", "three year"]) else
            24 if any(x in dur for x in ["24", "2 year", "2-year", "two year"]) else
            12 if any(x in dur for x in ["12", "1 year", "1-year", "annual", "one year"]) else
            None
        )
        if request.spend_type in ("recurring", "subscription") and months:
            tcv = request.spend_amount * months / 12
            tcv_note = f"£{tcv:,.2f} total over {months} months"
        elif request.spend_type == "one_time":
            tcv_note = f"£{request.spend_amount:,.2f} (one-time, no recurring obligation)"
        elif request.spend_type in ("recurring", "subscription"):
            tcv_note = f"£{request.spend_amount:,.2f}/year ongoing (duration not specified)"

    request_context = (
        f"Supplier: {request.supplier_name}\n"
        f"Website: {request.supplier_website or 'not provided'}\n"
        f"Service Description: {request.service_description or 'not specified'}\n"
        f"Category: {request.category}\n"
        f"Spend Amount: £{request.spend_amount:,.2f} ({request.spend_type})\n"
        f"Contract Duration: {request.contract_duration or 'not specified'}\n"
        f"Total Contract Value: {tcv_note}\n"
        f"Geography: {request.geography}\n"
        f"Data Access Level: {request.data_access}\n"
        f"New Supplier: {'Yes — no prior relationship or due diligence on file' if request.is_new_supplier else 'No — existing supplier'}\n"
        f"Security Certifications Declared: {', '.join(certs) if certs else 'None declared'}\n"
        f"Inherent Risk Score: {'N/A' if request.risk_score is None else f'{request.risk_score:.3f}'} ({request.risk_label or 'unknown'})\n"
        f"Residual Risk Score: {'N/A' if request.residual_risk_score is None else f'{request.residual_risk_score:.3f}'}\n"
        f"Policy Flags Raised: {', '.join(flags) if flags else 'None'}\n"
        f"Requester: {request.requester_name} ({request.department})\n"
        f"Business Justification: {request.business_justification}\n"
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
            max_tokens=400,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return (
            f"{request.supplier_name} — £{request.spend_amount:,.0f} {request.spend_type} "
            f"({request.category}). Data access: {request.data_access}. "
            f"Risk: {request.risk_label}. Requester: {request.requester_name} ({request.department})."
        )


def _role_info(config: dict, role_id: str) -> dict:
    """Return display_name and approver_name for a role, reading from config first."""
    for r in config.get("roles", []):
        if r.get("id") == role_id:
            people = r.get("people") or []
            return {
                "display_name":  r.get("name") or role_id,
                "approver_name": people[0].get("name") if people else None,
            }
    return {
        "display_name":  ROLE_DISPLAY_NAMES.get(role_id, role_id.replace("_", " ").title()),
        "approver_name": ROLE_APPROVERS.get(role_id),
    }


def _matches_trigger(trigger: dict, request: ProcurementRequestORM) -> bool:
    """Return True if all trigger conditions match the request (AND logic)."""
    for field, value in trigger.items():
        if field == "spend_amount_gt":
            if request.spend_amount is None or request.spend_amount <= value:
                return False
        elif field == "spend_amount_lte":
            if request.spend_amount is None or request.spend_amount > value:
                return False
        elif field == "is_new_supplier":
            if bool(request.is_new_supplier) != bool(value):
                return False
        elif isinstance(value, list):
            if getattr(request, field, None) not in value:
                return False
        else:
            if getattr(request, field, None) != value:
                return False
    return True


def _find_matching_flow(config: dict, request: ProcurementRequestORM) -> dict:
    """
    Return the most specific matching flow (highest number of trigger conditions
    that all match). Ties broken by config order. Falls back to the flow with
    trigger=null (Default), then the first flow.
    If no flows array exists, returns the config itself (legacy format).
    """
    flows = config.get("flows")
    if not flows:
        return config  # legacy: top-level stages/rules

    best_flow, best_score = None, -1
    default_flow = None

    for flow in flows:
        trigger = flow.get("trigger")
        if trigger is None:
            default_flow = flow
            continue
        if _matches_trigger(trigger, request):
            score = len(trigger)  # more conditions = more specific
            if score > best_score:
                best_flow, best_score = flow, score

    return best_flow or default_flow or flows[0]


def generate_approval_steps(
    request: ProcurementRequestORM,
    db: Session,
    flow_id: str = None,
) -> List[ApprovalStepORM]:
    """
    Read workflow_config.json, pick the matching flow for this request, evaluate
    each rule, create ApprovalStepORM rows, and pre-generate AI summaries.

    Pass flow_id to bypass trigger-matching and force a specific flow.
    Called immediately after _save_request() commits the ProcurementRequestORM.
    The caller must set request.status = 'in_review' and db.commit() after this returns.
    """
    config = _load_config()
    if flow_id:
        flows  = config.get("flows", [])
        flow   = next((f for f in flows if f["id"] == flow_id), None)
        if not flow:
            raise ValueError(f"Flow '{flow_id}' not found in config")
    else:
        flow   = _find_matching_flow(config, request)
    rules  = flow.get("approval_rules", [])

    # Determine the first (lowest) sequence_group so those steps start active
    approval_groups = [r["sequence_group"] for r in rules if r.get("type", "approval") == "approval"]
    first_group     = min(approval_groups) if approval_groups else 1

    steps = []
    for rule in rules:
        # Only approval-type rules generate steps; action blocks are canvas-only
        if rule.get("type", "approval") != "approval":
            continue
        if not _evaluate_rule(rule, request):
            continue

        role  = rule["role"]
        group = rule["sequence_group"]
        info  = _role_info(config, role)

        summary = generate_role_summary(request=request, role=role)

        step = ApprovalStepORM(
            request_id=request.id,
            role=role,
            role_display_name=info["display_name"],
            sequence_group=group,
            status="active" if group == first_group else "pending",
            approver_name=info["approver_name"],
            ai_summary=summary,
        )
        db.add(step)
        steps.append(step)

    db.flush()  # caller owns the commit boundary (sets status + commits atomically)
    return steps


def advance_workflow(request_id: str, db: Session) -> None:
    """
    Called after every approve/reject/escalate decision. State machine:

    1. If any step is 'rejected': reject the request, skip all pending/active peers.
    2. If all preceding steps are terminal (_TERMINAL), activate the next pending group.
    3. If all steps are terminal and at least one is 'approved': mark request approved.
    """
    steps = (
        db.query(ApprovalStepORM)
        .filter(ApprovalStepORM.request_id == request_id)
        .all()
    )
    request = db.query(ProcurementRequestORM).filter_by(id=request_id).first()

    if not steps or not request:
        return

    # 1. Rejection propagation — skip all non-terminal peers and downstream steps
    if any(s.status == "rejected" for s in steps):
        request.status = "rejected"
        for s in steps:
            if s.status in ("pending", "active"):
                s.status = "skipped"
        db.commit()
        return

    # 2. Advance to next pending group when all preceding steps are terminal
    pending_groups = sorted(set(s.sequence_group for s in steps if s.status == "pending"))
    if pending_groups:
        next_group = pending_groups[0]
        preceding = [s for s in steps if s.sequence_group < next_group]
        if preceding and all(s.status in _TERMINAL for s in preceding):
            for s in steps:
                if s.sequence_group == next_group and s.status == "pending":
                    s.status = "active"
            db.commit()
            return

    # 3. Full completion: all steps terminal, at least one explicit approval
    non_terminal = [s for s in steps if s.status not in _TERMINAL]
    if not non_terminal and any(s.status == "approved" for s in steps):
        request.status = "approved"
        db.commit()
