# Phase 4: Supplier Intelligence — SRM, Renewals & Natural Language Query

## Omnea Product Mapping

| POC Component | Omnea Product | URL / Reference |
|--------------|--------------|-----|
| Suppliers tab — 360 profile card | Omnea SRM: unified supplier identity, contacts, contracts, risk, spend, documents, custom fields | `omnea.co/products/supplier-relationship-management` |
| Renewal calendar widget — 90/60/30-day alerts | Omnea Renewal Management: automated alerts, auto-launch renewal workflow | `omnea.co/products/renewal-management` |
| POST /suppliers/{id}/renew — pre-filled chat session | Omnea Renewal Management: auto-launch renewal with prior context | `omnea.co/products/renewal-management` |
| POST /suppliers/query — NL query against portfolio | Omnea MCP Server (launched April 30, 2026 — industry first): read-only NL queries via Claude/ChatGPT/Copilot | Omnea MCP launch, April 2026 |

**Key Quotes:**
- **Adecco Group VP of Procurement Strategy**: *"Omnea is the first one in our stack that actually plugged into the AI tools we were already using."* (MCP Server positioning)
- **Spotify VP Finance**: consolidation of supplier data from siloed spreadsheets into a single source of truth (SRM positioning)
- **Reach plc outcomes**: maverick spend reduced from 30% to 5%; 15% supplier roster trimmed

**Strategic Context:**
Omnea is repositioning from "procurement workflow tool" to "system of record for supplier intelligence." The supplier 360 profile — identity, risk, certifications, contracts, spend, renewals, all in one place — makes that positioning real. The MCP Server (April 30, 2026) is the capstone: it makes Omnea's data queryable from any AI tool the organisation already uses.

---

## What This Phase Builds

The Suppliers tab in `dashboard.html` becomes a full SRM interface with three stacked zones:

1. **Natural language query bar** — ask plain-English questions about the supplier portfolio, powered by Groq/Llama against live `supplier_records` data
2. **Renewal calendar widget** — upcoming renewals with 90/60/30-day colour-coded urgency and one-click workflow launch
3. **Supplier list with 360 profile cards** — expandable cards showing the full supplier profile created in Phase 3

---

## 1. `backend/services/supplier_intelligence.py` — Full File

Create this file. It contains `compute_renewal_status()`, `supplier_record_to_dict()`, and `query_supplier_portfolio()`. The query function uses `get_client()` and `call_with_retry()` from `_groq_utils` — never imports Groq directly.

```python
"""
Supplier intelligence service.

compute_renewal_status()    — live computation from contract_expiry_date
supplier_record_to_dict()   — serialise SupplierRecordORM to dict
query_supplier_portfolio()  — NL query against supplier data via Groq/Llama
"""

import json
from datetime import datetime, timezone
from typing import Optional

from backend.services._groq_utils import MODEL, call_with_retry, get_client


def compute_renewal_status(contract_expiry_date: Optional[datetime]) -> str:
    """
    Compute renewal status from the contract expiry date. Called on every read.
    Never rely on the stored renewal_status field alone — always compute live.

    Returns: "overdue" | "due_30" | "due_60" | "due_90" | "active" | "no_expiry"
    """
    if not contract_expiry_date:
        return "no_expiry"

    today = datetime.now(timezone.utc).date()
    expiry = (
        contract_expiry_date.date()
        if isinstance(contract_expiry_date, datetime)
        else contract_expiry_date
    )
    days_until = (expiry - today).days

    if days_until < 0:
        return "overdue"
    elif days_until <= 30:
        return "due_30"
    elif days_until <= 60:
        return "due_60"
    elif days_until <= 90:
        return "due_90"
    else:
        return "active"


def supplier_record_to_dict(record, compute_status: bool = True) -> dict:
    """
    Serialise a SupplierRecordORM to a dict, with live-computed renewal_status.
    Fields included match exactly what POST /suppliers/query sends to the LLM.
    """
    status = (
        compute_renewal_status(record.contract_expiry_date)
        if compute_status
        else record.renewal_status
    )

    days_until = None
    if record.contract_expiry_date:
        today = datetime.now(timezone.utc).date()
        expiry = (
            record.contract_expiry_date.date()
            if isinstance(record.contract_expiry_date, datetime)
            else record.contract_expiry_date
        )
        days_until = (expiry - today).days

    return {
        # Identity
        "id": record.id,
        "supplier_name": record.supplier_name,
        "supplier_website": record.supplier_website,
        "category": record.category,
        "legal_name": record.legal_name,
        "registered_address": record.registered_address,
        "company_number": record.company_number,
        # Risk
        "risk_tier": record.risk_tier,
        "inherent_risk_score": record.inherent_risk_score,
        "residual_risk_score": record.residual_risk_score,
        "certifications": record.certifications or [],
        # Contract
        "relationship_owner": record.relationship_owner,
        "contract_value": record.contract_value,
        "contract_start_date": record.contract_start_date.isoformat() if record.contract_start_date else None,
        "contract_expiry_date": record.contract_expiry_date.isoformat() if record.contract_expiry_date else None,
        "days_until_expiry": days_until,
        "renewal_status": status,
        # Geography & data
        "geography": record.geography,
        "data_access": record.data_access,
        # Assessment
        "assessment_status": record.assessment_status,
        # Contacts
        "primary_contact_name": record.primary_contact_name,
        "primary_contact_email": record.primary_contact_email,
        "primary_contact_title": record.primary_contact_title,
        # Audit
        "first_engaged": record.first_engaged.isoformat() if record.first_engaged else None,
        "last_reviewed": record.last_reviewed.isoformat() if record.last_reviewed else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def query_supplier_portfolio(query: str, suppliers: list[dict]) -> dict:
    """
    Answer a plain-English question about the supplier portfolio.
    Uses get_client() and call_with_retry() from _groq_utils — never imports Groq directly.

    The system prompt explicitly names the fields available so the LLM can answer
    common queries about certifications, risk tier, expiry dates, geography, and spend.

    Returns: { answer: str, suppliers_referenced: list[str], query: str }
    """
    client = get_client()

    portfolio_json = json.dumps(suppliers, indent=2, default=str)

    system_prompt = (
        "You are a procurement intelligence assistant with read-only access to a supplier portfolio. "
        "Answer the user's question concisely and factually based only on the data provided. "
        "If the answer is not in the data, say so clearly. Do not speculate or invent information. "
        "When referencing suppliers, mention them by name. "
        "Format monetary values as currency (e.g. £10,000) where appropriate.\n\n"
        "Each supplier record contains the following fields:\n"
        "  supplier_name, category, risk_tier (low/medium/high/critical),\n"
        "  certifications (list), contract_expiry_date, days_until_expiry,\n"
        "  renewal_status (overdue/due_30/due_60/due_90/active/no_expiry),\n"
        "  contract_value (annual spend in GBP), geography, data_access,\n"
        "  relationship_owner, inherent_risk_score, residual_risk_score,\n"
        "  assessment_status, primary_contact_name, primary_contact_email.\n\n"
        f"Supplier portfolio:\n{portfolio_json}"
    )

    try:
        response = call_with_retry(
            client,
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0.1,   # Low — factual, deterministic answers
            max_tokens=400,
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        answer = f"Query failed: {e!s}. Please try again."

    # Highlight which suppliers are mentioned in the answer
    mentioned = [
        s["supplier_name"]
        for s in suppliers
        if s["supplier_name"].lower() in answer.lower()
    ]

    return {
        "answer": answer,
        "suppliers_referenced": mentioned,
        "query": query,
    }
```

**Example queries the demo supports:**

| Query | Expected answer type |
|-------|---------------------|
| "Which suppliers renew in the next 60 days?" | Suppliers where `days_until_expiry <= 60` |
| "Which suppliers have personal data access?" | Filtered by `data_access = personal_data` |
| "Which high-risk suppliers are missing SOC 2?" | Cross-reference `risk_tier = high` + `certifications` |
| "Who is the relationship owner for Workday?" | Single field lookup |
| "What is our total annual spend across all approved suppliers?" | Sum of `contract_value` fields |
| "Which suppliers are deployed in EU or Global geography?" | Filtered by `geography` |
| "How many suppliers do we have by category?" | Aggregation by `category` |

---

## 2. Register Router in `backend/main.py`

Add after the Phase 3 supplier_portal router registration:

```python
# Add to imports:
from backend.routers import suppliers as suppliers_router

# Add after existing include_router calls:
app.include_router(suppliers_router.router)
```

---

## 3. `backend/routers/suppliers.py` — Full File

**Critical route ordering:** `POST /suppliers/query` and `GET /suppliers/renewals` must be registered before `GET /suppliers/{supplier_id}` to prevent FastAPI from matching "query" or "renewals" as a path parameter. The order in this file is correct — maintain it exactly.

```python
"""
Suppliers router (SRM + Renewal Management + NL Query).

Route structure (all under prefix="/suppliers"):
  GET  /suppliers                   — list all supplier records with live renewal status
  GET  /suppliers/renewals          — upcoming renewals within N days
  GET  /suppliers/{supplier_id}     — full 360 profile + related requests
  POST /suppliers/{supplier_id}/renew — launch renewal workflow (new chat session)
  POST /suppliers/query             — natural language portfolio query

NOTE: /suppliers/renewals and /suppliers/query must come BEFORE /suppliers/{supplier_id}
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ProcurementRequestORM, SupplierRecordORM
from backend.services.supplier_intelligence import (
    compute_renewal_status,
    query_supplier_portfolio,
    supplier_record_to_dict,
)

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


# ── List all supplier records ─────────────────────────────────────────────────

@router.get("")
def list_suppliers(db: Session = Depends(get_db)):
    """
    Return all supplier records with live-computed renewal status.
    Write-through: updates stored renewal_status if the computed value has changed.
    """
    records = db.query(SupplierRecordORM).order_by(SupplierRecordORM.supplier_name).all()
    result = []
    for record in records:
        d = supplier_record_to_dict(record)
        if record.renewal_status != d["renewal_status"]:
            record.renewal_status = d["renewal_status"]
        result.append(d)
    db.commit()
    return result


# ── Upcoming renewals ─────────────────────────────────────────────────────────

@router.get("/renewals")
def get_renewals(days: Optional[int] = 90, db: Session = Depends(get_db)):
    """
    Return all suppliers with renewals within the next `days` days, plus any overdue.
    Sorted by days_until_expiry (overdue first, then soonest).
    Default window: 90 days.
    """
    records = db.query(SupplierRecordORM).all()
    result = []
    for record in records:
        d = supplier_record_to_dict(record)
        status = d["renewal_status"]
        if status == "overdue":
            result.append(d)
        elif d["days_until_expiry"] is not None and 0 <= d["days_until_expiry"] <= days:
            result.append(d)

    result.sort(key=lambda x: x["days_until_expiry"] if x["days_until_expiry"] is not None else -999)
    return result


# ── Natural language portfolio query ─────────────────────────────────────────
# MUST come before /{supplier_id} to avoid path capture

class QueryRequest(BaseModel):
    query: str


@router.post("/query")
def query_portfolio(body: QueryRequest, db: Session = Depends(get_db)):
    """
    Answer a plain-English question about the supplier portfolio.
    Fetches all supplier records, passes them to Groq/Llama with the question.
    Returns: { answer, suppliers_referenced, query }
    """
    records = db.query(SupplierRecordORM).all()

    if not records:
        return {
            "answer": "No supplier records found. Complete the intake → approval → supplier assessment flow to create supplier records.",
            "suppliers_referenced": [],
            "query": body.query,
        }

    suppliers_list = [supplier_record_to_dict(r) for r in records]
    return query_supplier_portfolio(query=body.query, suppliers=suppliers_list)


# ── Full 360 profile for a single supplier ────────────────────────────────────

@router.get("/{supplier_id}")
def get_supplier(supplier_id: str, db: Session = Depends(get_db)):
    """
    Full 360 profile for a single supplier, including related procurement requests.
    """
    record = db.query(SupplierRecordORM).filter_by(id=supplier_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Supplier not found")

    d = supplier_record_to_dict(record)

    related_requests = (
        db.query(ProcurementRequestORM)
        .filter(ProcurementRequestORM.supplier_name == record.supplier_name)
        .order_by(ProcurementRequestORM.created_at.desc())
        .limit(10)
        .all()
    )
    d["related_requests"] = [
        {
            "id": r.id,
            "status": r.status,
            "spend_amount": r.spend_amount,
            "requester_name": r.requester_name,
            "department": r.department,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in related_requests
    ]

    return d


# ── Trigger renewal workflow ───────────────────────────────────────────────────

@router.post("/{supplier_id}/renew")
def trigger_renewal(supplier_id: str, db: Session = Depends(get_db)):
    """
    Launch a renewal review by creating a new session pre-populated with supplier context.
    The frontend stores the prefill_message in sessionStorage and opens chat.html with the session ID.
    """
    record = db.query(SupplierRecordORM).filter_by(id=supplier_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Supplier not found")

    new_session_id = str(uuid.uuid4())

    expiry_str = (
        record.contract_expiry_date.strftime("%d %B %Y")
        if record.contract_expiry_date else "not set"
    )
    spend_str = (
        f"£{record.contract_value:,.0f}/year"
        if record.contract_value else "unknown"
    )
    certs_str = (
        ", ".join(record.certifications)
        if record.certifications else "none declared"
    )

    prefill_message = (
        f"This is a renewal review for {record.supplier_name}. "
        f"Current contract expires {expiry_str}. "
        f"Previous spend: {spend_str}. "
        f"Category: {record.category or 'unknown'}. "
        f"Geography: {record.geography or 'unknown'}. "
        f"Data access: {record.data_access or 'unknown'}. "
        f"Certifications on file: {certs_str}. "
        f"Current risk tier: {record.risk_tier or 'unknown'}. "
        f"Relationship owner: {record.relationship_owner or 'unknown'}. "
        f"Please confirm whether any of these details have changed and whether "
        f"the business still requires this supplier."
    )

    return {
        "session_id": new_session_id,
        "supplier_id": supplier_id,
        "supplier_name": record.supplier_name,
        "prefill_message": prefill_message,
        "redirect_url": f"chat.html?session_id={new_session_id}&renewal=true&prefill={new_session_id}",
    }
```

---

## 4. Frontend: Suppliers Tab in `dashboard.html`

### Tab Structure

The Suppliers tab (placeholder in Phase 2) now gets full content. Three visual zones stacked vertically:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  QUERY BAR                                                                  │
│  "Ask about your supplier portfolio..."                                     │
│  Chips: [Renewals in 60 days] [Personal data access] [High-risk, no SOC 2] │
│  ─────────────────────────────────────────────────────────                  │
│  Query result card (appears after query)                                    │
└─────────────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────────────┐
│  RENEWAL CALENDAR (shown if any renewals within 90 days)                   │
│  Supplier | Category | Risk | Value | Expiry | Days | Status | Action      │
└─────────────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────────────┐
│  ALL SUPPLIERS                                                              │
│  Expandable 360 profile cards (one per supplier record)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Suppliers Tab HTML (add inside `#tab-suppliers`)

```html
<div id="tab-suppliers" class="tab-content">

  <!-- Query bar -->
  <div class="query-bar">
    <div class="query-input-row">
      <input type="text" id="portfolio-query" placeholder="Ask about your supplier portfolio..."
        onkeydown="if(event.key==='Enter') runQuery()">
      <button class="btn-query" onclick="runQuery()">Ask</button>
    </div>
    <div class="query-chips">
      <span class="chip" onclick="setQuery('Which suppliers renew in the next 60 days?')">Renewals in 60 days</span>
      <span class="chip" onclick="setQuery('Which suppliers have personal data access?')">Personal data access</span>
      <span class="chip" onclick="setQuery('Which high-risk suppliers are missing SOC 2?')">High-risk, missing SOC 2</span>
      <span class="chip" onclick="setQuery('What is our total annual spend across all suppliers?')">Total annual spend</span>
    </div>
    <div id="query-result" style="display:none" class="query-result-card"></div>
  </div>

  <!-- Renewal calendar -->
  <div id="renewal-calendar" style="display:none" class="renewal-section"></div>

  <!-- Supplier list -->
  <div class="section-title">All Suppliers</div>
  <div id="suppliers-list"></div>

</div>
```

### Query Bar JavaScript

```javascript
function setQuery(text) {
  document.getElementById('portfolio-query').value = text;
  runQuery();
}

async function runQuery() {
  const query = document.getElementById('portfolio-query').value.trim();
  if (!query) return;

  const resultDiv = document.getElementById('query-result');
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = `<div class="loading-pulse">Querying supplier portfolio...</div>`;

  try {
    const response = await fetch('/suppliers/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await response.json();

    const referenced = data.suppliers_referenced.length > 0
      ? `<div class="query-refs">Suppliers referenced: ${data.suppliers_referenced.map(s =>
          `<span class="ref-badge">${s}</span>`
        ).join(' ')}</div>`
      : '';

    resultDiv.innerHTML = `
      <div class="query-result-inner">
        <div class="query-question">"${data.query}"</div>
        <div class="query-answer">${data.answer}</div>
        ${referenced}
      </div>
    `;
  } catch (e) {
    resultDiv.innerHTML = `<div class="query-error">Query failed: ${e.message}</div>`;
  }
}
```

### Renewal Calendar Widget

```javascript
async function loadRenewalCalendar() {
  const renewals = await fetch('/suppliers/renewals?days=90').then(r => r.json());
  const container = document.getElementById('renewal-calendar');

  if (renewals.length === 0) {
    container.style.display = 'none';
    return;
  }

  const STATUS_CONFIG = {
    overdue: { label: 'OVERDUE', rowClass: 'row-red',   badgeClass: 'badge-red' },
    due_30:  { label: '30 DAYS', rowClass: 'row-red',   badgeClass: 'badge-red' },
    due_60:  { label: '60 DAYS', rowClass: 'row-amber', badgeClass: 'badge-amber' },
    due_90:  { label: '90 DAYS', rowClass: 'row-green', badgeClass: 'badge-green' },
  };
  const riskColours = { low: '#10B981', medium: '#F59E0B', high: '#EF4444', critical: '#7C2D12' };

  const rows = renewals.map(s => {
    const cfg = STATUS_CONFIG[s.renewal_status] || { label: s.renewal_status, rowClass: '', badgeClass: '' };
    const daysText = s.days_until_expiry < 0
      ? `${Math.abs(s.days_until_expiry)} days overdue`
      : `${s.days_until_expiry} days`;
    const expiryDate = s.contract_expiry_date
      ? new Date(s.contract_expiry_date).toLocaleDateString('en-GB') : 'Not set';
    const value = s.contract_value ? `£${Number(s.contract_value).toLocaleString()}` : '—';

    return `
      <tr class="${cfg.rowClass}">
        <td><strong>${s.supplier_name}</strong></td>
        <td>${s.category || '—'}</td>
        <td>
          <span class="risk-dot" style="background:${riskColours[s.risk_tier] || '#9CA3AF'}"></span>
          ${(s.risk_tier || '').toUpperCase()}
        </td>
        <td>${value}</td>
        <td>${expiryDate}</td>
        <td>${daysText}</td>
        <td><span class="renewal-badge ${cfg.badgeClass}">${cfg.label}</span></td>
        <td>
          <button class="btn-renew" onclick="launchRenewal('${s.id}', '${s.supplier_name}')">
            Launch renewal review
          </button>
        </td>
      </tr>
    `;
  }).join('');

  container.style.display = 'block';
  container.innerHTML = `
    <div class="section-title">Upcoming Renewals</div>
    <table class="renewal-table">
      <thead>
        <tr>
          <th>Supplier</th><th>Category</th><th>Risk</th>
          <th>Contract Value</th><th>Expiry Date</th><th>Days Left</th>
          <th>Status</th><th>Action</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function launchRenewal(supplierId, supplierName) {
  if (!confirm(`Launch renewal review for ${supplierName}?`)) return;
  try {
    const data = await fetch(`/suppliers/${supplierId}/renew`, { method: 'POST' }).then(r => r.json());
    // Store prefill in sessionStorage so chat.html can read it on open
    sessionStorage.setItem(`renewal_prefill_${data.session_id}`, data.prefill_message);
    window.open(data.redirect_url, '_blank');
  } catch (e) {
    alert(`Could not launch renewal: ${e.message}`);
  }
}
```

### Supplier 360 Card

```javascript
async function loadSuppliers() {
  const suppliers = await fetch('/suppliers').then(r => r.json());
  const container = document.getElementById('suppliers-list');

  if (suppliers.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        No supplier records yet. Complete intake → approval → supplier assessment to create supplier records.
      </div>`;
    return;
  }
  container.innerHTML = suppliers.map(s => renderSupplierCard(s)).join('');
}

const KEY_CERTS = ["SOC 2 Type II", "ISO 27001", "GDPR compliant", "Cyber Essentials", "DORA"];

function renderCertBadges(heldCerts) {
  const heldSet = new Set(heldCerts);
  return KEY_CERTS.map(cert => {
    const held = heldSet.has(cert);
    return `<span class="cert-badge ${held ? 'cert-held' : 'cert-missing'}" title="${held ? 'Certified' : 'Not declared'}">
      ${held ? '✓' : '✗'} ${cert}
    </span>`;
  }).join('');
}

function renderRenewalBadge(status, daysUntil) {
  const configs = {
    overdue:   { label: 'OVERDUE',            cls: 'renewal-overdue' },
    due_30:    { label: `${daysUntil}d — URGENT`, cls: 'renewal-urgent' },
    due_60:    { label: `${daysUntil}d`,       cls: 'renewal-soon' },
    due_90:    { label: `${daysUntil}d`,       cls: 'renewal-notice' },
    active:    { label: 'Active',              cls: 'renewal-active' },
    no_expiry: { label: 'No expiry set',       cls: 'renewal-none' },
  };
  const cfg = configs[status] || { label: status, cls: '' };
  return `<span class="renewal-badge ${cfg.cls}">${cfg.label}</span>`;
}

function renderSupplierCard(s) {
  const riskColours = { low: '#10B981', medium: '#F59E0B', high: '#EF4444', critical: '#7C2D12' };
  const riskBg     = { low: '#D1FAE5', medium: '#FEF3C7', high: '#FEE2E2', critical: '#FEE2E2' };
  const tier = (s.risk_tier || 'unknown').toLowerCase();
  const certBadges = renderCertBadges(s.certifications || []);
  const renewalBadge = renderRenewalBadge(s.renewal_status, s.days_until_expiry);
  const expiryDate = s.contract_expiry_date ? new Date(s.contract_expiry_date).toLocaleDateString('en-GB') : 'Not set';
  const startDate  = s.contract_start_date  ? new Date(s.contract_start_date).toLocaleDateString('en-GB')  : 'Not set';
  const value = s.contract_value ? `£${Number(s.contract_value).toLocaleString()}` : 'Not set';

  return `
    <div class="supplier-card">
      <!-- Collapsed header -->
      <div class="supplier-card-header" onclick="toggleSupplierCard('${s.id}')">
        <div class="supplier-header-left">
          <div class="supplier-logo-placeholder">${s.supplier_name.charAt(0).toUpperCase()}</div>
          <div>
            <div class="supplier-name">${s.supplier_name}</div>
            <div class="supplier-meta">
              ${s.category ? `<span class="cat-badge">${s.category}</span>` : ''}
              <span class="risk-badge-sm" style="background:${riskBg[tier]};color:${riskColours[tier]}">
                ${tier.toUpperCase()} RISK
              </span>
              ${s.supplier_website ? `<a href="${s.supplier_website}" target="_blank" class="website-link">↗</a>` : ''}
            </div>
          </div>
        </div>
        <div class="supplier-header-right">
          ${renewalBadge}
          <span class="chevron">▼</span>
        </div>
      </div>

      <!-- Expanded 360 profile -->
      <div class="supplier-360" id="s360-${s.id}" style="display:none">
        <div class="profile-grid">

          <div class="profile-section">
            <h4>Risk Profile</h4>
            <div class="risk-scores">
              <div class="score-item"><span class="score-label">Inherent</span><span class="score-value">${s.inherent_risk_score?.toFixed(3) || '—'}</span></div>
              <span class="score-arrow">→</span>
              <div class="score-item"><span class="score-label">Residual</span><span class="score-value">${s.residual_risk_score?.toFixed(3) || '—'}</span></div>
            </div>
            <div class="cert-badges-row">${certBadges}</div>
          </div>

          <div class="profile-section">
            <h4>Contract</h4>
            <div class="profile-field"><span>Value</span><strong>${value}</strong></div>
            <div class="profile-field"><span>Start</span><strong>${startDate}</strong></div>
            <div class="profile-field"><span>Expiry</span><strong>${expiryDate}</strong></div>
            <div class="profile-field"><span>Renewal</span>${renewalBadge}</div>
          </div>

          <div class="profile-section">
            <h4>Contacts</h4>
            <div class="profile-field"><span>Owner</span><strong>${s.relationship_owner || '—'}</strong></div>
            ${s.primary_contact_name ? `
              <div class="profile-field"><span>Supplier contact</span><strong>${s.primary_contact_name}</strong></div>
              ${s.primary_contact_title ? `<div class="profile-field"><span>Title</span><strong>${s.primary_contact_title}</strong></div>` : ''}
              ${s.primary_contact_email ? `<div class="profile-field"><span>Email</span><a href="mailto:${s.primary_contact_email}">${s.primary_contact_email}</a></div>` : ''}
            ` : '<div class="profile-field muted">No contact — assessment pending</div>'}
          </div>

          <div class="profile-section">
            <h4>Geography &amp; Data</h4>
            <div class="profile-field"><span>Geography</span><strong>${s.geography || '—'}</strong></div>
            <div class="profile-field"><span>Data access</span><strong>${s.data_access || '—'}</strong></div>
            <div class="profile-field"><span>Assessment</span>
              <span class="assess-badge assess-${s.assessment_status}">${s.assessment_status || 'unknown'}</span>
            </div>
            <div class="profile-field"><span>First engaged</span><strong>${s.first_engaged ? new Date(s.first_engaged).toLocaleDateString('en-GB') : '—'}</strong></div>
            <div class="profile-field"><span>Last reviewed</span><strong>${s.last_reviewed ? new Date(s.last_reviewed).toLocaleDateString('en-GB') : '—'}</strong></div>
          </div>

        </div>

        <!-- Related requests (loaded on expand) -->
        <div id="related-requests-${s.id}" class="related-requests">
          <h4>Related Requests</h4>
          <div class="loading-pulse">Loading...</div>
        </div>

        <!-- Actions -->
        <div class="card-actions-row">
          <button class="btn-action btn-primary" onclick="launchRenewal('${s.id}', '${s.supplier_name}')">
            Launch renewal review
          </button>
        </div>
      </div>
    </div>
  `;
}

async function toggleSupplierCard(supplierId) {
  const profile = document.getElementById(`s360-${supplierId}`);
  const isExpanded = profile.style.display === 'block';
  profile.style.display = isExpanded ? 'none' : 'block';
  if (!isExpanded) loadRelatedRequests(supplierId);
}

async function loadRelatedRequests(supplierId) {
  const container = document.getElementById(`related-requests-${supplierId}`);
  try {
    const data = await fetch(`/suppliers/${supplierId}`).then(r => r.json());
    const requests = data.related_requests || [];
    if (requests.length === 0) {
      container.innerHTML = '<h4>Related Requests</h4><p class="muted">No related requests found.</p>';
      return;
    }
    const statusColours = { approved: '#10B981', rejected: '#EF4444', in_review: '#3B82F6', pending: '#9CA3AF' };
    container.innerHTML = `
      <h4>Related Requests</h4>
      <div class="related-list">
        ${requests.map(r => `
          <div class="related-item">
            <span class="related-dot" style="background:${statusColours[r.status] || '#9CA3AF'}"></span>
            <span>${r.requester_name} (${r.department})</span>
            <span>${r.spend_amount ? '£' + Number(r.spend_amount).toLocaleString() : '—'}</span>
            <span class="related-status">${r.status}</span>
            <span class="muted">${r.created_at ? new Date(r.created_at).toLocaleDateString('en-GB') : ''}</span>
          </div>
        `).join('')}
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<h4>Related Requests</h4><p class="muted">Could not load.</p>`;
  }
}
```

### Load Suppliers Tab on Switch

The tab switch JS (in Phase 2) already calls these when the Suppliers tab is clicked:

```javascript
if (tab.dataset.tab === 'suppliers') {
  loadRenewalCalendar();
  loadSuppliers();
}
```

---

## 5. Renewal Pre-Fill in `chat.html`

Add to `chat.html` init to detect the renewal flag and pre-fill Aria's context:

```javascript
// At the top of chat.html init():
const urlParams = new URLSearchParams(window.location.search);
const isRenewal = urlParams.get('renewal') === 'true';
const prefillKey = urlParams.get('prefill');

if (isRenewal && prefillKey) {
  const prefillMsg = sessionStorage.getItem(`renewal_prefill_${prefillKey}`);
  if (prefillMsg) {
    // Inject as the first user message — Aria will use this as context
    // The chat function will receive it as the opening message of the renewal session
    addSystemContext(prefillMsg);
    sessionStorage.removeItem(`renewal_prefill_${prefillKey}`);
  }

  document.getElementById('chat-header').innerHTML += `
    <div class="renewal-banner">
      Renewal Review — previous contract context pre-loaded. Confirm changes and resubmit.
    </div>
  `;
}
```

---

## 6. CSS for Suppliers Tab

Add to `dashboard.html` `<style>` section:

```css
/* Query bar */
.query-bar { background: white; border: 1px solid #E5E7EB; border-radius: 10px; padding: 20px; margin-bottom: 20px; }
.query-input-row { display: flex; gap: 8px; }
.query-input-row input { flex: 1; padding: 10px 14px; border: 1px solid #D1D5DB; border-radius: 8px; font-size: 14px; }
.btn-query { background: #1E40AF; color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: 500; }
.query-chips { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.chip { background: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE; padding: 4px 12px; border-radius: 20px; font-size: 12px; cursor: pointer; }
.chip:hover { background: #DBEAFE; }
.query-result-card { margin-top: 12px; border-top: 1px solid #E5E7EB; padding-top: 12px; }
.query-question { font-size: 13px; color: #6B7280; font-style: italic; margin-bottom: 8px; }
.query-answer { font-size: 14px; color: #111827; line-height: 1.6; background: #F0FDF4; padding: 12px; border-radius: 6px; }
.query-refs { margin-top: 8px; font-size: 12px; color: #6B7280; }
.ref-badge { background: #DBEAFE; color: #1E40AF; padding: 2px 8px; border-radius: 4px; margin-left: 4px; }
.loading-pulse { color: #9CA3AF; font-size: 14px; padding: 12px 0; }

/* Renewal table */
.renewal-section { margin-bottom: 24px; }
.section-title { font-size: 14px; font-weight: 600; color: #374151; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
.renewal-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.renewal-table th { text-align: left; padding: 8px 12px; border-bottom: 2px solid #E5E7EB; color: #6B7280; font-size: 11px; text-transform: uppercase; }
.renewal-table td { padding: 10px 12px; border-bottom: 1px solid #F3F4F6; }
.row-red td   { background: #FFF5F5; }
.row-amber td { background: #FFFBEB; }
.row-green td { background: #F0FDF4; }
.badge-red   { background: #FEE2E2; color: #991B1B; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
.badge-amber { background: #FEF3C7; color: #92400E; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
.badge-green { background: #D1FAE5; color: #065F46; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
.btn-renew { background: #1E40AF; color: white; border: none; padding: 5px 12px; border-radius: 5px; cursor: pointer; font-size: 12px; }
.risk-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }

/* Supplier cards */
.supplier-card { background: white; border: 1px solid #E5E7EB; border-radius: 10px; margin-bottom: 12px; overflow: hidden; }
.supplier-card-header { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px; cursor: pointer; }
.supplier-card-header:hover { background: #F9FAFB; }
.supplier-header-left { display: flex; align-items: center; gap: 14px; }
.supplier-header-right { display: flex; align-items: center; gap: 10px; }
.supplier-logo-placeholder { width: 40px; height: 40px; background: #1E40AF; color: white; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 700; flex-shrink: 0; }
.supplier-name { font-size: 15px; font-weight: 600; }
.supplier-meta { display: flex; align-items: center; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
.cat-badge { background: #F3F4F6; color: #374151; font-size: 11px; padding: 2px 8px; border-radius: 4px; }
.risk-badge-sm { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
.website-link { font-size: 12px; color: #1E40AF; text-decoration: none; }
.renewal-badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.renewal-overdue { background: #FEE2E2; color: #991B1B; }
.renewal-urgent  { background: #FEE2E2; color: #991B1B; }
.renewal-soon    { background: #FEF3C7; color: #92400E; }
.renewal-notice  { background: #D1FAE5; color: #065F46; }
.renewal-active  { background: #D1FAE5; color: #065F46; }
.renewal-none    { background: #F3F4F6; color: #6B7280; }

/* 360 profile */
.supplier-360 { border-top: 1px solid #E5E7EB; padding: 20px; }
.profile-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 20px; }
.profile-section h4 { font-size: 11px; font-weight: 600; color: #6B7280; text-transform: uppercase; margin-bottom: 10px; letter-spacing: 0.05em; }
.profile-field { display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; margin-bottom: 6px; color: #374151; gap: 8px; }
.profile-field span:first-child { color: #6B7280; white-space: nowrap; }
.risk-scores { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.score-item { text-align: center; }
.score-label { font-size: 11px; color: #6B7280; display: block; }
.score-value { font-size: 18px; font-weight: 700; color: #111827; }
.score-arrow { font-size: 18px; color: #6B7280; }
.cert-badges-row { display: flex; flex-wrap: wrap; gap: 4px; }
.cert-badge { font-size: 11px; padding: 3px 7px; border-radius: 4px; }
.cert-held    { background: #D1FAE5; color: #065F46; }
.cert-missing { background: #F3F4F6; color: #9CA3AF; border: 1px solid #E5E7EB; }
.assess-badge { font-size: 11px; padding: 2px 6px; border-radius: 4px; }
.assess-completed   { background: #D1FAE5; color: #065F46; }
.assess-pending     { background: #FEF3C7; color: #92400E; }
.assess-in_progress { background: #DBEAFE; color: #1E40AF; }

/* Related requests */
.related-requests { border-top: 1px solid #F3F4F6; padding-top: 16px; margin-top: 4px; }
.related-requests h4 { font-size: 11px; font-weight: 600; color: #6B7280; text-transform: uppercase; margin-bottom: 10px; letter-spacing: 0.05em; }
.related-list { display: flex; flex-direction: column; gap: 6px; }
.related-item { display: flex; align-items: center; gap: 12px; font-size: 13px; padding: 6px 0; border-bottom: 1px solid #F9FAFB; }
.related-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.related-status { font-size: 11px; background: #F3F4F6; padding: 2px 6px; border-radius: 4px; }
.muted { color: #9CA3AF; font-size: 12px; }

/* Card actions */
.card-actions-row { border-top: 1px solid #F3F4F6; padding-top: 14px; display: flex; gap: 10px; }
.btn-action { padding: 7px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 500; }
.btn-primary   { background: #1E40AF; color: white; border: none; }
.btn-secondary { background: white; color: #374151; border: 1px solid #D1D5DB; }
```

---

## 7. What This Demonstrates (CSE Interview)

- **"Omnea launched the industry's first procurement MCP Server on April 30, 2026 — I built the local equivalent."** `POST /suppliers/query` takes a plain-English question, fetches the live supplier portfolio from SQLite, and answers using Groq/Llama with the data as context. "Which high-risk suppliers are missing SOC 2?" returns a factual answer from real data. That's the same pattern as Omnea's MCP Server — read-only natural language access to live procurement data.

- **"The Adecco Group VP of Procurement Strategy: 'Omnea is the first one in our stack that actually plugged into the AI tools we were already using.'"** The MCP Server makes Omnea's data queryable from Claude, ChatGPT, or Copilot. This query endpoint demonstrates why that matters: instead of opening a dashboard, you ask in plain English from wherever you already work.

- **"Omnea is repositioning from procurement workflow tool to system of record for supplier intelligence."** The supplier 360 profile — identity, risk, certifications, contracts, spend, geography, contacts, renewals — all in one place, built automatically from the intake → approval → assessment pipeline. No manual data entry. That's the system of record positioning.

- **"The renewal module solves a real problem."** Renewals happen by default because nobody remembered to review. 90/60/30-day alerts with automatic workflow launch make renewal an active decision. The "Launch renewal review" button creates a new Aria session pre-populated with: supplier name, expiry date, spend, geography, certifications, risk tier, relationship owner. The requester just confirms what's changed.

- **"Reach plc outcomes: maverick spend from 30% to 5%, 15% supplier consolidation."** Supplier intelligence makes these outcomes possible. When every supplier relationship is visible, tracked, and queryable, procurement leaders can see consolidation opportunities and catch maverick spend that bypassed the process.

- **"The Spotify VP Finance story."** Omnea SRM consolidates supplier data from siloed spreadsheets — each team managing their own supplier list in Google Sheets — into a single source of truth. The supplier records in this POC are exactly that: one row per supplier, built automatically from the procurement process.

- **"Live renewal status computation — no stale data."** `compute_renewal_status()` calculates from `contract_expiry_date` on every read. A supplier that was "90 DAYS" yesterday is "89 DAYS" today without any cron job or background task. The stored `renewal_status` field is a write-through cache; the source of truth is always the computed value.
