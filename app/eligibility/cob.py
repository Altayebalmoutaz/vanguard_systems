"""Layer 7 — Coordination of benefits (primary + secondary)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

COB_POLICY_VERSION = "2.0"


def _to_decimal(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _aggregate_amounts(rows: list[dict[str, Any]], amount_key: str) -> dict[str, Decimal]:
    by_code: dict[str, Decimal] = {}
    for row in rows:
        code = str(row.get("cdt_code") or "").strip().upper()
        if not code:
            continue
        by_code[code] = by_code.get(code, Decimal("0")) + _to_decimal(row.get(amount_key))
    return by_code


def calculate_cob(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    """
    Standard dental COB when both plans are complete.

    secondary_pays = min(patient_responsibility_after_primary, secondary_coverage_amount)
    final_patient_responsibility = patient_responsibility_after_primary - secondary_pays
    """
    if not primary.get("response_complete"):
        raise ValueError("primary eligibility must be response_complete for COB")
    if not secondary.get("response_complete"):
        raise ValueError(
            "secondary eligibility must be response_complete for COB — route to human review"
        )

    primary_rows = list(primary.get("procedure_estimates") or [])
    secondary_rows = list(secondary.get("procedure_estimates") or [])
    prim_by_code = _aggregate_amounts(primary_rows, "patient_responsibility")
    sec_by_code = _aggregate_amounts(secondary_rows, "insurance_pays")

    merged: list[dict[str, Any]] = []
    for cdt in sorted(prim_by_code.keys()):
        patient_after_primary_raw = prim_by_code.get(cdt, Decimal("0"))
        secondary_coverage_raw = sec_by_code.get(cdt, Decimal("0"))

        flags: list[str] = []
        if cdt not in sec_by_code:
            flags.append("missing_secondary_line")
        if patient_after_primary_raw < 0:
            patient_after_primary_raw = Decimal("0")
            flags.append("negative_primary_responsibility_clamped")
        if secondary_coverage_raw < 0:
            secondary_coverage_raw = Decimal("0")
            flags.append("negative_secondary_coverage_clamped")

        secondary_pays_raw = min(patient_after_primary_raw, secondary_coverage_raw)
        if secondary_coverage_raw > patient_after_primary_raw:
            flags.append("secondary_cap_applied")

        patient_after_primary = _money(patient_after_primary_raw)
        secondary_coverage_amount = _money(secondary_coverage_raw)
        secondary_pays = _money(secondary_pays_raw)
        final_patient = _money(patient_after_primary_raw - secondary_pays_raw)

        merged.append(
            {
                "cdt_code": cdt,
                "patient_responsibility_after_primary": float(patient_after_primary),
                "secondary_coverage_amount": float(secondary_coverage_amount),
                "secondary_pays": float(secondary_pays),
                "final_patient_responsibility": float(final_patient),
                "flags": flags,
            }
        )

    return {
        "cob_policy_version": COB_POLICY_VERSION,
        "cob_lines": merged,
        "primary_check_id": primary.get("check_id"),
        "secondary_check_id": secondary.get("check_id"),
    }
