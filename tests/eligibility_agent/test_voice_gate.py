"""Unit tests for voice escalation gate."""

from __future__ import annotations

from app.eligibility.voice.gate import (
    canonical_voice_escalation_eligible,
    missing_fields_target,
    routing_status_voice_eligible,
)


def test_routing_status_voice_eligible() -> None:
    assert routing_status_voice_eligible("INCOMPLETE") is True
    assert routing_status_voice_eligible("COVERAGE_AMBIGUOUS") is True
    assert routing_status_voice_eligible("CLEARED") is False


def test_canonical_voice_eligible_missing_fields() -> None:
    canonical = {
        "is_active": True,
        "missing_fields": ["annual_max_remaining"],
        "is_covered": True,
    }
    ok, targets = canonical_voice_escalation_eligible(canonical)
    assert ok is True
    assert "annual_max_remaining" in targets


def test_canonical_voice_ineligible_inactive() -> None:
    canonical = {"is_active": False, "missing_fields": ["is_active"]}
    ok, _ = canonical_voice_escalation_eligible(canonical)
    assert ok is False


def test_canonical_voice_ineligible_verify_subscriber() -> None:
    canonical = {
        "is_active": True,
        "missing_fields": ["is_active"],
        "stedi_aaa_actions": [{"action": "verify_subscriber"}],
    }
    ok, _ = canonical_voice_escalation_eligible(canonical)
    assert ok is False


def test_missing_fields_target_is_covered_fallback() -> None:
    canonical = {"missing_fields": [], "is_covered": None}
    targets = missing_fields_target(canonical)
    assert targets[0] == "is_covered"
    # Specialist-depth topics append when already escalating for coverage gaps.
    assert "frequency_limitations" in targets
    assert "prior_auth_required" in targets


def test_missing_fields_target_appends_specialist_depth_to_financial_gaps() -> None:
    canonical = {
        "missing_fields": ["annual_max_remaining"],
        "is_covered": True,
        "dental_benefit_breakdown": {},
    }
    targets = missing_fields_target(canonical)
    assert targets[0] == "annual_max_remaining"
    assert "downgrades" in targets
    assert "last_service_dates" in targets


def test_missing_fields_target_empty_when_financially_complete() -> None:
    canonical = {
        "missing_fields": [],
        "is_covered": True,
        "prior_auth_required": True,
        "last_service_dates": [{"service_date": "2024-01-01"}],
        "dental_benefit_breakdown": {
            "frequency_limitations": [{"description": "2/year"}],
            "waiting_periods": [{"description": "6 months"}],
            "age_limits": [{"description": "age 19"}],
            "downgrades": [{"description": "composite to amalgam"}],
        },
    }
    assert missing_fields_target(canonical) == []


def test_format_missing_fields_for_voice_scopes_to_gaps() -> None:
    from app.eligibility.voice.gate import format_missing_fields_for_voice

    text = format_missing_fields_for_voice(
        ["annual_max_remaining", "deductible_remaining"],
        cdt_codes=["D2740"],
    )
    assert "ONLY verify these missing items" in text
    assert "remaining annual maximum" in text
    assert "D2740" in text
    assert "standard dental benefit set" not in text.lower()
