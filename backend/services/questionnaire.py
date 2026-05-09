"""
Questionnaire service — adaptive supplier due diligence question bank.

Three cumulative tiers: standard includes all basic questions plus its own;
deep_due_diligence includes all of both. This mirrors Omnea's adaptive TPRM
questionnaire depth, where question count scales with supplier risk tier.
"""

# ── Question bank ─────────────────────────────────────────────────────────────
# All questions in sequential order. The first 5 are basic, next 5 standard,
# final 5 deep_due_diligence. Each tier is cumulative — higher tiers include
# all questions from lower tiers.

_ALL_QUESTIONS = [
    # ── Basic (q01–q05) ───────────────────────────────────────────────────────
    {
        "id": "q01",
        "section": "Company Information",
        "text": "What is your company's full legal name and registered address?",
        "type": "text",
        "required": True,
    },
    {
        "id": "q02",
        "section": "Company Information",
        "text": "Who is your primary security contact (full name and email address)?",
        "type": "text",
        "required": True,
    },
    {
        "id": "q03",
        "section": "Security Controls",
        "text": "Do you have a written information security policy?",
        "type": "yes_no",
        "required": True,
    },
    {
        "id": "q04",
        "section": "Data Handling",
        "text": "Describe how you will store and protect any data shared with you as part of this engagement.",
        "type": "text",
        "required": True,
    },
    {
        "id": "q05",
        "section": "Data Handling",
        "text": "Have you experienced a data breach or cyber security incident in the last 3 years?",
        "type": "yes_no",
        "required": True,
    },
    # ── Standard (q06–q10) ────────────────────────────────────────────────────
    {
        "id": "q06",
        "section": "Certifications",
        "text": "Do you hold any security certifications (e.g. ISO 27001, SOC 2, Cyber Essentials)?",
        "type": "yes_no",
        "required": True,
    },
    {
        "id": "q07",
        "section": "Certifications",
        "text": "Please upload any current certification documents.",
        "type": "upload",
        "required": False,
    },
    {
        "id": "q08",
        "section": "Sub-processors",
        "text": "Do you engage sub-processors or third-party suppliers to deliver this service?",
        "type": "yes_no",
        "required": True,
    },
    {
        "id": "q09",
        "section": "Sub-processors",
        "text": "If yes, list the sub-processors involved and describe what data they can access.",
        "type": "text",
        "required": False,
    },
    {
        "id": "q10",
        "section": "Incident Response",
        "text": "Do you have a formal incident response plan? If so, what is the notification timeline for affected customers?",
        "type": "text",
        "required": True,
    },
    # ── Deep due diligence (q11–q15) ──────────────────────────────────────────
    {
        "id": "q11",
        "section": "Penetration Testing",
        "text": "Please upload your most recent penetration test report or executive summary (dated within the last 12 months).",
        "type": "upload",
        "required": True,
    },
    {
        "id": "q12",
        "section": "GDPR & Privacy",
        "text": "Describe your GDPR compliance programme, including your lawful basis for processing personal data.",
        "type": "text",
        "required": True,
    },
    {
        "id": "q13",
        "section": "Regulatory History",
        "text": "Have you been subject to any regulatory investigations, enforcement actions, or fines in the last 5 years?",
        "type": "yes_no",
        "required": True,
    },
    {
        "id": "q14",
        "section": "Financial Stability",
        "text": "Please upload evidence of financial stability (most recent annual accounts or a current credit report).",
        "type": "upload",
        "required": True,
    },
    {
        "id": "q15",
        "section": "Business Continuity",
        "text": "Describe your business continuity and disaster recovery arrangements, including your target recovery time.",
        "type": "text",
        "required": True,
    },
]

_DEPTH_SLICE = {
    "basic":               5,
    "standard":            10,
    "deep_due_diligence":  15,
}


def get_questions(questionnaire_depth: str) -> list[dict]:
    """Return the question list for the given depth tier.

    Tiers are cumulative: standard includes all basic questions.
    Unknown depth falls back to basic (5 questions) rather than crashing.
    """
    count = _DEPTH_SLICE.get(questionnaire_depth, _DEPTH_SLICE["basic"])
    return _ALL_QUESTIONS[:count]
