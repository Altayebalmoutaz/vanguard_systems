"""Unit tests for Layer 4 (integrity)."""

from app.eligibility.integrity import validate_completeness


def test_integrity_happy_all_critical() -> None:
    canonical = {
        "is_active": True,
        "in_network": True,
        "deductible_remaining": 100.0,
        "annual_max_remaining": 500.0,
        "coverage_percent": 80.0,
        "copay": None,
        "coinsurance": 20.0,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is True
    assert out["missing_fields"] == []
    assert out["integrity_policy_version"] == "2.6"
    assert isinstance(out.get("integrity_issues"), list)


def test_integrity_payer_not_reporting_network_is_ok() -> None:
    canonical = {
        "is_active": True,
        "in_network": None,
        "deductible_remaining": 0.0,
        "annual_max_remaining": 0.0,
        "coverage_percent": 100.0,
        "copay": 0.0,
        "coinsurance": 0.0,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is True
    assert "in_network" not in out["missing_fields"]
    issues = out.get("integrity_issues") or []
    assert not any(i.get("code") == "L4_IN_NETWORK_NOT_REPORTED" for i in issues)


def test_integrity_missing_critical_is_active() -> None:
    canonical = {
        "is_active": None,
        "in_network": True,
        "deductible_remaining": 0.0,
        "annual_max_remaining": 0.0,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is False
    assert "is_active" in out["missing_fields"]
    issues = out.get("integrity_issues") or []
    assert any(i.get("code") == "L4_MISSING_CRITICAL_FIELD" and i.get("field") == "is_active" for i in issues)


def test_integrity_null_remainders_ok_when_no_financial_totals() -> None:
    canonical = {
        "is_active": True,
        "in_network": True,
        "deductible_total": None,
        "deductible_met": None,
        "deductible_remaining": None,
        "annual_max_total": None,
        "annual_max_used": None,
        "annual_max_remaining": None,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is True
    assert "deductible_remaining" not in out["missing_fields"]
    assert "annual_max_remaining" not in out["missing_fields"]


def test_integrity_partial_deductible_context_does_not_fail_completeness() -> None:
    """Payer total or met without remainder is common; we warn but do not gate routing."""
    canonical = {
        "is_active": True,
        "in_network": True,
        "deductible_total": 1500.0,
        "deductible_met": None,
        "deductible_remaining": None,
        "annual_max_total": None,
        "annual_max_used": None,
        "annual_max_remaining": None,
        "coverage_percent": 100.0,
        "copay": 0.0,
        "coinsurance": 0.0,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is True
    assert "deductible_remaining" not in out["missing_fields"]
    issues = out.get("integrity_issues") or []
    assert any(i.get("code") == "L4_DEDUCTIBLE_REMAINING_UNKNOWN" for i in issues)


def test_integrity_flags_important_nulls() -> None:
    canonical = {
        "is_active": True,
        "in_network": True,
        "deductible_remaining": 1.0,
        "annual_max_remaining": 1.0,
        "coverage_percent": None,
        "copay": None,
        "coinsurance": None,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is True
    warns = out.get("integrity_warnings") or []
    assert any("important_field_null:coverage_percent" in w for w in warns)
    issues = out.get("integrity_issues") or []
    assert any(i.get("code") == "L4_IMPORTANT_FIELD_NULL" and i.get("field") == "coverage_percent" for i in issues)


def test_integrity_range_and_consistency_warnings() -> None:
    canonical = {
        "is_active": True,
        "in_network": True,
        "deductible_remaining": 1100.0,
        "deductible_total": 1000.0,
        "annual_max_remaining": 2500.0,
        "annual_max_total": 2000.0,
        "coverage_percent": 140.0,
        "copay": -1.0,
        "coinsurance": 120.0,
        "normalization_warnings": [],
    }
    out = validate_completeness(canonical)
    warns = out.get("integrity_warnings") or []
    assert "coverage_percent_out_of_range" in warns
    assert "copay_negative" in warns
    assert "coinsurance_out_of_range" in warns
    assert "deductible_remaining_gt_total" in warns
    assert "annual_max_remaining_gt_total" in warns
    issues = out.get("integrity_issues") or []
    assert any(i.get("field") == "coverage_percent" and i.get("code") == "L4_RANGE_VIOLATION" for i in issues)
    assert any(i.get("field") == "copay" and i.get("code") == "L4_RANGE_VIOLATION" for i in issues)
    assert any(i.get("field") == "coinsurance" and i.get("code") == "L4_RANGE_VIOLATION" for i in issues)
    assert any(i.get("field") == "deductible_remaining" and i.get("code") == "L4_CONSISTENCY_VIOLATION" for i in issues)
    assert any(i.get("field") == "annual_max_remaining" and i.get("code") == "L4_CONSISTENCY_VIOLATION" for i in issues)


def test_integrity_stedi_x12_999_blocks_complete() -> None:
    """Stedi may return a 999 in ``x12`` instead of a 271 — Layer 4 must not mark complete."""
    canonical = {
        "is_active": True,
        "in_network": True,
        "deductible_remaining": 0.0,
        "annual_max_remaining": 0.0,
        "coverage_percent": 100.0,
        "copay": 0.0,
        "coinsurance": 0.0,
        "stedi_x12_transaction_kind": "999",
        "normalization_warnings": ["stedi_x12_payload:implementation_ack_999_not_271"],
    }
    out = validate_completeness(canonical)
    assert out["response_complete"] is False
    assert "stedi_implementation_ack_999" in out["missing_fields"]
    issues = out.get("integrity_issues") or []
    assert any(i.get("code") == "L4_STEDI_X12_999_PAYLOAD" for i in issues)
