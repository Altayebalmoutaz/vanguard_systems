"""Normalize a single Stedi-shaped EB / benefitsInformation row."""

from __future__ import annotations

from typing import Any


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _additional_blob(benefit: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("additionalInformation", "benefitsAdditionalInformation"):
        block = benefit.get(key)
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    d = item.get("description") or item.get("planNetworkDescription")
                    if d:
                        parts.append(str(d).lower())
    return " ".join(parts)


def _infer_in_network(benefit: dict[str, Any]) -> tuple[bool | None, str]:
    """
    Per-block network hint (do not merge with other EB rows — variant 5).
    Returns (is_in_network, reason_when_none).
    """
    texts: list[str] = [
        str(benefit.get("planNetworkDescription") or "").lower(),
        str(benefit.get("name") or "").lower(),
        _additional_blob(benefit),
    ]
    blob = " ".join(texts)
    inn = "in network" in blob or " in-network" in blob or "in-network" in blob
    oon = "out of network" in blob or " out-of-network" in blob or "out-of-network" in blob or " oon " in f" {blob} "
    if inn and not oon:
        return True, ""
    if oon and not inn:
        return False, ""
    if inn and oon:
        return None, "conflicting_in_network_and_out_of_network_hints_in_same_block"
    return None, "no_in_network_or_out_of_network_hints_in_benefit_block"


def _procedure_from_top_level(benefit: dict[str, Any]) -> str | None:
    pc = benefit.get("procedureCode")
    if isinstance(pc, str) and pc.strip():
        return pc.strip().upper()
    return None


def _procedure_from_composite(benefit: dict[str, Any]) -> str | None:
    comp = benefit.get("compositeMedicalProcedureIdentifier")
    if not isinstance(comp, dict):
        return None
    for key in ("procedureCode", "productOrServiceID", "procedureCodeOrProcedureType"):
        v = comp.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return None


def _classify_benefit_type(code: str, name: str, has_amount: bool, has_pct: bool) -> str:
    n = name.lower()
    if has_pct or code == "A":
        return "coinsurance"
    if code == "B":
        return "copay"
    if code in ("C",):
        if "remaining" in n:
            return "deductible_remaining"
        return "deductible"
    if code in ("F",):
        if "remaining" in n:
            return "annual_max_remaining"
        return "annual_maximum"
    if code == "1":
        return "active_coverage"
    if code in ("N", "I"):
        return "not_covered"
    return "other"


def _with_network_benefit_type(base: str, inn: bool | None, inn_reason: str) -> str:
    if inn is None and inn_reason:
        return f"NETWORK_UNKNOWN:{inn_reason}|{base}"
    return base


def procedure_identifier_from_benefit(raw_benefit: dict[str, Any]) -> str | None:
    """
    Return CDT / procedure id from top-level ``procedureCode`` or
    ``compositeMedicalProcedureIdentifier`` (same resolution order as ``normalize_benefit_block``).
    """
    p = _procedure_from_top_level(raw_benefit)
    if p:
        return p
    return _procedure_from_composite(raw_benefit)


def normalize_benefit_block(raw_benefit: dict[str, Any], payer_id: str) -> dict[str, Any]:
    """
    Map one raw ``benefitsInformation`` element to a flat dict.

    Explicit branches (evaluation order):
    4) Benefit ``code`` **N** or **I** (not covered)
    1) Top-level ``procedureCode``
    2) ``compositeMedicalProcedureIdentifier`` (no top-level procedure code)
    3) ``timeQualifier`` / ``timePeriodQualifier`` is **Remaining**, or C/F **Remaining** row
       without a total-only name
    5) In-network vs out-of-network — inferred per block only (never merge rows)
    """
    _ = payer_id

    name = str(raw_benefit.get("name") or "")
    name_l = name.lower()
    code = str(raw_benefit.get("code") or "").strip().upper()

    bdi = raw_benefit.get("benefitsDateInformation")
    if not isinstance(bdi, dict):
        bdi = {}

    tq_raw = str(bdi.get("timePeriodQualifier") or bdi.get("timeQualifier") or "").strip()
    time_qualifier = tq_raw

    def six(
        is_covered: bool,
        inn: bool | None,
        inn_reason: str,
        benefit_amount: float | None,
        benefit_type: str,
        qualifier: str,
        tq: str,
    ) -> dict[str, Any]:
        return {
            "is_covered": is_covered,
            "is_in_network": inn,
            "benefit_amount": benefit_amount,
            "benefit_type": _with_network_benefit_type(benefit_type, inn, inn_reason),
            "qualifier": qualifier,
            "time_qualifier": tq,
        }

    # --- Branch 4: not covered (N / I) -----------------------------------------
    if code in ("N", "I") or ("not covered" in name_l and code not in ("1",)):
        proc = _procedure_from_top_level(raw_benefit) or _procedure_from_composite(raw_benefit) or ""
        inn, inn_reason = _infer_in_network(raw_benefit)
        return six(
            False,
            inn,
            inn_reason,
            _to_float(raw_benefit.get("benefitAmount")),
            "not_covered",
            proc or code,
            time_qualifier or "",
        )

    # --- Branch 1: top-level procedureCode -------------------------------------
    if _procedure_from_top_level(raw_benefit) is not None:
        proc = _procedure_from_top_level(raw_benefit) or ""
        inn, inn_reason = _infer_in_network(raw_benefit)
        is_cov = "inactive" not in name_l
        amt = _to_float(raw_benefit.get("benefitAmount"))
        pct = _to_float(raw_benefit.get("benefitPercent"))
        bt = _classify_benefit_type(code, name, amt is not None, pct is not None)
        return six(
            is_cov,
            inn,
            inn_reason,
            amt if amt is not None else pct,
            bt,
            proc,
            time_qualifier or "",
        )

    # --- Branch 2: compositeMedicalProcedureIdentifier -------------------------
    if raw_benefit.get("compositeMedicalProcedureIdentifier") is not None:
        proc = _procedure_from_composite(raw_benefit) or ""
        inn, inn_reason = _infer_in_network(raw_benefit)
        is_cov = "inactive" not in name_l
        amt = _to_float(raw_benefit.get("benefitAmount"))
        pct = _to_float(raw_benefit.get("benefitPercent"))
        bt = _classify_benefit_type(code, name, amt is not None, pct is not None)
        comp = raw_benefit.get("compositeMedicalProcedureIdentifier")
        pq = ""
        if isinstance(comp, dict):
            pq = str(comp.get("productOrServiceIDQualifier") or "")
        qual = f"{proc}|{pq}" if pq else proc
        return six(
            is_cov,
            inn,
            inn_reason,
            amt if amt is not None else pct,
            bt,
            qual,
            time_qualifier or "",
        )

    # --- Branch 3: Remaining-only (no total on this row) -----------------------
    remaining_time = tq_raw == "Remaining" or tq_raw.upper() == "REMAINING"
    remaining_by_name = (
        code in ("C", "F")
        and "remaining" in name_l
        and "deductible met" not in name_l
        and "used" not in name_l
        and name_l.strip() not in ("annual maximum", "deductible")
    )
    if remaining_time or remaining_by_name:
        if remaining_time or (remaining_by_name and not time_qualifier):
            time_qualifier = "Remaining"
        proc = _procedure_from_top_level(raw_benefit) or _procedure_from_composite(raw_benefit)
        amt = _to_float(raw_benefit.get("benefitAmount"))
        pct = _to_float(raw_benefit.get("benefitPercent"))
        inn, inn_reason = _infer_in_network(raw_benefit)
        is_cov = "inactive" not in name_l
        bt = _classify_benefit_type(code, name, amt is not None, pct is not None)
        if remaining_time or remaining_by_name:
            bt = f"{bt}|remaining_only_no_total_row"
        qual = proc or code
        return six(
            is_cov,
            inn,
            inn_reason,
            amt if amt is not None else pct,
            bt,
            str(qual),
            time_qualifier,
        )

    # --- Default: single-block network inference (variant 5 compatible) -------
    proc = _procedure_from_top_level(raw_benefit) or _procedure_from_composite(raw_benefit)
    inn, inn_reason = _infer_in_network(raw_benefit)
    is_cov = "inactive" not in name_l
    amt = _to_float(raw_benefit.get("benefitAmount"))
    pct = _to_float(raw_benefit.get("benefitPercent"))
    bt = _classify_benefit_type(code, name, amt is not None, pct is not None)
    qual = proc or code
    return six(
        is_cov,
        inn,
        inn_reason,
        amt if amt is not None else pct,
        bt,
        str(qual),
        time_qualifier or "",
    )

