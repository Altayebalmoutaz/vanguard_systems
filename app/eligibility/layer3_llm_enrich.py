"""
Layer 3 optional enrichment: deterministic numeric fixes + bounded LLM annotations.

LLM is used only when ``ELIGIBILITY_LAYER3_LLM_ENRICH_ENABLED`` is true and an
OpenRouter-compatible API key is configured. It must not override extracted
booleans or financial amounts — only ``coverage_confidence`` and explanatory notes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.eligibility.config import get_settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_ALLOWED_NULL_NOTE_KEYS = frozenset(
    {
        "is_in_network",
        "is_covered",
        "procedure_covered",
        "deductible_remaining",
        "deductible_total",
        "max_remaining",
        "max_total",
        "copay",
        "coinsurance",
        "patient_responsibility",
    }
)

_SYSTEM_PROMPT = """You annotate a dental/medical eligibility snapshot that was extracted from an X12 271 JSON.
You MUST NOT invent coverage, amounts, or network status. Use only the JSON facts given.
Return ONLY valid JSON (no markdown fences) with exactly these keys:
- "coverage_confidence": one of "high", "medium", "low", or null if you cannot judge from the facts
- "null_field_notes": object whose keys are subset of:
  is_in_network, is_covered, procedure_covered, deductible_remaining, deductible_total,
  max_remaining, max_total, copay, coinsurance, patient_responsibility
  Values are short plain-English strings explaining why that field is null or uncertain, based ONLY on the snapshot.
  Omit keys you have nothing useful to say about.
- "summary": one sentence summarizing data quality / gaps for staff (no PHI beyond what is in the input)

If the snapshot already has boolean or numeric fields filled, do not contradict them."""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _compact_snapshot(
    canonical: dict[str, Any], payer_id: str, cdt_codes: list[str]
) -> dict[str, Any]:
    """Minimal facts for the model; omit raw_response to reduce PHI payload size."""
    proc = canonical.get("procedure_details") or []
    proc_brief = [
        {
            "cdt_code": p.get("cdt_code"),
            "procedure_covered": p.get("procedure_covered"),
            "non_covered_reason": p.get("non_covered_reason"),
        }
        for p in proc
        if isinstance(p, dict)
    ]
    return {
        "payer_id": payer_id or None,
        "requested_cdt_codes": list(cdt_codes),
        "is_active": canonical.get("is_active"),
        "is_covered": canonical.get("is_covered"),
        "in_network": canonical.get("in_network"),
        "coverage_percent": canonical.get("coverage_percent"),
        "copay": canonical.get("copay"),
        "coinsurance": canonical.get("coinsurance"),
        "deductible_total": canonical.get("deductible_total"),
        "deductible_met": canonical.get("deductible_met"),
        "deductible_remaining": canonical.get("deductible_remaining"),
        "annual_max_total": canonical.get("annual_max_total"),
        "annual_max_used": canonical.get("annual_max_used"),
        "annual_max_remaining": canonical.get("annual_max_remaining"),
        "procedure_details": proc_brief,
        "normalization_warnings": list(canonical.get("normalization_warnings") or []),
        "payer_aaa_errors": canonical.get("payer_aaa_errors"),
    }


def apply_layer3_numeric_consistency(canonical: dict[str, Any]) -> None:
    """
    Clamp impossible remainder-vs-total pairs; append normalization_warnings entries.
    Deterministic; does not call an LLM.
    """
    warnings: list[str] = list(canonical.get("normalization_warnings") or [])

    dt = canonical.get("deductible_total")
    dr = canonical.get("deductible_remaining")
    if dt is not None and dr is not None:
        try:
            f_dt, f_dr = float(dt), float(dr)
            if f_dr > f_dt:
                canonical["deductible_remaining"] = f_dt
                warnings.append("layer3_clamp:deductible_remaining_capped_to_deductible_total")
        except (TypeError, ValueError):
            pass

    mt = canonical.get("annual_max_total")
    mr = canonical.get("annual_max_remaining")
    if mt is not None and mr is not None:
        try:
            f_mt, f_mr = float(mt), float(mr)
            if f_mr > f_mt:
                canonical["annual_max_remaining"] = f_mt
                warnings.append("layer3_clamp:annual_max_remaining_capped_to_annual_max_total")
        except (TypeError, ValueError):
            pass

    oop_t = canonical.get("out_of_pocket_max_total")
    oop_r = canonical.get("out_of_pocket_max_remaining")
    if oop_t is not None and oop_r is not None:
        try:
            f_ot, f_or = float(oop_t), float(oop_r)
            if f_or > f_ot:
                canonical["out_of_pocket_max_remaining"] = f_ot
                warnings.append(
                    "layer3_clamp:out_of_pocket_max_remaining_capped_to_out_of_pocket_max_total"
                )
        except (TypeError, ValueError):
            pass

    canonical["normalization_warnings"] = warnings


def enrich_with_llm(
    canonical: dict[str, Any],
    payer_id: str,
    cdt_codes: list[str],
    *,
    settings: Any | None = None,
) -> None:
    """
    Optionally call an LLM to add ``coverage_confidence`` and human-readable null-field notes.

    Mutates ``canonical`` in place. No-op when disabled or on any failure.
    Never overwrites ``is_covered``, ``procedure_covered``, ``in_network``, or numeric benefits.
    """
    s = settings or get_settings()
    if not bool(getattr(s, "eligibility_layer3_llm_enrich_enabled", False)):
        return
    key = (getattr(s, "eligibility_layer3_llm_openrouter_api_key", "") or "").strip()
    if not key:
        logger.debug("Layer 3 LLM enrich skipped: no API key")
        return

    snapshot = _compact_snapshot(canonical, payer_id, cdt_codes)
    user_content = "Eligibility snapshot (JSON). Annotate per instructions.\n\n" + json.dumps(
        snapshot, indent=2, default=str
    )

    payload = {
        "model": getattr(s, "eligibility_layer3_llm_model", "openai/gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost",
        "X-Title": "eligibility-layer3-enrich",
    }

    try:
        with httpx.Client(
            timeout=float(getattr(s, "eligibility_layer3_llm_timeout_seconds", 45.0))
        ) as client:
            response = client.post(OPENROUTER_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        raw = _strip_json_fence(content)
        parsed = json.loads(raw)
    except Exception as ex:
        logger.warning("Layer 3 LLM enrich failed: %s", ex)
        return

    cc = parsed.get("coverage_confidence")
    if cc in ("high", "medium", "low"):
        canonical["coverage_confidence"] = cc

    notes = parsed.get("null_field_notes")
    if isinstance(notes, dict):
        cleaned: dict[str, str] = {}
        for k, v in notes.items():
            ks = str(k).strip()
            if ks not in _ALLOWED_NULL_NOTE_KEYS:
                continue
            if v is None:
                continue
            vs = str(v).strip()
            if vs:
                cleaned[ks] = vs[:2000]
        if cleaned:
            canonical["layer3_llm_null_field_notes"] = cleaned

    summary = parsed.get("summary")
    if isinstance(summary, str) and summary.strip():
        canonical["layer3_llm_summary"] = summary.strip()[:2000]
