"""Build UniversalDentalRecord v1 from Layer 3 canonical + stored raw 271 (heuristic)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from app.eligibility.universal_dental.models import (
    AgeLimit,
    BenefitCategory,
    CategoryBenefit,
    ConfidenceLevel,
    DowngradeClause,
    FinancialSummary,
    FrequencyLimitation,
    LastServiceDate,
    MissingToothClause,
    NetworkStatus,
    NormalizationMethod,
    OrthoDetail,
    UniversalDentalRecord,
    WaitingPeriod,
    data_point_bool,
    data_point_float,
    data_point_int,
)

_WARN_DED_CONFLICT = "deductible_remaining conflict"
_WARN_MAX_CONFLICT = "annual_max_remaining conflict"
_WARN_DED_CLAMP = "layer3_clamp:deductible_remaining_capped_to_deductible_total"
_WARN_MAX_CLAMP = "layer3_clamp:annual_max_remaining_capped_to_annual_max_total"


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_STC_TO_CATEGORY: dict[str, BenefitCategory] = {
    "23": BenefitCategory.DIAGNOSTIC,
    "25": BenefitCategory.BASIC,
    "36": BenefitCategory.MAJOR,
    "38": BenefitCategory.ORTHO,
}


def _hash_raw(raw: dict[str, Any]) -> str:
    payload = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _warnings_list(canonical: dict[str, Any]) -> list[str]:
    """Merge Layer 3 normalization warnings with Layer 4 integrity warnings for downstream confidence."""
    out: list[str] = []
    for key in ("normalization_warnings", "integrity_warnings"):
        w = canonical.get(key)
        if isinstance(w, list):
            out.extend(str(x) for x in w if x is not None)
    return out


def _confidence_float(
    value: float | None,
    *,
    conflict_markers: tuple[str, ...],
    warnings: list[str],
) -> ConfidenceLevel:
    if value is None:
        return ConfidenceLevel.UNKNOWN
    blob = " | ".join(warnings)
    if any(m in blob for m in conflict_markers):
        return ConfidenceLevel.INFERRED
    return ConfidenceLevel.EXPLICIT


def _parse_yyyymmdd(s: str | None) -> date | None:
    if not s or not isinstance(s, str) or len(s) < 8:
        return None
    digits = re.sub(r"\D", "", s[:8])
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _parse_plan_dates(raw: dict[str, Any]) -> tuple[date | None, date | None]:
    pdi = raw.get("planDateInformation")
    if not isinstance(pdi, dict):
        return None, None
    plan = pdi.get("plan")
    if isinstance(plan, str) and "-" in plan:
        parts = plan.split("-", 1)
        return _parse_yyyymmdd(parts[0].strip()), _parse_yyyymmdd(parts[1].strip())
    el = pdi.get("eligibility") or pdi.get("planBegin") or pdi.get("planEnd")
    if isinstance(el, str):
        d = _parse_yyyymmdd(el)
        return d, d
    return None, None


def _network_status(canonical: dict[str, Any]) -> NetworkStatus:
    inn = canonical.get("in_network")
    if inn is True:
        return NetworkStatus.IN_NETWORK
    if inn is False:
        return NetworkStatus.OUT_OF_NETWORK
    return NetworkStatus.UNKNOWN


def _build_categories(canonical: dict[str, Any]) -> list[CategoryBenefit]:
    br = canonical.get("dental_benefit_breakdown") or {}
    by_stc = br.get("coinsurance_patient_pct_by_stc") or {}
    if not isinstance(by_stc, dict):
        return []

    is_active = canonical.get("is_active") is True
    is_cov = canonical.get("is_covered")
    cov_ok = is_cov is not False

    out: list[CategoryBenefit] = []
    for stc, cat in _STC_TO_CATEGORY.items():
        pct = by_stc.get(stc)
        if pct is None:
            continue
        try:
            pf = float(pct)
        except (TypeError, ValueError):
            continue
        # Patient % 0–100; EXPLICIT from normalized dental_benefit_breakdown
        cc = ConfidenceLevel.EXPLICIT
        covered_v = is_active and cov_ok and pf is not None
        out.append(
            CategoryBenefit(
                category=cat,
                covered=data_point_bool(
                    covered_v,
                    confidence=ConfidenceLevel.INFERRED
                    if is_cov is None
                    else ConfidenceLevel.EXPLICIT,
                    source_field=f"benefitsInformation/STC/{stc}/A",
                ),
                coinsurance_patient_pct=data_point_float(
                    pf,
                    confidence=cc,
                    source_field=f"dental_benefit_breakdown/coinsurance_patient_pct_by_stc/{stc}",
                ),
            )
        )
    return out


def _build_ortho(canonical: dict[str, Any], _warnings: list[str]) -> OrthoDetail | None:
    br = canonical.get("dental_benefit_breakdown") or {}
    if not isinstance(br, dict):
        return None
    lt = br.get("ortho_lifetime_max")
    by_stc = br.get("coinsurance_patient_pct_by_stc") or {}
    o38 = by_stc.get("38") if isinstance(by_stc, dict) else None
    age_cutoff_raw = br.get("ortho_age_cutoff")
    if age_cutoff_raw is None:
        for age_row in br.get("age_limits") or []:
            if not isinstance(age_row, dict):
                continue
            if (
                str(age_row.get("category") or "").upper() == "ORTHO"
                and age_row.get("age_max") is not None
            ):
                age_cutoff_raw = age_row.get("age_max")
                break

    if lt is None and o38 is None and age_cutoff_raw is None:
        return None

    try:
        lt_f = float(lt) if lt is not None else None
    except (TypeError, ValueError):
        lt_f = None
    try:
        age_cutoff = int(age_cutoff_raw) if age_cutoff_raw is not None else None
    except (TypeError, ValueError):
        age_cutoff = None

    return OrthoDetail(
        eligible=data_point_bool(
            canonical.get("is_active") is True and canonical.get("is_covered") is not False,
            confidence=ConfidenceLevel.EXPLICIT,
            source_field="canonical",
        ),
        lifetime_max=data_point_float(
            lt_f,
            confidence=ConfidenceLevel.EXPLICIT if lt_f is not None else ConfidenceLevel.UNKNOWN,
            source_field="dental_benefit_breakdown/ortho_lifetime_max",
        ),
        age_cutoff=data_point_int(
            age_cutoff,
            confidence=ConfidenceLevel.EXPLICIT
            if age_cutoff is not None
            else ConfidenceLevel.UNKNOWN,
            source_field="dental_benefit_breakdown/ortho_age_cutoff",
        ),
        in_progress_treatment=data_point_bool(
            None, confidence=ConfidenceLevel.UNKNOWN, source_field="not_extracted_v1"
        ),
        months_remaining=data_point_int(
            None, confidence=ConfidenceLevel.UNKNOWN, source_field="not_extracted_v1"
        ),
    )


def _category_enum(value: str | None) -> BenefitCategory | None:
    if not value:
        return None
    try:
        return BenefitCategory(str(value).upper())
    except ValueError:
        return None


def _build_frequency_limitations(breakdown: dict[str, Any]) -> list[FrequencyLimitation]:
    rows = breakdown.get("frequency_limitations") or []
    out: list[FrequencyLimitation] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if not desc:
            continue
        qty = row.get("quantity")
        period = row.get("period_months")
        confidence = (
            ConfidenceLevel.EXPLICIT
            if qty is not None or period is not None
            else ConfidenceLevel.INFERRED
        )
        age_min = row.get("age_min")
        age_max = row.get("age_max")
        out.append(
            FrequencyLimitation(
                category=_category_enum(row.get("category")),
                cdt_code=row.get("cdt_code"),
                quantity=int(qty) if qty is not None else None,
                quantity_qualifier=row.get("quantity_qualifier"),
                period_months=int(period) if period is not None else None,
                age_min=int(age_min) if age_min is not None else None,
                age_max=int(age_max) if age_max is not None else None,
                description=desc,
                confidence=confidence,
            )
        )
    return out


def _build_age_limits(breakdown: dict[str, Any]) -> list[AgeLimit]:
    rows = breakdown.get("age_limits") or []
    out: list[AgeLimit] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if not desc:
            continue
        age_min = row.get("age_min")
        age_max = row.get("age_max")
        out.append(
            AgeLimit(
                category=_category_enum(row.get("category")),
                cdt_code=row.get("cdt_code"),
                age_min=int(age_min) if age_min is not None else None,
                age_max=int(age_max) if age_max is not None else None,
                description=desc,
                confidence=ConfidenceLevel.EXPLICIT,
            )
        )
    return out


def _build_downgrades(breakdown: dict[str, Any]) -> list[DowngradeClause]:
    rows = breakdown.get("downgrades") or []
    out: list[DowngradeClause] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if not desc:
            continue
        out.append(
            DowngradeClause(
                cdt_from=row.get("cdt_from"),
                cdt_to=row.get("cdt_to"),
                category=_category_enum(row.get("category")),
                description=desc,
                confidence=ConfidenceLevel.EXPLICIT,
            )
        )
    return out


def _build_last_service_dates(canonical: dict[str, Any]) -> list[LastServiceDate]:
    rows = canonical.get("last_service_dates") or []
    out: list[LastServiceDate] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("service_date")
        service_date = _parse_yyyymmdd(str(raw_date)) if raw_date else None
        if service_date is None and isinstance(raw_date, str) and "-" in raw_date:
            try:
                service_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                service_date = None
        out.append(
            LastServiceDate(
                source_benefit_index=row.get("source_benefit_index"),
                cdt_code=row.get("cdt_code"),
                category=_category_enum(row.get("category")),
                service_date=service_date,
                description=row.get("description"),
            )
        )
    return out


def _build_waiting_periods(breakdown: dict[str, Any]) -> list[WaitingPeriod]:
    rows = breakdown.get("waiting_periods") or []
    out: list[WaitingPeriod] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if not desc:
            continue
        end_raw = row.get("end_date")
        end_date = _parse_yyyymmdd(str(end_raw)) if end_raw else None
        months = row.get("months")
        confidence = (
            ConfidenceLevel.EXPLICIT if months is not None or end_date else ConfidenceLevel.INFERRED
        )
        out.append(
            WaitingPeriod(
                category=_category_enum(row.get("category")),
                cdt_code=row.get("cdt_code"),
                months=int(months) if months is not None else None,
                end_date=end_date,
                description=desc,
                confidence=confidence,
            )
        )
    return out


def _build_missing_tooth_clause(breakdown: dict[str, Any]) -> MissingToothClause:
    raw = breakdown.get("missing_tooth_clause")
    if not isinstance(raw, dict):
        return MissingToothClause(
            present=False, description=None, confidence=ConfidenceLevel.UNKNOWN
        )
    present = bool(raw.get("present"))
    desc = raw.get("description")
    confidence = ConfidenceLevel.EXPLICIT if present and desc else ConfidenceLevel.UNKNOWN
    return MissingToothClause(
        present=present,
        description=str(desc).strip() if desc else None,
        confidence=confidence,
    )


def build_universal_dental_record(
    canonical: dict[str, Any],
    raw_stored_271: dict[str, Any],
    stedi_payer_id: str,
) -> UniversalDentalRecord:
    """
    Derive UniversalDentalRecord from existing normalized ``canonical`` (no re-parse of EB rows).

    ``raw_stored_271`` must be the payload persisted to DB (no underscore keys).
    """
    warnings = _warnings_list(canonical)
    dbreak = canonical.get("dental_benefit_breakdown")
    ortho_max_raw = dbreak.get("ortho_lifetime_max") if isinstance(dbreak, dict) else None

    payer = raw_stored_271.get("payer") if isinstance(raw_stored_271.get("payer"), dict) else {}
    sub = (
        raw_stored_271.get("subscriber")
        if isinstance(raw_stored_271.get("subscriber"), dict)
        else {}
    )
    plan_info = (
        raw_stored_271.get("planInformation")
        if isinstance(raw_stored_271.get("planInformation"), dict)
        else {}
    )

    plan_begin, plan_end = _parse_plan_dates(raw_stored_271)

    fin = FinancialSummary(
        annual_max=data_point_float(
            canonical.get("annual_max_total"),
            confidence=_confidence_float(
                _float_or_none(canonical.get("annual_max_total")),
                conflict_markers=(_WARN_MAX_CONFLICT,),
                warnings=warnings,
            ),
            source_field="canonical/annual_max_total",
        ),
        annual_max_used=data_point_float(
            canonical.get("annual_max_used"),
            confidence=_confidence_float(
                _float_or_none(canonical.get("annual_max_used")),
                conflict_markers=(_WARN_MAX_CONFLICT,),
                warnings=warnings,
            ),
            source_field="canonical/annual_max_used",
        ),
        annual_max_remaining=data_point_float(
            canonical.get("annual_max_remaining"),
            confidence=_confidence_float(
                _float_or_none(canonical.get("annual_max_remaining")),
                conflict_markers=(_WARN_MAX_CONFLICT, _WARN_MAX_CLAMP),
                warnings=warnings,
            ),
            source_field="canonical/annual_max_remaining",
        ),
        deductible_total=data_point_float(
            canonical.get("deductible_total"),
            confidence=_confidence_float(
                _float_or_none(canonical.get("deductible_total")),
                conflict_markers=(_WARN_DED_CONFLICT,),
                warnings=warnings,
            ),
            source_field="canonical/deductible_total",
        ),
        deductible_met=data_point_float(
            canonical.get("deductible_met"),
            confidence=_confidence_float(
                _float_or_none(canonical.get("deductible_met")),
                conflict_markers=(_WARN_DED_CONFLICT,),
                warnings=warnings,
            ),
            source_field="canonical/deductible_met",
        ),
        deductible_remaining=data_point_float(
            canonical.get("deductible_remaining"),
            confidence=_confidence_float(
                _float_or_none(canonical.get("deductible_remaining")),
                conflict_markers=(_WARN_DED_CONFLICT, _WARN_DED_CLAMP),
                warnings=warnings,
            ),
            source_field="canonical/deductible_remaining",
        ),
        ortho_lifetime_max=data_point_float(
            ortho_max_raw,
            confidence=_confidence_float(
                _float_or_none(ortho_max_raw),
                conflict_markers=(),
                warnings=warnings,
            ),
            source_field="dental_benefit_breakdown/ortho_lifetime_max",
        ),
        ortho_lifetime_used=data_point_float(
            None,
            confidence=ConfidenceLevel.UNKNOWN,
            source_field="not_extracted_v1",
        ),
        deductible_individual=data_point_float(
            canonical.get("deductible_individual"),
            confidence=ConfidenceLevel.EXPLICIT
            if canonical.get("deductible_individual") is not None
            else ConfidenceLevel.UNKNOWN,
            source_field="canonical/deductible_individual",
        ),
        deductible_family=data_point_float(
            canonical.get("deductible_family"),
            confidence=ConfidenceLevel.EXPLICIT
            if canonical.get("deductible_family") is not None
            else ConfidenceLevel.UNKNOWN,
            source_field="canonical/deductible_family",
        ),
        annual_max_individual=data_point_float(
            canonical.get("annual_max_individual"),
            confidence=ConfidenceLevel.EXPLICIT
            if canonical.get("annual_max_individual") is not None
            else ConfidenceLevel.UNKNOWN,
            source_field="canonical/annual_max_individual",
        ),
        annual_max_family=data_point_float(
            canonical.get("annual_max_family"),
            confidence=ConfidenceLevel.EXPLICIT
            if canonical.get("annual_max_family") is not None
            else ConfidenceLevel.UNKNOWN,
            source_field="canonical/annual_max_family",
        ),
    )

    br_notes = dbreak.get("limitation_notes") if isinstance(dbreak, dict) else []
    if not isinstance(br_notes, list):
        br_notes = []
    notes_str = [str(x) for x in br_notes]
    breakdown = dbreak if isinstance(dbreak, dict) else {}
    frequency_limitations = _build_frequency_limitations(breakdown)
    waiting_periods = _build_waiting_periods(breakdown)
    missing_tooth = _build_missing_tooth_clause(breakdown)
    age_limits = _build_age_limits(breakdown)
    downgrades = _build_downgrades(breakdown)
    last_service_dates = _build_last_service_dates(canonical)
    waiting = (
        bool(waiting_periods)
        or any("waiting" in n.lower() for n in notes_str)
        or any(
            bool(p.get("waiting_period_end"))
            for p in (canonical.get("procedure_details") or [])
            if isinstance(p, dict)
        )
    )

    ortho = _build_ortho(canonical, warnings)
    prior_auth = canonical.get("prior_auth_required")
    if prior_auth is not None:
        prior_auth = bool(prior_auth)

    return UniversalDentalRecord(
        record_id=uuid4(),
        stedi_payer_id=stedi_payer_id,
        payer_name=payer.get("name"),
        subscriber_id=sub.get("memberId"),
        plan_begin_date=plan_begin,
        plan_end_date=plan_end,
        group_number=plan_info.get("groupNumber") if isinstance(plan_info, dict) else None,
        network_status=_network_status(canonical),
        financial=fin,
        categories=_build_categories(canonical),
        ortho=ortho,
        frequency_limitations=frequency_limitations,
        waiting_periods=waiting_periods,
        missing_tooth_clause=missing_tooth,
        waiting_periods_present=waiting,
        limitation_notes=notes_str,
        prior_auth_required=prior_auth,
        last_service_dates=last_service_dates,
        age_limits=age_limits,
        downgrades=downgrades,
        normalization_method=NormalizationMethod.HEURISTIC,
        normalization_timestamp=datetime.now(UTC),
        raw_payload_hash=_hash_raw(raw_stored_271),
        canonical_version=str(canonical.get("normalization_version") or "1.0"),
    )


__all__ = ["build_universal_dental_record"]
