"""
Risk scoring service.

Computes inherent risk score (0.0–1.0) and residual risk score (after certification
credits). Labels: low / medium / high / critical.

Mirrors Omnea's two-layer risk model: inherent risk before supplier controls are
applied, residual risk after accounting for certifications the supplier holds.
"""

_SPEND_SCORE = [
    (100_000, 0.40),
    (50_000,  0.30),
    (10_000,  0.20),
    (1_000,   0.10),
    (0,       0.05),
]

_DATA_ACCESS_SCORE = {
    "personal_data": 0.40,
    "confidential":  0.30,
    "internal":      0.10,
    "none":          0.00,
}

_CATEGORY_SCORE = {
    "Software":  0.10,
    "Hardware":  0.08,
    "Services":  0.12,
    "Marketing": 0.05,
    "Legal":     0.05,
    "Other":     0.08,
}

_GEOGRAPHY_SCORE = {
    "Global": 0.08,
    "US":     0.05,
    "EU":     0.03,
    "UK":     0.00,
}

_NEW_SUPPLIER_BONUS = 0.10

_CERT_CREDITS = {
    "SOC 2":          0.08,
    "ISO 27001":      0.08,
    "GDPR compliant": 0.05,
    "DORA":           0.05,
}


def _label(score: float) -> str:
    if score >= 0.75: return "critical"
    if score >= 0.50: return "high"
    if score >= 0.25: return "medium"
    return "low"


def score_supplier(
    spend_amount: float,
    category: str,
    data_access: str,
    is_new_supplier: bool = True,
    geography: str = "UK",
) -> tuple[float, str]:
    """Return (inherent_risk_score, risk_label)."""
    if not isinstance(spend_amount, (int, float)) or spend_amount <= 0:
        raise ValueError(f"spend_amount must be a positive number, got: {spend_amount!r}")

    score = 0.0
    for threshold, points in _SPEND_SCORE:
        if spend_amount >= threshold:
            score += points
            break

    score += _DATA_ACCESS_SCORE.get(data_access, 0.0)
    score += _CATEGORY_SCORE.get(category, 0.08)
    score += _GEOGRAPHY_SCORE.get(geography, 0.05)
    if is_new_supplier:
        score += _NEW_SUPPLIER_BONUS

    score = min(round(score, 3), 1.0)
    return score, _label(score)


def compute_residual_risk(
    inherent_score: float,
    certifications: list[str] | None,
) -> tuple[float, str]:
    """Return (residual_risk_score, residual_label) after applying certification credits."""
    credit = sum(_CERT_CREDITS.get(c, 0.0) for c in (certifications or []))
    residual = round(max(0.0, inherent_score - credit), 3)
    return residual, _label(residual)
