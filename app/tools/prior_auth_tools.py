"""
Prior authorization tools: deterministic payer rules + LLM wrapper + risk scoring.

Kept synchronous; I/O via Supabase (optional) and prior_auth_llm_tool → LLM.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.llm.prior_auth_llm import llm_prior_auth_decision
from app.schemas.prior_auth import PriorAuthAgentRequest
from supabase import Client

# --- Hardcoded payer / procedure rules (extend or move to DB later) ---

# CDT → prior auth + documentation expectations (dental RCM–style stubs)
_AUTH_BY_CODE: dict[str, dict[str, Any]] = {
    "D2740": {
        "requires_auth": True,
        "documents": [
            "Periapical or bitewing X-rays dated within benefit-plan window",
            "Clinical notes documenting crown medical necessity",
        ],
        "rules": [
            "D2740 (crown): many plans require prior authorization for major restorative services",
        ],
    },
    "D2750": {
        "requires_auth": True,
        "documents": ["Radiographs", "Narrative for crown necessity"],
        "rules": ["D2750 (crown): same prior auth considerations as D2740 family"],
    },
    "D7210": {
        "requires_auth": False,
        "documents": ["Pre-operative radiograph (per plan / medical necessity)"],
        "rules": [
            "D7210 (surgical extraction): prior auth often not required, but radiograph commonly required for claim support",
        ],
    },
    "D1110": {
        "requires_auth": False,
        "documents": [],
        "rules": ["D1110 (adult prophylaxis): typically no prior authorization"],
    },
    "D1120": {
        "requires_auth": False,
        "documents": [],
        "rules": ["D1120 (child prophylaxis): typically no prior authorization"],
    },
    "D4910": {
        "requires_auth": True,
        "documents": ["Periodontal charting", "Recent radiographs", "History of periodontal therapy"],
        "rules": ["D4910 (periodontal maintenance): may require auth or documentation when following active therapy"],
    },
    "D4341": {
        "requires_auth": True,
        "documents": ["Full periodontal charting", "Radiographs", "Treatment plan"],
        "rules": ["D4341 (SRP): many payers require authorization or detailed periodontal documentation"],
    },
    "D3330": {
        "requires_auth": True,
        "documents": ["Pre-op radiograph", "Clinical narrative for endodontic necessity"],
        "rules": ["D3330 (anterior endo): prior authorization possible depending on plan"],
    },
}

_NO_AUTH_PREVENTIVE = {"D1110", "D1120", "D0120", "D0140", "D0150"}


def _apply_fallback_auth_rules_stub(cdt_codes: list[str], insurance: str) -> dict[str, Any]:
    """
    Built-in CDT profiles + payer flavor (used when Supabase is off or as a union with DB rows).
    """
    codes_upper = [c.upper().strip() for c in cdt_codes]
    requires_auth = False
    required_documents: list[str] = []
    payer_rules: list[str] = []

    matched_any = False
    for code in codes_upper:
        profile = _AUTH_BY_CODE.get(code)
        if not profile:
            continue
        matched_any = True
        if profile["requires_auth"]:
            requires_auth = True
        required_documents.extend(profile["documents"])
        payer_rules.extend(profile["rules"])

    if codes_upper and all(c in _NO_AUTH_PREVENTIVE for c in codes_upper) and not requires_auth:
        payer_rules.append(
            "Preventive / evaluation codes only: prior authorization usually not required"
        )

    if "DELTA" in insurance.upper():
        payer_rules.append(
            "Delta Dental: confirm member-specific benefit booklet — auth requirements vary by employer group"
        )

    if not matched_any and codes_upper:
        payer_rules.append(
            "No explicit rule for listed CDT code(s) in engine; rely on LLM + manual payer verification"
        )

    required_documents = list(dict.fromkeys(required_documents))
    payer_rules = list(dict.fromkeys(payer_rules))

    return {
        "requires_auth": requires_auth,
        "required_documents": required_documents,
        "payer_rules": payer_rules,
    }


def apply_auth_rules_tool(
    cdt_codes: list[str],
    insurance: str,
    *,
    supabase: Client | None = None,
    patient_age: int | None = None,
) -> dict[str, Any]:
    """
    Deterministic rules: Supabase (`cdt_payer_rules_structured` + `v_rules_for_preauth_agent`)
    merged with built-in CDT profiles (OR on requires_auth; union lists).
    """
    stub = _apply_fallback_auth_rules_stub(cdt_codes, insurance)
    if supabase is None:
        return stub

    from app.tools.prior_auth_db import fetch_deterministic_prior_auth_from_supabase

    try:
        db = fetch_deterministic_prior_auth_from_supabase(
            supabase, cdt_codes, insurance, patient_age
        )
    except Exception as e:
        db = {
            "requires_auth": False,
            "required_documents": [],
            "payer_rules": [f"Prior auth DB (unexpected): {e}"],
        }

    return {
        "requires_auth": bool(stub["requires_auth"] or db["requires_auth"]),
        "required_documents": list(
            dict.fromkeys(list(db["required_documents"]) + list(stub["required_documents"]))
        ),
        "payer_rules": list(dict.fromkeys(list(db["payer_rules"]) + list(stub["payer_rules"]))),
    }


def prior_auth_llm_tool(settings: Settings, request: PriorAuthAgentRequest) -> dict[str, Any]:
    """
    Tool: package input and call the LLM layer (structured JSON).
    """
    payload: dict[str, Any] = {
        "cdt_codes": request.coding.cdt_codes,
        "icd10_codes": request.coding.icd10_codes,
        "coding_confidence": request.coding.confidence,
        "coding_justification": request.coding.justification,
        "coding_payer_flags": request.coding.payer_flags,
        "insurance": request.insurance,
        "clinical_note": request.clinical_note or "",
        "patient_age": request.patient_age,
    }
    return llm_prior_auth_decision(settings, payload)


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def assess_risk_tool(cdt_codes: list[str], documents: list[str]) -> dict[str, Any]:
    """
    Heuristic denial / admin risk from procedure mix and documentation burden.
    """
    codes_upper = [c.upper().strip() for c in cdt_codes]
    high_codes = {"D2740", "D2750", "D2751", "D2752", "D3330", "D3346", "D3347", "D3348", "D4341", "D4910"}
    medium_codes = {"D7210", "D7220", "D7230", "D7140", "D2950", "D2954"}

    level = "low"
    reasons: list[str] = []

    if any(c in high_codes for c in codes_upper):
        level = "high"
        reasons.append("High-cost or complex services present (crown, endo, perio therapy)")
    elif any(c in medium_codes for c in codes_upper):
        level = "medium"
        reasons.append("Surgical or major restorative codes may trigger documentation or medical necessity review")

    if len(documents) >= 4:
        if _RISK_ORDER[level] < _RISK_ORDER["medium"]:
            level = "medium"
        reasons.append("Heavy documentation checklist increases administrative denial risk if any item is missing")

    if not reasons:
        reasons.append("Routine profile: lower administrative risk if claims match documented services")

    return {
        "risk_level": level,
        "risk_reason": " ".join(reasons),
    }


def merge_risk_level(a: str, b: str) -> str:
    """Return the more severe of two risk levels (coerce unknown to medium)."""
    a_n = a.lower().strip() if a.lower().strip() in _RISK_ORDER else "medium"
    b_n = b.lower().strip() if b.lower().strip() in _RISK_ORDER else "medium"
    return a_n if _RISK_ORDER[a_n] >= _RISK_ORDER[b_n] else b_n
