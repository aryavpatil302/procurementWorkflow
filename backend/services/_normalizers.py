"""
Enum normalization for Llama 3.3-70b tool-call outputs.

Llama frequently ignores JSON Schema `enum` constraints and returns creative
values like "annual", "SaaS", "customer_data". These lookup tables map known
variants to the canonical values our policy engine and DB schema expect.

Each function falls back to a safe default rather than crashing, so a bad
LLM response causes a soft error (wrong category) rather than a 500.
"""

_SPEND_TYPE_MAP = {
    "one-time": "one-time",
    "one_time": "one-time",
    "onetime": "one-time",
    "once": "one-time",
    "single": "one-time",
    "recurring": "recurring",
    "monthly": "recurring",
    "annual": "recurring",
    "annually": "recurring",
    "yearly": "recurring",
    "quarterly": "recurring",
    "subscription": "subscription",
    "sub": "subscription",
    "saas": "subscription",
    "license": "subscription",
}

_CATEGORY_MAP = {
    "software": "Software",
    "saas": "Software",
    "app": "Software",
    "application": "Software",
    "tool": "Software",
    "hardware": "Hardware",
    "device": "Hardware",
    "equipment": "Hardware",
    "services": "Services",
    "consulting": "Services",
    "professional services": "Services",
    "contractor": "Services",
    "freelance": "Services",
    "marketing": "Marketing",
    "advertising": "Marketing",
    "pr": "Marketing",
    "legal": "Legal",
    "legal services": "Legal",
    "law": "Legal",
    "other": "Other",
}

_DATA_ACCESS_MAP = {
    "none": "none",
    "no data": "none",
    "no access": "none",
    "internal": "internal",
    "internal data": "internal",
    "company data": "internal",
    "confidential": "confidential",
    "sensitive": "confidential",
    "restricted": "confidential",
    "personal_data": "personal_data",
    "personal data": "personal_data",
    "pii": "personal_data",
    "gdpr": "personal_data",
    "customer_data": "personal_data",
    "customer data": "personal_data",
    "user data": "personal_data",
}

_RISK_LABEL_MAP = {
    "low": "low",
    "low risk": "low",
    "medium": "medium",
    "moderate": "medium",
    "med": "medium",
    "high": "high",
    "high risk": "high",
    "critical": "critical",
    "very high": "critical",
}


def normalize_spend_type(raw) -> str:
    if not isinstance(raw, str):
        return "one-time"
    return _SPEND_TYPE_MAP.get(raw.lower().strip(), "one-time")


def normalize_category(raw) -> str:
    if not isinstance(raw, str):
        return "Other"
    return _CATEGORY_MAP.get(raw.lower().strip(), "Other")


def normalize_data_access(raw) -> str:
    if not isinstance(raw, str):
        return "none"
    return _DATA_ACCESS_MAP.get(raw.lower().strip(), "none")


def normalize_risk_label(raw) -> str:
    if not isinstance(raw, str):
        return "medium"
    return _RISK_LABEL_MAP.get(raw.lower().strip(), "medium")
