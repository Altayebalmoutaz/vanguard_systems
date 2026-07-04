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
    assert missing_fields_target(canonical) == ["is_covered"]


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
