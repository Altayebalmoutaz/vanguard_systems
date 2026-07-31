"""Tests for VOB specialist-parity (promote + age/downgrade parse + estimates + voice merge)."""

from __future__ import annotations

from app.eligibility.cost_calculator import calculate_responsibility
from app.eligibility.normalizer import normalize
from app.eligibility.services import _vob_details_from_canonical
from app.eligibility.universal_dental.build import build_universal_dental_record
from app.eligibility.voice.bland import (
    map_bland_analysis_to_extracted,
    map_bland_variables_to_extracted,
)
from app.eligibility.voice.reconcile import merge_voice_extraction
from app.integrations.opendental.writeback import build_benefit_snapshot, format_benefit_notes


def _active_raw(**extra: object) -> dict:
    raw: dict = {
        "payer": {"payorIdentification": "P", "name": "Test Payer"},
        "subscriber": {"subscriberStatus": "Active", "memberId": "M1"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [],
        "_request_procedure_codes": ["D2391", "D1110"],
        "_trading_partner_service_id": "P",
    }
    raw.update(extra)
    return raw


def test_normalize_promotes_prior_auth_ind_fam_and_last_service() -> None:
    raw = _active_raw(
        benefitsInformation=[
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": "50",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "coverageLevelCode": "IND",
                "timeQualifierCode": "23",
            },
            {
                "code": "C",
                "name": "Family Deductible",
                "benefitAmount": "150",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "coverageLevelCode": "FAM",
                "timeQualifierCode": "23",
            },
            {
                "code": "F",
                "name": "Annual Maximum",
                "benefitAmount": "1500",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "coverageLevelCode": "IND",
                "timeQualifierCode": "23",
            },
            {
                "code": "A",
                "name": "Co-Insurance",
                "benefitPercent": "0.2",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "priorAuthorizationRequired": True,
                "benefitsDateInformation": {"latestVisitOrConsultation": "20240315"},
            },
        ]
    )
    c = normalize(raw, "primary")
    assert c["prior_auth_required"] is True
    assert c["deductible_individual"] == 50.0
    assert c["deductible_family"] == 150.0
    assert c["annual_max_individual"] == 1500.0
    assert c["last_service_dates"]
    assert c["last_service_dates"][0]["service_date"] == "2024-03-15"
    assert c["normalization_version"] == "1.1"


def test_normalize_parses_age_limits_and_downgrades() -> None:
    raw = _active_raw(
        benefitsInformation=[
            {
                "code": "A",
                "name": "Sealants",
                "benefitPercent": "1.0",
                "serviceTypeCodes": ["23"],
                "inPlanNetworkIndicatorCode": "Y",
                "additionalInformation": [
                    {"description": "Sealants covered up to age 14"},
                    {"description": ("Posterior composite restorations are downgraded to amalgam")},
                ],
            },
            {
                "code": "F",
                "name": "Ortho Lifetime",
                "benefitAmount": "2000",
                "serviceTypeCodes": ["38"],
                "inPlanNetworkIndicatorCode": "Y",
                "additionalInformation": [{"description": "Orthodontics through age 19"}],
            },
        ]
    )
    c = normalize(raw, "primary")
    br = c["dental_benefit_breakdown"]
    ages = br.get("age_limits") or []
    assert any(a.get("age_max") == 14 for a in ages)
    assert any(a.get("age_max") == 19 for a in ages)
    assert br.get("ortho_age_cutoff") == 19
    downs = br.get("downgrades") or []
    assert any("downgraded" in str(d.get("description") or "").lower() for d in downs)
    notes = br.get("limitation_notes") or []
    assert any("downgraded" in n.lower() for n in notes)


def test_udr_includes_promoted_specialist_fields() -> None:
    raw = _active_raw(
        benefitsInformation=[
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": "75",
                "serviceTypeCodes": ["35"],
                "coverageLevelCode": "IND",
                "inPlanNetworkIndicatorCode": "Y",
                "timeQualifierCode": "23",
                "priorAuthorizationRequired": False,
                "benefitsDateInformation": {"lastVisit": "20240101"},
                "additionalInformation": [
                    {"description": "Composite downgraded to amalgam alternate benefit"}
                ],
            }
        ]
    )
    c = normalize(raw, "primary")
    udr = build_universal_dental_record(c, c["raw_response"], "P")
    assert udr.prior_auth_required is False
    assert udr.financial.deductible_individual is not None
    assert udr.financial.deductible_individual.value == 75.0
    assert udr.last_service_dates
    assert udr.downgrades


def test_cost_calculator_applies_downgrade_alternate_fee() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "procedure_details": [{"cdt_code": "D2391", "procedure_covered": True}],
        "dental_benefit_breakdown": {
            "downgrades": [
                {
                    "cdt_from": "D2391",
                    "cdt_to": "D2140",
                    "description": "Composite paid as amalgam",
                }
            ]
        },
    }
    fee_schedule = {
        "contracted": {"P1": {"D2391": 200.0, "D2140": 120.0}},
        "billed": {"D2391": 220.0, "D2140": 130.0},
    }
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["downgrade_applied"] is True
    assert rows[0]["alternate_cdt"] == "D2140"
    assert "alternate_benefit_downgrade_applied" in rows[0]["estimate_flags"]
    # Insurer pays 80% of alternate fee; patient pays remainder of billed allowed.
    assert rows[0]["allowed_amount"] == 200.0
    assert rows[0]["insurance_pays"] == 96.0  # 120 * 0.8
    assert rows[0]["patient_responsibility"] == 104.0  # 200 - 96


def test_voice_merge_specialist_fields() -> None:
    base = {
        "is_active": True,
        "is_covered": True,
        "missing_fields": [],
        "response_complete": True,
        "procedure_details": [],
        "dental_benefit_breakdown": {},
        "last_service_dates": [],
    }
    extracted = {
        "prior_auth_required": True,
        "frequency_limitations": [
            {"description": "Prophy 2 per 12 months", "quantity": 2, "period_months": 12}
        ],
        "waiting_periods": [{"description": "Major 6 months", "months": 6}],
        "last_service_dates": [{"cdt_code": "D1110", "service_date": "2024-06-01"}],
        "age_limits": [{"category": "ORTHO", "age_max": 19, "description": "Ortho to age 19"}],
        "downgrades": [{"description": "Porcelain downgraded on molars"}],
    }
    patched = merge_voice_extraction(base, extracted, session_id="s1")
    assert patched["prior_auth_required"] is True
    assert patched["last_service_dates"]
    br = patched["dental_benefit_breakdown"]
    assert br["frequency_limitations"]
    assert br["waiting_periods"]
    assert br["age_limits"]
    assert br["downgrades"]
    assert br["ortho_age_cutoff"] == 19


def test_bland_maps_pathway_specialist_vars() -> None:
    extracted = map_bland_variables_to_extracted(
        {
            "coverage_active": "true",
            "frequency_limitations": "2 cleanings per year",
            "waiting_periods": "Major 12 months",
            "other_limitations": "Composite downgraded to amalgam; sealants to age 14",
            "prior_auth_required": "yes",
        }
    )
    assert extracted["is_active"] is True
    assert extracted["prior_auth_required"] is True
    assert extracted["frequency_limitations"]
    assert extracted["waiting_periods"]
    assert extracted.get("downgrades") or extracted.get("age_limits")

    analysis = map_bland_analysis_to_extracted(
        {
            "member_active": True,
            "prior_auth_required": False,
            "downgrades": "PFM paid as full metal",
        }
    )
    assert analysis["prior_auth_required"] is False
    assert analysis["downgrades"]


def test_vob_details_forwards_plan_rules_fields() -> None:
    canonical = {
        "prior_auth_required": True,
        "last_service_dates": [{"cdt_code": "D1110", "service_date": "2024-03-15"}],
        "deductible_individual": 50.0,
        "deductible_family": 150.0,
        "annual_max_individual": 1500.0,
        "annual_max_family": 3000.0,
        "dental_benefit_breakdown": {
            "age_limits": [{"description": "Sealants up to age 14"}],
            "downgrades": [{"description": "Composite downgraded to amalgam"}],
            "ortho_age_cutoff": 19,
            "frequency_limitations": [
                {
                    "cdt_code": "D1110",
                    "quantity": 2,
                    "period_months": 12,
                    "description": "Prophy 2 per 12 months",
                }
            ],
            "waiting_periods": [
                {"category": "MAJOR", "months": 12, "description": "Major 12 months"}
            ],
            "missing_tooth_clause": {
                "present": True,
                "description": "Missing tooth clause applies to prosthetics",
            },
        },
    }
    details = _vob_details_from_canonical(canonical)
    assert details["prior_auth_required"] is True
    assert details["last_service_dates"]
    assert details["frequency_limitations"][0]["cdt_code"] == "D1110"
    assert details["waiting_periods"][0]["months"] == 12
    assert details["missing_tooth_clause"]["present"] is True
    assert details["age_limits"]
    assert details["downgrades"]
    assert details["ortho_age_cutoff"] == 19


def test_od_benefit_notes_include_specialist_sections() -> None:
    canonical = {
        "coverage_percent": 80.0,
        "deductible_total": 50.0,
        "deductible_remaining": 25.0,
        "deductible_individual": 50.0,
        "deductible_family": 150.0,
        "annual_max_total": 1500.0,
        "annual_max_remaining": 1200.0,
        "prior_auth_required": True,
        "last_service_dates": [
            {"cdt_code": "D1110", "service_date": "2024-03-15"},
        ],
        "dental_benefit_breakdown": {
            "age_limits": [{"description": "Sealants up to age 14"}],
            "downgrades": [{"description": "Composite downgraded to amalgam"}],
            "frequency_limitations": [],
            "waiting_periods": [],
            "missing_tooth_clause": {"present": False},
        },
    }
    snapshot = build_benefit_snapshot(
        routing={"status": "CLEARED"},
        canonical=canonical,
        procedure_estimates=[],
        carrier_name="Test",
        check_id="chk-1",
    )
    notes = format_benefit_notes(snapshot)
    assert "Prior Auth" in notes
    assert "Required: yes" in notes
    assert "Last Service Dates" in notes
    assert "D1110: 2024-03-15" in notes
    assert "Age Limits" in notes
    assert "Sealants up to age 14" in notes
    assert "Downgrades" in notes
    assert "Individual: $50.00" in notes
