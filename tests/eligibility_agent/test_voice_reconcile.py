"""Unit tests for voice reconciliation merge."""

from __future__ import annotations

from datetime import UTC, datetime

from app.eligibility.voice.reconcile import merge_voice_extraction


def _base_canonical() -> dict:
    return {
        "payer_id": "84103",
        "checked_at": datetime.now(UTC),
        "is_active": True,
        "is_covered": None,
        "missing_fields": ["annual_max_remaining", "deductible_remaining"],
        "response_complete": False,
        "procedure_details": [{"cdt_code": "D2740", "procedure_covered": None}],
        "integrity_warnings": [],
        "normalization_version": "1.0",
    }


def test_merge_voice_extraction_fills_fields() -> None:
    extracted = {
        "annual_max_remaining": 1500.0,
        "deductible_remaining": 50.0,
        "is_covered": True,
        "call_reference": "REF-123",
        "procedure_details": [{"cdt_code": "D2740", "procedure_covered": True}],
    }
    patched = merge_voice_extraction(
        _base_canonical(),
        extracted,
        session_id="sess-1",
        call_reference="REF-123",
    )
    assert patched["annual_max_remaining"] == 1500.0
    assert patched["deductible_remaining"] == 50.0
    assert patched["is_covered"] is True
    assert patched["voice_verification"]["session_id"] == "sess-1"
    assert patched["procedure_details"][0]["procedure_covered"] is True
    assert patched["response_complete"] is True
    assert patched["missing_fields"] == []


def test_voice_recovery_complete() -> None:
    from app.eligibility.voice.reconcile import voice_recovery_complete

    patched = merge_voice_extraction(
        _base_canonical(),
        {
            "annual_max_remaining": 1500.0,
            "deductible_remaining": 50.0,
            "is_covered": True,
        },
        session_id="sess-1",
    )
    assert voice_recovery_complete(patched) is True
    inactive = merge_voice_extraction(
        {**_base_canonical(), "is_active": False},
        {"annual_max_remaining": 1500.0},
        session_id="sess-2",
    )
    assert voice_recovery_complete(inactive) is False
