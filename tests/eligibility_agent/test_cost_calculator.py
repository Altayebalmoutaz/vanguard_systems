"""Unit tests for Layer 5 cost calculator."""

from __future__ import annotations

import pytest

from app.eligibility.cost_calculator import (
    apply_coinsurance_ambiguous_missing_field,
    build_coverage_ambiguous_partial_estimates,
    calculate_responsibility,
)


def test_cost_calculator_applies_deductible_then_annual_max_cap() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 100.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 150.0,
        "procedure_details": [
            {"cdt_code": "D1110", "procedure_covered": True},
            {"cdt_code": "D2740", "procedure_covered": True},
        ],
    }
    fee_schedule = {
        "contracted": {"P1": {"D1110": 200.0, "D2740": 300.0}},
        "billed": {"D1110": 220.0, "D2740": 330.0},
    }

    rows = calculate_responsibility(canonical, fee_schedule)
    assert len(rows) == 2

    assert rows[0]["cdt_code"] == "D1110"
    assert rows[0]["allowed_amount"] == 200.0
    assert rows[0]["insurance_pays"] == 80.0
    assert rows[0]["patient_responsibility"] == 120.0
    assert "annual_max_cap_applied" not in rows[0]["estimate_flags"]

    assert rows[1]["cdt_code"] == "D2740"
    assert rows[1]["allowed_amount"] == 300.0
    assert rows[1]["insurance_pays"] == 70.0
    assert rows[1]["patient_responsibility"] == 230.0
    assert "annual_max_cap_applied" in rows[1]["estimate_flags"]


def test_cost_calculator_noncovered_with_missing_fee_sets_flag() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 1000.0,
        "procedure_details": [{"cdt_code": "D9999", "procedure_covered": False}],
    }
    fee_schedule = {"contracted": {"P1": {}}, "billed": {}}

    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["allowed_amount"] == 0.0
    assert rows[0]["insurance_pays"] == 0.0
    assert rows[0]["patient_responsibility"] == 0.0
    assert "missing_fee_schedule_or_billed_amount" in rows[0]["estimate_flags"]


def test_cost_calculator_out_of_network_uses_billed_amount() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": False,
        "deductible_remaining": 0.0,
        "coverage_percent": 50.0,
        "annual_max_remaining": 1000.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {
        "contracted": {"P1": {"D0120": 40.0}},
        "billed": {"D0120": 120.0},
    }

    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["allowed_amount"] == 120.0
    assert rows[0]["insurance_pays"] == 60.0
    assert rows[0]["patient_responsibility"] == 60.0


def test_fee_network_directory_overrides_271_for_allowed_amount() -> None:
    """in_network_for_fees True uses contracted amounts even when 271 in_network is False."""
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": False,
        "in_network_for_fees": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 50.0,
        "annual_max_remaining": 1000.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {
        "contracted": {"P1": {"D0120": 40.0}},
        "billed": {"D0120": 120.0},
    }
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["allowed_amount"] == 40.0


def test_coverage_ambiguous_copay_only_estimate() -> None:
    canonical = {
        "copay": 40.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": None}],
    }
    rows = build_coverage_ambiguous_partial_estimates(canonical)
    assert len(rows) == 1
    assert rows[0]["patient_responsibility"] == 40.0
    assert rows[0]["estimate_basis"] == "copay_only"
    assert rows[0]["estimate_confidence"] == "medium"
    assert "deductible remaining unknown" in rows[0]["warning"]


def test_coinsurance_ambiguous_adds_missing_field_when_max_remaining_unknown() -> None:
    canonical = {
        "coinsurance": 20.0,
        "annual_max_total": 7000.0,
        "annual_max_remaining": None,
        "missing_fields": ["annual_max_remaining"],
    }
    apply_coinsurance_ambiguous_missing_field(canonical)
    assert "max_remaining_required_for_coinsurance_estimate" in canonical["missing_fields"]


def test_out_of_pocket_max_remaining_caps_patient_share_once_per_procedure() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "out_of_pocket_max_remaining": 15.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["insurance_pays"] == pytest.approx(85.0)
    assert rows[0]["patient_responsibility"] == pytest.approx(15.0)
    assert "out_of_pocket_max_remaining_cap_applied" in rows[0]["estimate_flags"]


def test_out_of_pocket_max_remaining_runs_across_multiple_procedures() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "out_of_pocket_max_remaining": 35.0,
        "procedure_details": [
            {"cdt_code": "D0120", "procedure_covered": True},
            {"cdt_code": "D1110", "procedure_covered": True},
        ],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0, "D1110": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["patient_responsibility"] == pytest.approx(20.0)
    assert "out_of_pocket_max_remaining_cap_applied" not in rows[0]["estimate_flags"]
    assert rows[1]["patient_responsibility"] == pytest.approx(15.0)
    assert "out_of_pocket_max_remaining_cap_applied" in rows[1]["estimate_flags"]
    assert rows[1]["insurance_pays"] == pytest.approx(85.0)


def test_out_of_pocket_max_remaining_unknown_skips_cap() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "out_of_pocket_max_remaining": None,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["patient_responsibility"] == pytest.approx(20.0)
    assert "out_of_pocket_max_remaining_cap_applied" not in rows[0]["estimate_flags"]


def test_oop_and_annual_max_reconcile_without_conflict_when_pool_allows() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 100.0,
        "out_of_pocket_max_remaining": 15.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["insurance_pays"] == pytest.approx(85.0)
    assert rows[0]["patient_responsibility"] == pytest.approx(15.0)
    assert "benefit_caps_conflict_am_priority" not in rows[0]["estimate_flags"]
    assert "out_of_pocket_max_remaining_cap_applied" in rows[0]["estimate_flags"]


def test_infeasible_oop_vs_annual_max_falls_back_am_priority() -> None:
    """When OOP remainder and annual max pool cannot both bind, insurer pays min(raw, max_left)."""
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 45.0,
        "out_of_pocket_max_remaining": 20.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["insurance_pays"] == pytest.approx(45.0)
    assert rows[0]["patient_responsibility"] == pytest.approx(55.0)
    assert "benefit_caps_conflict_am_priority" in rows[0]["estimate_flags"]


def test_spend_down_remaining_cap_parallel_to_oop() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "spend_down_remaining": 12.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["patient_responsibility"] == pytest.approx(12.0)
    assert rows[0]["insurance_pays"] == pytest.approx(88.0)
    assert "spend_down_remaining_cap_applied" in rows[0]["estimate_flags"]


def test_cost_containment_remaining_cap() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "cost_containment_remaining": 10.0,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["patient_responsibility"] == pytest.approx(10.0)
    assert rows[0]["insurance_pays"] == pytest.approx(90.0)
    assert "cost_containment_remaining_cap_applied" in rows[0]["estimate_flags"]


def test_spend_down_remaining_decrements_across_lines() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "spend_down_remaining": 35.0,
        "procedure_details": [
            {"cdt_code": "D0120", "procedure_covered": True},
            {"cdt_code": "D1110", "procedure_covered": True},
        ],
    }
    fee_schedule = {"contracted": {"P1": {"D0120": 100.0, "D1110": 100.0}}, "billed": {}}
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["patient_responsibility"] == pytest.approx(20.0)
    assert rows[1]["patient_responsibility"] == pytest.approx(15.0)
    assert "spend_down_remaining_cap_applied" in rows[1]["estimate_flags"]


def test_flat_visit_copay_from_271_applied_once_across_procedures() -> None:
    canonical = {
        "response_complete": True,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "copay": 25.0,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 5000.0,
        "procedure_details": [
            {"cdt_code": "D0120", "procedure_covered": True},
            {"cdt_code": "D1110", "procedure_covered": True},
        ],
    }
    fee_schedule = {
        "contracted": {"P1": {"D0120": 50.0, "D1110": 100.0}},
        "billed": {},
    }
    rows = calculate_responsibility(canonical, fee_schedule)
    assert rows[0]["patient_responsibility"] == pytest.approx(35.0)
    assert "flat_visit_copay_from_271_applied_once" in rows[0]["estimate_flags"]
    assert rows[1]["patient_responsibility"] == pytest.approx(20.0)
    assert "flat_visit_copay_from_271_applied_once" not in rows[1]["estimate_flags"]


def test_cost_calculator_requires_complete_active_canonical() -> None:
    canonical = {
        "response_complete": False,
        "is_active": True,
        "payer_id": "P1",
        "in_network": True,
        "deductible_remaining": 0.0,
        "coverage_percent": 80.0,
        "annual_max_remaining": 1000.0,
        "procedure_details": [],
    }
    fee_schedule = {"contracted": {"P1": {}}, "billed": {}}
    with pytest.raises(ValueError):
        calculate_responsibility(canonical, fee_schedule)
