"""
Tools layer: small, testable units the agent calls in order.

Each function does one job. The LLM is invoked only from generate_codes_tool (which
delegates to app.llm.coding_llm). When `JINA_API_KEY` is set and Supabase is available,
pgvector retrieval is injected into the LLM prompt as optional CDT memory.

Supabase alignment:
- ICD validation uses `icd10_dental_gem_axis` (columns icd10_code, icd10_code_compact).
- CDT validation uses `cdt_codes` (your project currently has a 52-code reference set).
- Payer rules: `public.payer_rules` (default PostgREST schema).
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.integrations.db_tables import CDT_CODES, ICD10_DENTAL_GEM_AXIS, PAYER_RULES
from app.llm.coding_llm import llm_generate_codes
from app.services.cdt_vector_memory import fetch_cdt_vector_memory
from supabase import Client


def generate_codes_tool(
    settings: Settings,
    supabase: Client | None,
    clinical_note: str,
    patient_age: int,
    insurance: str,
) -> dict[str, Any]:
    """
    Tool: produce structured code suggestions using the LLM module.
    Optionally enriches the prompt with pgvector CDT retrieval (Jina + match_cdt_codes).
    """
    memory = ""
    if supabase is not None and settings.jina_api_key:
        memory = fetch_cdt_vector_memory(
            supabase,
            clinical_note,
            insurance,
            jina_api_key=settings.jina_api_key,
            match_count=settings.cdt_vector_match_count,
            match_threshold=settings.cdt_vector_match_threshold,
        )
    return llm_generate_codes(
        settings,
        clinical_note,
        patient_age,
        insurance,
        retrieval_context=memory or None,
    )


def _icd_variants(code: str) -> set[str]:
    u = code.upper().replace(" ", "").strip()
    return {u, u.replace(".", "")}


def _icd_row_sets(rows: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    display: set[str] = set()
    compact: set[str] = set()
    for row in rows:
        if row.get("icd10_code"):
            display.add(str(row["icd10_code"]).upper().replace(" ", ""))
        if row.get("icd10_code_compact"):
            compact.add(str(row["icd10_code_compact"]).upper().replace(" ", ""))
    return display, compact


def _icd_matches_input(
    original: str,
    display_set: set[str],
    compact_set: set[str],
) -> bool:
    u = original.upper().replace(" ", "").strip()
    if u in display_set:
        return True
    nodot = u.replace(".", "")
    return nodot in compact_set


def validate_icd_tool(supabase: Client | None, icd10_codes: list[str]) -> dict[str, Any]:
    """
    Tool: verify ICD-10 codes against `icd10_dental_gem_axis`.

    Matches either dotted form (e.g. K02.9) or compact form (e.g. K029) from the table.
    """
    icd_flags: list[str] = []
    if not icd10_codes:
        return {"invalid": [], "verified": [], "icd_flags": icd_flags}

    if supabase is None:
        icd_flags.append(
            "ICD-10 codes not verified: Supabase not configured (set SUPABASE_URL and key)."
        )
        return {"invalid": [], "verified": list(icd10_codes), "icd_flags": icd_flags}

    variants: set[str] = set()
    for c in icd10_codes:
        variants.update(_icd_variants(c))
    vlist = list(variants)

    r1 = (
        supabase.table(ICD10_DENTAL_GEM_AXIS)
        .select("icd10_code,icd10_code_compact")
        .in_("icd10_code", vlist)
        .execute()
    )
    r2 = (
        supabase.table(ICD10_DENTAL_GEM_AXIS)
        .select("icd10_code,icd10_code_compact")
        .in_("icd10_code_compact", vlist)
        .execute()
    )
    rows = (getattr(r1, "data", None) or []) + (getattr(r2, "data", None) or [])
    display_set, compact_set = _icd_row_sets(rows)

    verified: list[str] = []
    invalid: list[str] = []
    for original in icd10_codes:
        if _icd_matches_input(original, display_set, compact_set):
            verified.append(original)
        else:
            invalid.append(original)
            icd_flags.append(
                f"ICD-10 {original} not found in {ICD10_DENTAL_GEM_AXIS} (dental reference subset)"
            )

    return {"invalid": invalid, "verified": verified, "icd_flags": icd_flags}


def validate_cdt_tool(supabase: Client | None, cdt_codes: list[str]) -> dict[str, Any]:
    """
    Tool: verify CDT codes exist in `cdt_codes`.

    Your hosted table is a small reference set (~52 codes) — good for dev/testing, not full ADA CDT.
    """
    cdt_flags: list[str] = []
    if not cdt_codes:
        return {"invalid": [], "verified": [], "cdt_flags": cdt_flags, "reference_size": 0}

    if supabase is None:
        cdt_flags.append("CDT codes not verified: Supabase not configured.")
        return {"invalid": [], "verified": list(cdt_codes), "cdt_flags": cdt_flags, "reference_size": 0}

    normalized = [c.upper().strip() for c in cdt_codes]
    count_res = (
        supabase.table(CDT_CODES).select("code", count="exact").limit(0).execute()
    )
    reference_size = int(getattr(count_res, "count", None) or 0)

    result = (
        supabase.table(CDT_CODES).select("code").in_("code", normalized).execute()
    )
    rows = getattr(result, "data", None) or []
    found = {str(r["code"]).upper().strip() for r in rows}

    verified: list[str] = []
    invalid: list[str] = []
    for original, norm in zip(cdt_codes, normalized, strict=False):
        if norm in found:
            verified.append(original)
        else:
            invalid.append(original)
            cdt_flags.append(
                f"CDT {original} not in {CDT_CODES} reference ({reference_size} procedures loaded)"
            )

    if reference_size and reference_size < 200:
        cdt_flags.append(
            f"Note: CDT reference is a limited test set ({reference_size} codes), not full ADA CDT."
        )

    return {
        "invalid": invalid,
        "verified": verified,
        "cdt_flags": cdt_flags,
        "reference_size": reference_size,
    }


def _insurance_matches_payer(insurance: str, payer_name: str) -> bool:
    raw = (payer_name or "").strip()
    if not raw:
        return False
    u = raw.upper()
    if u in ("*", "ANY", "ALL"):
        return True
    ins_l = insurance.strip().lower()
    pn_l = raw.lower()
    if pn_l in ins_l or ins_l in pn_l:
        return True
    return any(len(w) >= 3 and w in ins_l for w in pn_l.split())


def _related_overlaps_claim(related_codes: Any, codes_upper: list[str]) -> bool:
    if not related_codes or not codes_upper:
        return False
    if not isinstance(related_codes, list):
        return False
    relu = {str(x).upper().strip() for x in related_codes if x is not None and str(x).strip()}
    return bool(relu & set(codes_upper))


def _row_applies_to_codes(
    code_cell: Any,
    related_codes: Any,
    codes_upper: list[str],
    conditions: Any,
) -> bool:
    """
    Row applies if primary code is blank or on the claim, or related_codes overlaps the claim.
    """
    on_claim = False
    if code_cell is None or not str(code_cell).strip() or str(code_cell).upper().strip() in codes_upper or _related_overlaps_claim(related_codes, codes_upper):
        on_claim = True
    if not on_claim:
        return False
    if not isinstance(conditions, dict):
        return True
    req_all = conditions.get("require_all_codes")
    if req_all:
        need = [str(x).upper().strip() for x in req_all if str(x).strip()]
        if need and not all(c in codes_upper for c in need):
            return False
    return True


def _as_row_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _format_payer_flag(row: dict[str, Any], source_label: str) -> str:
    payer_name = str(row.get("payer_name") or "").strip()
    rt = row.get("rule_type") or "rule"
    text = str(row.get("rule_text") or "")
    cpart = row.get("code") or "any"
    tt = row.get("transforms_to_code")
    extra = f" → {tt}" if tt else ""
    return f"[{source_label}][{payer_name}] {cpart}{extra} ({rt}): {text}"


def _payer_rules_or_filter(codes_csv: str) -> str:
    """
    PostgREST OR: billed codes, global rows, or related_codes overlaps claim (text[]).
    """
    # `ov` = array overlap (any billed code appears in related_codes).
    return f"code.is.null,code.in.({codes_csv}),related_codes.ov.{{{codes_csv}}}"


def _fetch_payer_rules_rows(
    supabase: Client,
    *,
    codes_csv: str,
    select_columns: str,
    table_name: str,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """
    Returns (rows, error_message, response_had_list_payload).

    `response_had_list_payload` is True only when PostgREST returned a JSON array for `data`,
    so we can show “empty table” hints without firing on mocks or malformed responses.
    """
    try:
        res = (
            supabase.table(table_name)
            .select(select_columns)
            .or_(_payer_rules_or_filter(codes_csv))
            .execute()
        )
        raw = getattr(res, "data", None)
        saw_list = isinstance(raw, list)
        return _as_row_list(raw), None, saw_list
    except Exception as e:
        return [], str(e), False


def _row_passes_age(conditions: Any, patient_age: int | None) -> bool:
    if patient_age is None or not isinstance(conditions, dict):
        return True
    amin = conditions.get("age_min")
    amax = conditions.get("age_max")
    try:
        if amin is not None and patient_age < int(amin):
            return False
        if amax is not None and patient_age > int(amax):
            return False
    except (TypeError, ValueError):
        return True
    return True


def apply_payer_rules_tool(
    supabase: Client | None,
    cdt_codes: list[str],
    insurance: str,
    patient_age: int | None = None,
) -> dict[str, Any]:
    """
    Tool: load payer rules from `payer_rules`.

    Match insurance ↔ payer_name, optional conditions (age_min/max, require_all_codes);
    primary code NULL / on claim, or related_codes overlaps billed CDT.
    """
    payer_flags: list[str] = []
    matched: list[dict[str, Any]] = []

    codes_upper = [str(c).upper().strip() for c in cdt_codes if str(c).strip()]
    if not codes_upper:
        return {"payer_flags": payer_flags, "payer_rules_matched": matched}

    if supabase is None:
        payer_flags.append(
            "Payer rules not applied: Supabase not configured (set SUPABASE_URL and key)."
        )
        return {"payer_flags": payer_flags, "payer_rules_matched": matched}

    codes_csv = ",".join(codes_upper)
    select_columns = (
        "id,payer_name,rule_type,code,rule_text,related_codes,conditions,transforms_to_code"
    )
    rel_label = PAYER_RULES

    rows, fetch_err, data_was_list = _fetch_payer_rules_rows(
        supabase,
        codes_csv=codes_csv,
        select_columns=select_columns,
        table_name=PAYER_RULES,
    )
    if fetch_err:
        payer_flags.append(f"Payer rules ({rel_label}): {fetch_err}")

    for row in rows:
        payer_name = str(row.get("payer_name") or "")
        if not _insurance_matches_payer(insurance, payer_name):
            continue
        cond = row.get("conditions") or {}
        if not _row_passes_age(cond, patient_age):
            continue
        if not _row_applies_to_codes(
            row.get("code"), row.get("related_codes"), codes_upper, cond
        ):
            continue

        enriched = dict(row)
        enriched["_rule_source"] = rel_label
        matched.append(enriched)
        payer_flags.append(_format_payer_flag(row, rel_label))

    if not fetch_err and not matched and data_was_list:
        if not rows:
            payer_flags.append(
                "Payer rules: no rows in payer_rules for this query "
                "(code on claim, code null, or related_codes overlap). Add or adjust rules for "
                f"these codes: {', '.join(codes_upper)}."
            )
        else:
            payer_flags.append(
                "Payer rules: rules were returned but none matched encounter insurance "
                f"({insurance!r}). Align encounter.insurance with payer_name, or use payer_name "
                "'*' / 'any' for payer-wide notices."
            )

    return {"payer_flags": payer_flags, "payer_rules_matched": matched}
