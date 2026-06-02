"""Layer 4 — Completeness and integrity validation after normalization."""

from __future__ import annotations

from typing import Any

from app.eligibility.canonical_record import attach_eligibility_canonical_record

INTEGRITY_POLICY_VERSION = "2.6"
BASE_CRITICAL_FIELDS = ("is_active",)
IMPORTANT_FIELDS = ("coverage_percent", "copay", "coinsurance")
# Kept for callers/tests: union of historical Layer-4 requirements. Only ``is_active``
# is enforced via ``missing_fields``; remainder fields are warned, not blocking.
CRITICAL_FIELDS = ("is_active", "deductible_remaining", "annual_max_remaining")


def _issue(
    *,
    code: str,
    severity: str,
    field: str | None = None,
    message: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if field is not None:
        payload["field"] = field
    if detail:
        payload["detail"] = detail
    return payload


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_completeness(canonical: dict[str, Any]) -> dict[str, Any]:
    """
    Set response_complete and missing_fields on canonical.

    CRITICAL null → response_complete False (block routing).
    IMPORTANT null → integrity_warnings (do not block on these alone).
    """
    missing: list[str] = []
    issues: list[dict[str, Any]] = []

    for field in BASE_CRITICAL_FIELDS:
        if canonical.get(field) is None:
            missing.append(field)
            issues.append(
                _issue(
                    code="L4_MISSING_CRITICAL_FIELD",
                    severity="error",
                    field=field,
                    message=f"Critical field is null: {field}",
                )
            )

    # Layer 3 classifies raw ``x12`` when present; 999 is an implementation ack, not a 271 benefit reply.
    _STEDI_999_GATE = "stedi_implementation_ack_999"
    if canonical.get("stedi_x12_transaction_kind") == "999":
        if _STEDI_999_GATE not in missing:
            missing.append(_STEDI_999_GATE)
        issues.append(
            _issue(
                code="L4_STEDI_X12_999_PAYLOAD",
                severity="error",
                field="raw_response.x12",
                message=(
                    "Stedi raw X12 is a 999 implementation acknowledgment, not a 271 eligibility "
                    "response — eligibility JSON may be empty or unreliable per Stedi docs."
                ),
                detail={"stedi_x12_transaction_kind": "999"},
            )
        )

    deductible_total = _float_or_none(canonical.get("deductible_total"))
    deductible_met = _float_or_none(canonical.get("deductible_met"))
    deductible_remaining = _float_or_none(canonical.get("deductible_remaining"))
    annual_max_total = _float_or_none(canonical.get("annual_max_total"))
    annual_max_used = _float_or_none(canonical.get("annual_max_used"))
    annual_max_remaining = _float_or_none(canonical.get("annual_max_remaining"))

    warnings: list[str] = list(canonical.get("normalization_warnings") or [])

    # Remaining amounts: deterministic Layer 3 (total − met / used) fills remainders when both
    # components exist. Partial payer context without a remainder row still routes — warn only.
    if (
        (deductible_total is not None or deductible_met is not None)
        and deductible_remaining is None
        and not (deductible_total is not None and deductible_met is not None)
    ):
        wk = "ambiguous_deductible_remaining_null"
        if wk not in warnings:
            warnings.append(wk)
        issues.append(
            _issue(
                code="L4_DEDUCTIBLE_REMAINING_UNKNOWN",
                severity="warning",
                field="deductible_remaining",
                message="deductible_remaining is null with partial deductible_total / deductible_met context",
            )
        )

    if deductible_total is not None and deductible_met is not None and deductible_remaining is None:
        warnings.append("deductible_remaining_null_despite_total_and_met")
        issues.append(
            _issue(
                code="L4_DEDUCTIBLE_REMAINING_MISSING_UNEXPECTED",
                severity="warning",
                field="deductible_remaining",
                message="deductible_total and deductible_met are set but deductible_remaining is null",
            )
        )

    if (
        (annual_max_total is not None or annual_max_used is not None)
        and annual_max_remaining is None
        and not (annual_max_total is not None and annual_max_used is not None)
    ):
        wk = "ambiguous_annual_max_remaining_null"
        if wk not in warnings:
            warnings.append(wk)
        issues.append(
            _issue(
                code="L4_ANNUAL_MAX_REMAINING_UNKNOWN",
                severity="warning",
                field="annual_max_remaining",
                message="annual_max_remaining is null with partial annual_max_total / annual_max_used context",
            )
        )

    if (
        annual_max_total is not None
        and annual_max_used is not None
        and annual_max_remaining is None
    ):
        warnings.append("annual_max_remaining_null_despite_total_and_used")
        issues.append(
            _issue(
                code="L4_ANNUAL_MAX_REMAINING_MISSING_UNEXPECTED",
                severity="warning",
                field="annual_max_remaining",
                message="annual_max_total and annual_max_used are set but annual_max_remaining is null",
            )
        )

    # Payer/Stedi omission of aggregate INN/OON is not an integrity_warning (would force dashboard
    # "Needs Attention" everywhere 271 lacks network indicators).

    for field in IMPORTANT_FIELDS:
        if canonical.get(field) is None:
            w = f"important_field_null:{field}"
            warnings.append(w)
            issues.append(
                _issue(
                    code="L4_IMPORTANT_FIELD_NULL",
                    severity="warning",
                    field=field,
                    message=f"Important field is null: {field}",
                )
            )

    # --- Numeric consistency and range checks ---
    coverage_percent = _float_or_none(canonical.get("coverage_percent"))
    if coverage_percent is not None and (coverage_percent < 0.0 or coverage_percent > 100.0):
        warnings.append("coverage_percent_out_of_range")
        issues.append(
            _issue(
                code="L4_RANGE_VIOLATION",
                severity="warning",
                field="coverage_percent",
                message="coverage_percent should be between 0 and 100",
                detail={"value": coverage_percent, "min": 0.0, "max": 100.0},
            )
        )

    copay = _float_or_none(canonical.get("copay"))
    if copay is not None and copay < 0.0:
        warnings.append("copay_negative")
        issues.append(
            _issue(
                code="L4_RANGE_VIOLATION",
                severity="warning",
                field="copay",
                message="copay should be non-negative",
                detail={"value": copay, "min": 0.0},
            )
        )

    coinsurance = _float_or_none(canonical.get("coinsurance"))
    if coinsurance is not None and (coinsurance < 0.0 or coinsurance > 100.0):
        warnings.append("coinsurance_out_of_range")
        issues.append(
            _issue(
                code="L4_RANGE_VIOLATION",
                severity="warning",
                field="coinsurance",
                message="coinsurance should be between 0 and 100",
                detail={"value": coinsurance, "min": 0.0, "max": 100.0},
            )
        )

    if (
        deductible_total is not None
        and deductible_remaining is not None
        and deductible_remaining > deductible_total
    ):
        warnings.append("deductible_remaining_gt_total")
        issues.append(
            _issue(
                code="L4_CONSISTENCY_VIOLATION",
                severity="warning",
                field="deductible_remaining",
                message="deductible_remaining exceeds deductible_total",
                detail={
                    "deductible_remaining": deductible_remaining,
                    "deductible_total": deductible_total,
                },
            )
        )

    if (
        annual_max_total is not None
        and annual_max_remaining is not None
        and annual_max_remaining > annual_max_total
    ):
        warnings.append("annual_max_remaining_gt_total")
        issues.append(
            _issue(
                code="L4_CONSISTENCY_VIOLATION",
                severity="warning",
                field="annual_max_remaining",
                message="annual_max_remaining exceeds annual_max_total",
                detail={
                    "annual_max_remaining": annual_max_remaining,
                    "annual_max_total": annual_max_total,
                },
            )
        )

    canonical["missing_fields"] = missing
    canonical["integrity_warnings"] = warnings
    canonical["response_complete"] = len(missing) == 0
    canonical["integrity_policy_version"] = INTEGRITY_POLICY_VERSION
    canonical["integrity_issues"] = issues
    attach_eligibility_canonical_record(canonical)
    return canonical
