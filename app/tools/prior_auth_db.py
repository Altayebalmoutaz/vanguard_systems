"""
Supabase-backed deterministic prior authorization rules.

- public.cdt_payer_rules_structured (Medicaid-style booleans + age)
- public.v_rules_for_preauth_agent (prior_auth + documentation_required)
"""

from __future__ import annotations

import logging
from typing import Any

from app.integrations.db_tables import CDT_PAYER_RULES_STRUCTURED, RULES_FOR_PREAUTH_AGENT_VIEW
from app.integrations.payer_identity import get_payer_directory_row, resolve_canonical_payer_id
from app.tools.coding_tools import (
    _as_row_list,
    _fetch_payer_rules_rows,
    _insurance_matches_payer,
    _row_applies_to_codes,
    _row_passes_age,
)
from supabase import Client

logger = logging.getLogger(__name__)


def _payer_context_matches(
    insurance: str,
    payer_name: str,
    *,
    resolved_display_name: str | None = None,
) -> bool:
    """Rule row applies if fuzzy insurance matches rule payer_name or resolved directory display_name."""
    if _insurance_matches_payer(insurance, payer_name):
        return True
    return bool(
        resolved_display_name and _insurance_matches_payer(insurance, str(resolved_display_name))
    )


_PREAUTH_VIEW_SELECT = (
    "payer_name,rule_type,code,rule_text,related_codes,conditions,transforms_to_code"
)


def _structured_row_passes_table_age(row: dict[str, Any], patient_age: int | None) -> bool:
    if patient_age is None:
        return True
    amin, amax = row.get("age_min"), row.get("age_max")
    try:
        if amin is not None and patient_age < int(amin):
            return False
        if amax is not None and patient_age > int(amax):
            return False
    except (TypeError, ValueError):
        return True
    return True


def _fetch_structured_prior_auth(
    supabase: Client,
    codes_upper: list[str],
    insurance: str,
    patient_age: int | None,
    *,
    resolved_display_name: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"requires_auth": False, "required_documents": [], "payer_rules": []}
    if not codes_upper:
        return out
    label = CDT_PAYER_RULES_STRUCTURED
    try:
        res = (
            supabase.table(CDT_PAYER_RULES_STRUCTURED)
            .select(
                "code,payer_name,rule_type,rule_text,requires_prior_auth,requires_report,"
                "age_min,age_max,conditions"
            )
            .in_("code", codes_upper)
            .execute()
        )
        rows = _as_row_list(getattr(res, "data", None))
    except Exception as e:
        logger.warning("cdt_payer_rules_structured fetch failed: %s", e)
        out["payer_rules"].append(f"Prior auth DB ({label}): {e}")
        return out

    for row in rows:
        payer_name = str(row.get("payer_name") or "")
        if not _payer_context_matches(
            insurance, payer_name, resolved_display_name=resolved_display_name
        ):
            continue
        if not _structured_row_passes_table_age(row, patient_age):
            continue
        cond = row.get("conditions") or {}
        if not _row_passes_age(cond, patient_age):
            continue

        if row.get("requires_prior_auth"):
            out["requires_auth"] = True
        if row.get("requires_report"):
            code = row.get("code") or "?"
            snippet = str(row.get("rule_text") or "").strip()
            doc = f"Payer report required ({code})"
            if snippet:
                doc = f"{doc}: {snippet[:240]}{'…' if len(snippet) > 240 else ''}"
            out["required_documents"].append(doc)

        text = str(row.get("rule_text") or "").strip()
        if text:
            rt = row.get("rule_type") or "rule"
            out["payer_rules"].append(
                f"[{label}][{payer_name.strip()}] {row.get('code')} ({rt}): {text}"
            )

    return out


def _fetch_preauth_view_rules(
    supabase: Client,
    codes_upper: list[str],
    insurance: str,
    patient_age: int | None,
    *,
    resolved_display_name: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"requires_auth": False, "required_documents": [], "payer_rules": []}
    if not codes_upper:
        return out
    codes_csv = ",".join(codes_upper)
    label = RULES_FOR_PREAUTH_AGENT_VIEW

    rows, fetch_err, _data_list = _fetch_payer_rules_rows(
        supabase,
        codes_csv=codes_csv,
        select_columns=_PREAUTH_VIEW_SELECT,
        table_name=RULES_FOR_PREAUTH_AGENT_VIEW,
    )
    if fetch_err:
        out["payer_rules"].append(f"Prior auth DB ({label}): {fetch_err}")
        return out

    for row in rows:
        payer_name = str(row.get("payer_name") or "")
        if not _payer_context_matches(
            insurance, payer_name, resolved_display_name=resolved_display_name
        ):
            continue
        cond = row.get("conditions") or {}
        if not _row_passes_age(cond, patient_age):
            continue
        if not _row_applies_to_codes(row.get("code"), row.get("related_codes"), codes_upper, cond):
            continue

        rt = str(row.get("rule_type") or "").strip().lower()
        text = str(row.get("rule_text") or "").strip()

        if rt == "prior_auth":
            out["requires_auth"] = True
        if rt == "documentation_required" and text:
            out["required_documents"].append(
                f"Documentation ({row.get('code') or 'any'}): {text[:300]}{'…' if len(text) > 300 else ''}"
            )

        if text:
            cpart = row.get("code") or "any"
            tt = row.get("transforms_to_code")
            extra = f" → {tt}" if tt else ""
            out["payer_rules"].append(
                f"[{label}][{payer_name.strip()}] {cpart}{extra} ({rt or 'rule'}): {text}"
            )

    return out


def _merge_pa_parts(*parts: dict[str, Any]) -> dict[str, Any]:
    requires_auth = False
    docs: list[str] = []
    rules: list[str] = []
    for p in parts:
        requires_auth = requires_auth or bool(p.get("requires_auth"))
        docs.extend(p.get("required_documents") or [])
        rules.extend(p.get("payer_rules") or [])
    return {
        "requires_auth": requires_auth,
        "required_documents": list(dict.fromkeys(docs)),
        "payer_rules": list(dict.fromkeys(rules)),
    }


def fetch_deterministic_prior_auth_from_supabase(
    supabase: Client,
    cdt_codes: list[str],
    insurance: str,
    patient_age: int | None,
) -> dict[str, Any]:
    """
    Merge structured Medicaid rules + preauth-scoped payer_rules view rows.

    Resolves free-text insurance to `payer_network` (including `aliases` on the directory row) when possible,
    so rule rows match on directory display_name as well as legacy payer_name strings.
    """
    codes_upper = [str(c).upper().strip() for c in cdt_codes if str(c).strip()]
    resolved_display: str | None = None
    rid = resolve_canonical_payer_id(supabase, insurance)
    if rid:
        prow = get_payer_directory_row(supabase, rid)
        if prow and prow.get("display_name"):
            resolved_display = str(prow["display_name"]).strip()
        note = f"[identity] Resolved insurance string to payer_id={rid}"
        if resolved_display:
            note += f" ({resolved_display})"
    else:
        note = ""

    a = _fetch_structured_prior_auth(
        supabase, codes_upper, insurance, patient_age, resolved_display_name=resolved_display
    )
    b = _fetch_preauth_view_rules(
        supabase, codes_upper, insurance, patient_age, resolved_display_name=resolved_display
    )
    merged = _merge_pa_parts(a, b)
    if note:
        rules = list(merged.get("payer_rules") or [])
        rules.insert(0, note)
        merged["payer_rules"] = list(dict.fromkeys(rules))
    return merged
