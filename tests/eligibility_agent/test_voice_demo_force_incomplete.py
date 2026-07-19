"""Voice demo override: Jaguar/Elephant Dent force INCOMPLETE on mock clinic."""

from __future__ import annotations

from unittest.mock import patch

from app.eligibility.mock_clinic import (
    DEFAULT_MOCK_PRACTICE_ID,
    apply_voice_demo_force_incomplete,
    is_voice_demo_force_incomplete_patient,
)
from app.eligibility.router import route
from app.eligibility.voice.gate import canonical_voice_escalation_eligible


def test_matches_jaguar_and_elephant_on_mock_practice() -> None:
    assert is_voice_demo_force_incomplete_patient(
        first_name="Jaguar",
        last_name="Dent",
        practice_id=DEFAULT_MOCK_PRACTICE_ID,
    )
    assert is_voice_demo_force_incomplete_patient(
        first_name="Elephant",
        last_name="Dent",
        practice_id=DEFAULT_MOCK_PRACTICE_ID,
    )
    assert not is_voice_demo_force_incomplete_patient(
        first_name="Aardvark",
        last_name="Dent",
        practice_id=DEFAULT_MOCK_PRACTICE_ID,
    )
    assert not is_voice_demo_force_incomplete_patient(
        first_name="Jaguar",
        last_name="Dent",
        practice_id="some_other_practice",
    )


def test_force_incomplete_mutates_canonical_for_voice() -> None:
    canonical = {
        "is_active": True,
        "response_complete": True,
        "is_covered": True,
        "deductible_remaining": 50.0,
        "missing_fields": [],
        "integrity_warnings": [],
        "raw_response": {"benefitsInformation": [{"code": "1"}]},
        "procedure_details": [],
    }
    assert apply_voice_demo_force_incomplete(
        canonical,
        first_name="Jaguar",
        last_name="Dent",
        practice_id=DEFAULT_MOCK_PRACTICE_ID,
    )
    assert canonical["response_complete"] is False
    assert "deductible_remaining" in canonical["missing_fields"]
    assert canonical["deductible_remaining"] is None
    assert "voice_demo_force_incomplete" in canonical["integrity_warnings"]

    ok, targets = canonical_voice_escalation_eligible(canonical)
    assert ok is True
    assert "deductible_remaining" in targets


@patch("app.eligibility.router.fetch_payer_voice_config")
def test_forced_jaguar_routes_incomplete_and_queues_voice(mock_payer_cfg: object) -> None:
    mock_payer_cfg.return_value = {
        "eligibility_phone": "+12082749734",
        "voice_escalation_enabled": True,
    }
    canonical = {
        "is_active": True,
        "response_complete": True,
        "is_covered": True,
        "payer_id": "62308",
        "deductible_remaining": 50.0,
        "missing_fields": [],
        "integrity_warnings": [],
        "raw_response": {"benefitsInformation": [{"code": "1"}]},
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": True}],
    }
    apply_voice_demo_force_incomplete(
        canonical,
        first_name="Jaguar",
        last_name="Dent",
        practice_id=DEFAULT_MOCK_PRACTICE_ID,
    )
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INCOMPLETE"
    assert out["action"] == "queue_voice_verification"
    assert out["detail"]["voice_escalation_eligible"] is True


@patch("app.eligibility.router.fetch_payer_voice_config")
def test_forced_elephant_routes_incomplete_and_queues_voice(mock_payer_cfg: object) -> None:
    mock_payer_cfg.return_value = {
        "eligibility_phone": "+12082749734",
        "voice_escalation_enabled": True,
    }
    canonical = {
        "is_active": True,
        "response_complete": True,
        "is_covered": True,
        "payer_id": "10134",
        "deductible_remaining": 25.0,
        "missing_fields": [],
        "integrity_warnings": [],
        "raw_response": {"benefitsInformation": [{"code": "1"}]},
        "procedure_details": [],
    }
    apply_voice_demo_force_incomplete(
        canonical,
        first_name="Elephant",
        last_name="Dent",
        practice_id=DEFAULT_MOCK_PRACTICE_ID,
    )
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INCOMPLETE"
    assert out["action"] == "queue_voice_verification"
