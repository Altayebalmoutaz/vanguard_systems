"""UniversalDentalRecord v1 builder from canonical + raw 271."""

from __future__ import annotations

import json
from datetime import date

from app.eligibility.universal_dental.build import build_universal_dental_record
from app.eligibility.universal_dental.models import (
    ConfidenceLevel,
    NetworkStatus,
    NormalizationMethod,
)


def _minimal_canonical(**overrides: object) -> dict:
    base = {
        "is_active": True,
        "is_covered": True,
        "in_network": True,
        "deductible_total": 50.0,
        "deductible_met": 10.0,
        "deductible_remaining": 40.0,
        "annual_max_total": 1500.0,
        "annual_max_used": 200.0,
        "annual_max_remaining": 1300.0,
        "normalization_warnings": [],
        "normalization_version": "1.0",
        "dental_benefit_breakdown": {
            "coinsurance_patient_pct_by_stc": {"23": 20.0, "25": 30.0},
            "ortho_lifetime_max": None,
            "limitation_notes": [],
        },
        "procedure_details": [],
    }
    base.update(overrides)
    return base


def _minimal_raw() -> dict:
    return {
        "payer": {"name": "Test Payer"},
        "subscriber": {"memberId": "M-1"},
        "planInformation": {"groupNumber": "G99"},
        "planDateInformation": {"plan": "20240101-20241231"},
    }


def test_build_universal_dental_record_shape() -> None:
    c = _minimal_canonical()
    raw = _minimal_raw()
    rec = build_universal_dental_record(c, raw, "60054")
    dumped = rec.model_dump(mode="json")
    assert dumped["stedi_payer_id"] == "60054"
    assert dumped["payer_name"] == "Test Payer"
    assert dumped["subscriber_id"] == "M-1"
    assert dumped["group_number"] == "G99"
    assert dumped["plan_begin_date"] == date(2024, 1, 1).isoformat()
    assert dumped["plan_end_date"] == date(2024, 12, 31).isoformat()
    assert dumped["network_status"] == NetworkStatus.IN_NETWORK.value
    assert dumped["normalization_method"] == NormalizationMethod.HEURISTIC.value
    assert isinstance(dumped["record_id"], str)
    fin = dumped["financial"]
    assert fin["deductible_remaining"]["confidence"] == ConfidenceLevel.EXPLICIT.value
    assert fin["deductible_remaining"]["value"] == 40.0
    cats = dumped["categories"]
    assert len(cats) == 2
    stcs = {x["category"]: x["coinsurance_patient_pct"]["value"] for x in cats}
    assert stcs["DIAGNOSTIC"] == 20.0
    assert stcs["BASIC"] == 30.0


def test_deductible_conflict_sets_inferred() -> None:
    c = _minimal_canonical(
        normalization_warnings=[
            "deductible_remaining conflict: derived=35.00 payer=40.00",
        ],
    )
    rec = build_universal_dental_record(c, _minimal_raw(), "payer-x")
    dr = rec.financial.deductible_remaining
    assert dr.confidence == ConfidenceLevel.INFERRED
    assert rec.financial.deductible_total.confidence == ConfidenceLevel.INFERRED


def test_layer3_clamp_annual_remaining_inferred() -> None:
    c = _minimal_canonical(
        normalization_warnings=["layer3_clamp:annual_max_remaining_capped_to_annual_max_total"],
    )
    rec = build_universal_dental_record(c, _minimal_raw(), "payer-x")
    assert rec.financial.annual_max_remaining.confidence == ConfidenceLevel.INFERRED


def test_raw_payload_hash_stable() -> None:
    c = _minimal_canonical()
    raw = _minimal_raw()
    h1 = build_universal_dental_record(c, raw, "1").raw_payload_hash
    h2 = build_universal_dental_record(c, raw, "1").raw_payload_hash
    assert h1 == h2
    raw2 = json.loads(json.dumps(raw))
    assert build_universal_dental_record(c, raw2, "1").raw_payload_hash == h1


def test_ortho_block_when_stc38_or_lifetime() -> None:
    c = _minimal_canonical(
        dental_benefit_breakdown={
            "coinsurance_patient_pct_by_stc": {"38": 50.0},
            "ortho_lifetime_max": None,
            "limitation_notes": [],
        },
    )
    rec = build_universal_dental_record(c, _minimal_raw(), "x")
    assert rec.ortho is not None
    assert rec.financial.ortho_lifetime_max.value is None
    assert rec.financial.ortho_lifetime_max.confidence == ConfidenceLevel.UNKNOWN
