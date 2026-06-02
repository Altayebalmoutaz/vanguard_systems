"""Attach ``EligibilityCanonicalRecord`` to the flat ``canonical`` dict after Layer 4."""

from __future__ import annotations

from typing import Any

from app.eligibility.canonical_model import EligibilityCanonicalRecord


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_NULL_REASONS: dict[str, str] = {
    "is_in_network": "not_reported_or_ambiguous_in_271_response",
    "is_covered": "aggregate_procedure_coverage_not_determined",
    "procedure_covered": "aggregate_procedure_coverage_not_determined",
    "deductible_remaining": "not_present_in_normalized_271",
    "deductible_total": "not_present_in_normalized_271",
    "max_remaining": "not_present_in_normalized_271",
    "max_total": "not_present_in_normalized_271",
    "copay": "not_present_in_normalized_271",
    "coinsurance": "not_present_in_normalized_271",
    "patient_responsibility": "computed_in_layer_5_fee_schedule_not_run",
}


def attach_eligibility_canonical_record(canonical: dict[str, Any]) -> None:
    """
    Populate ``canonical["eligibility_canonical"]`` with a validated
    :class:`~models.eligibility_canonical.EligibilityCanonicalRecord` dump.

    Skips when ``is_active`` is not a bool (unknown subscriber status).
    """
    ia = canonical.get("is_active")
    if not isinstance(ia, bool):
        canonical["eligibility_canonical"] = None
        return

    inn = canonical.get("in_network")
    is_cov = canonical.get("is_covered")
    if not ia:
        is_cov = False
    proc_cov = is_cov if ia else False

    dr = _float_or_none(canonical.get("deductible_remaining"))
    dt = _float_or_none(canonical.get("deductible_total"))
    mr = _float_or_none(canonical.get("annual_max_remaining"))
    mt = _float_or_none(canonical.get("annual_max_total"))
    cp = _float_or_none(canonical.get("copay"))
    co = _float_or_none(canonical.get("coinsurance"))
    pr = _float_or_none(canonical.get("patient_responsibility"))
    cov_conf = canonical.get("coverage_confidence")
    cov_conf_s: str | None = (
        str(cov_conf).strip() if isinstance(cov_conf, str) and cov_conf.strip() else None
    )

    missing = list(canonical.get("missing_fields") or [])
    response_complete = bool(canonical.get("response_complete"))

    null_reasons: dict[str, str] = {}
    for key, val in (
        ("is_in_network", inn),
        ("is_covered", is_cov),
        ("procedure_covered", proc_cov),
        ("deductible_remaining", dr),
        ("deductible_total", dt),
        ("max_remaining", mr),
        ("max_total", mt),
        ("copay", cp),
        ("coinsurance", co),
        ("patient_responsibility", pr),
    ):
        if val is None:
            null_reasons[key] = _NULL_REASONS[key]

    rec = EligibilityCanonicalRecord(
        is_active=ia,
        is_in_network=inn,
        is_covered=is_cov,
        procedure_covered=proc_cov,
        deductible_remaining=dr,
        deductible_total=dt,
        max_remaining=mr,
        max_total=mt,
        copay=cp,
        coinsurance=co,
        patient_responsibility=pr,
        coverage_confidence=cov_conf_s,
        missing_fields=missing,
        response_complete=response_complete,
        null_reasons=null_reasons,
    )

    canonical["eligibility_canonical"] = rec.model_dump(mode="python")


__all__ = ["attach_eligibility_canonical_record"]
