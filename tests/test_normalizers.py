"""Layer 2 tests: enum normalization for all four normalizer functions."""
import pytest
from backend.services._normalizers import (
    normalize_category,
    normalize_data_access,
    normalize_risk_label,
    normalize_spend_type,
)


# ── spend_type ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("annual", "recurring"),
    ("Annual", "recurring"),
    ("ANNUAL", "recurring"),
    ("monthly", "recurring"),
    ("quarterly", "recurring"),
    ("yearly", "recurring"),
    ("SaaS", "subscription"),
    ("saas", "subscription"),
    ("subscription", "subscription"),
    ("license", "subscription"),
    ("one-time", "one-time"),
    ("one_time", "one-time"),
    ("onetime", "one-time"),
    ("once", "one-time"),
    ("single", "one-time"),
])
def test_normalize_spend_type_known(raw, expected):
    assert normalize_spend_type(raw) == expected


def test_normalize_spend_type_unknown_falls_back():
    assert normalize_spend_type("mystery_value") == "one-time"


def test_normalize_spend_type_empty_falls_back():
    assert normalize_spend_type("") == "one-time"


# ── category ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("software", "Software"),
    ("Software", "Software"),
    ("saas", "Software"),
    ("tool", "Software"),
    ("hardware", "Hardware"),
    ("device", "Hardware"),
    ("equipment", "Hardware"),
    ("services", "Services"),
    ("consulting", "Services"),
    ("contractor", "Services"),
    ("marketing", "Marketing"),
    ("advertising", "Marketing"),
    ("legal", "Legal"),
    ("legal services", "Legal"),
    ("other", "Other"),
])
def test_normalize_category_known(raw, expected):
    assert normalize_category(raw) == expected


def test_normalize_category_unknown_falls_back():
    assert normalize_category("procurement") == "Other"


# ── data_access ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("none", "none"),
    ("no data", "none"),
    ("no access", "none"),
    ("internal", "internal"),
    ("company data", "internal"),
    ("confidential", "confidential"),
    ("sensitive", "confidential"),
    ("restricted", "confidential"),
    ("personal_data", "personal_data"),
    ("personal data", "personal_data"),
    ("pii", "personal_data"),
    ("gdpr", "personal_data"),
    ("customer_data", "personal_data"),
    ("customer data", "personal_data"),
    ("user data", "personal_data"),
])
def test_normalize_data_access_known(raw, expected):
    assert normalize_data_access(raw) == expected


def test_normalize_data_access_unknown_falls_back():
    assert normalize_data_access("classified") == "none"


# ── risk_label ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("low", "low"),
    ("low risk", "low"),
    ("medium", "medium"),
    ("moderate", "medium"),
    ("med", "medium"),
    ("high", "high"),
    ("high risk", "high"),
    ("critical", "critical"),
    ("very high", "critical"),
])
def test_normalize_risk_label_known(raw, expected):
    assert normalize_risk_label(raw) == expected


def test_normalize_risk_label_unknown_falls_back():
    assert normalize_risk_label("extreme danger") == "medium"


# ── None / non-string safety ───────────────────────────────────────────────────

def test_normalize_spend_type_none_falls_back():
    assert normalize_spend_type(None) == "one-time"


def test_normalize_category_none_falls_back():
    assert normalize_category(None) == "Other"


def test_normalize_data_access_none_falls_back():
    assert normalize_data_access(None) == "none"


def test_normalize_risk_label_none_falls_back():
    assert normalize_risk_label(None) == "medium"


def test_normalize_spend_type_whitespace():
    assert normalize_spend_type("  annual  ") == "recurring"


def test_normalize_data_access_whitespace():
    assert normalize_data_access("  personal data  ") == "personal_data"
