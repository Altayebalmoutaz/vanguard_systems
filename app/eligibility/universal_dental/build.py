"""Build UniversalDentalRecord v1 from Layer 3 canonical + stored raw 271 (heuristic)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from app.eligibility.universal_dental.models import (
    BenefitCategory,
    CategoryBenefit,
    ConfidenceLevel,
    FinancialSummary,
    NetworkStatus,
    NormalizationMethod,
    OrthoDetail,
    UniversalDentalRecord,
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

    if lt is None and o38 is None:
        return None

    try:
        lt_f = float(lt) if lt is not None else None
    except (TypeError, ValueError):
        lt_f = None

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
            None, confidence=ConfidenceLevel.UNKNOWN, source_field="not_extracted_v1"
        ),
        in_progress_treatment=data_point_bool(
            None, confidence=ConfidenceLevel.UNKNOWN, source_field="not_extracted_v1"
        ),
        months_remaining=data_point_int(
            None, confidence=ConfidenceLevel.UNKNOWN, source_field="not_extracted_v1"
        ),
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
    )

    br_notes = dbreak.get("limitation_notes") if isinstance(dbreak, dict) else []
    if not isinstance(br_notes, list):
        br_notes = []
    notes_str = [str(x) for x in br_notes]
    waiting = any("waiting" in n.lower() for n in notes_str) or any(
        bool(p.get("waiting_period_end"))
        for p in (canonical.get("procedure_details") or [])
        if isinstance(p, dict)
    )

    ortho = _build_ortho(canonical, warnings)

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
        waiting_periods_present=waiting,
        limitation_notes=notes_str,
        normalization_method=NormalizationMethod.HEURISTIC,
        normalization_timestamp=datetime.now(UTC),
        raw_payload_hash=_hash_raw(raw_stored_271),
        canonical_version=str(canonical.get("normalization_version") or "1.0"),
    )


__all__ = ["build_universal_dental_record"]
