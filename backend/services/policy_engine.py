"""
Policy engine — approval routing + questionnaire depth.

Returns required_approvers, policy flags, and questionnaire_depth.
Mirrors Omnea's Workflow Builder rules engine.
"""

import os
from dataclasses import dataclass, field

CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "£")


@dataclass
class PolicyResult:
    required_approvers: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    spend_tier_label: str = ""
    questionnaire_depth: str = "basic"  # basic | standard | deep_due_diligence


def evaluate(
    spend_amount: float,
    category: str,
    data_access: str,
    risk_score: float,
    is_new_supplier: bool = True,
    geography: str = "UK",
    contract_duration: str | None = None,
) -> PolicyResult:
    result = PolicyResult()
    approver_set: set[str] = set()

    # 1. Manager always required
    approver_set.add("manager")

    # 2. Spend tiers
    if spend_amount >= 100_000:
        result.spend_tier_label = "strategic"
        approver_set.update(["finance", "cfo", "legal"])
        result.flags.append("Strategic spend — CFO, Finance, and Legal approval required.")
    elif spend_amount >= 50_000:
        result.spend_tier_label = "high"
        approver_set.update(["finance", "legal"])
        result.flags.append("High spend — Finance and Legal approval required.")
    elif spend_amount >= 10_000:
        result.spend_tier_label = "medium"
        approver_set.add("finance")
        result.flags.append(f"Finance approval required for spend over {CURRENCY_SYMBOL}10,000.")
    else:
        result.spend_tier_label = "low"

    # 3. Data sensitivity
    if data_access == "personal_data":
        approver_set.update(["legal", "dpo"])
        result.flags.append("Personal data access — Legal and DPO review required (GDPR).")
    elif data_access == "confidential":
        approver_set.add("it_security")
        result.flags.append("Confidential data access — IT Security review required.")

    # 4. Geography / data residency
    if geography in ("EU", "Global") and data_access == "personal_data":
        approver_set.add("legal")
        result.flags.append("EU/Global data transfer — GDPR Article 46 transfer mechanisms required.")
    if geography == "Global" and data_access in ("personal_data", "confidential"):
        result.flags.append("Global deployment — multi-jurisdiction data compliance review required.")

    # 5. High risk score
    if risk_score >= 0.65:
        approver_set.add("it_security")
        result.flags.append("High inherent risk score — IT Security review required.")

    # 6. New supplier
    if is_new_supplier:
        approver_set.add("it_security")
        result.flags.append("New supplier — enhanced due diligence required.")

    # 7. Legal category
    if category == "Legal":
        approver_set.add("legal")
        result.flags.append("Legal services category — Legal team must review.")

    # 8. Contract duration
    long_term = contract_duration in ("1–2 years", "Ongoing", "1-2 years")
    if long_term and spend_amount >= 10_000:
        result.flags.append("Long-term contract — annual review and renewal management required.")
    if long_term and spend_amount >= 50_000:
        approver_set.add("finance")
        result.flags.append("Long-term high-value contract — multi-year financial commitment requires Finance sign-off.")

    # 9. Questionnaire depth
    if spend_amount > 50_000 or data_access in ("personal_data", "confidential"):
        result.questionnaire_depth = "deep_due_diligence"
    elif spend_amount > 10_000 or is_new_supplier or long_term:
        result.questionnaire_depth = "standard"
    else:
        result.questionnaire_depth = "basic"

    result.required_approvers = sorted(approver_set)
    return result
