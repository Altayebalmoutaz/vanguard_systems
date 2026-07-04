"""Tests for HIPAA Safe Harbor de-identification (fail-closed)."""

from __future__ import annotations

import pytest

from app.compliance.deidentification import (
    DeidentificationError,
    DeidentificationETL,
    deidentify_record,
    validate_deidentified,
)


def test_strips_identifiers_and_linkage_keys() -> None:
    source = {
        "patient_id": "11111111-1111-1111-1111-111111111111",
        "patient_name": "Jane Doe",
        "first_name": "Jane",
        "last_name": "Doe",
        "dob": "1988-04-12",
        "subscriber_id": "SUB123",
        "appointment_date": "2026-06-17",
        "payer_id": "84103",
        "routing_status": "CLEARED",
        "confidence": 0.91,
    }
    out = deidentify_record(source)
    assert "patient_id" not in out
    assert "patient_name" not in out
    assert "dob" not in out
    assert out["appointment_year"] == 2026
    assert out["payer_id"] == "84103"
    assert out["routing_status"] == "CLEARED"


def test_fail_closed_on_residual_ssn_in_free_text() -> None:
    with pytest.raises(DeidentificationError, match="SSN"):
        deidentify_record({"notes": "member ssn 123-45-6789 on file"})


def test_validate_rejects_forbidden_key() -> None:
    with pytest.raises(DeidentificationError, match="forbidden key"):
        validate_deidentified({"patient_name": "hidden"})


def test_etl_transform_adds_metadata() -> None:
    etl = DeidentificationETL(dataset_name="golden_eligibility")
    row = etl.transform(
        {
            "patient_id": "abc",
            "checked_at": "2026-01-15T10:00:00Z",
            "routing_status": "CLEARED",
        }
    )
    assert row["checked_year"] == 2026
    assert row["_deid"]["dataset"] == "golden_eligibility"
    assert etl.stats["transformed"] == 1


def test_etl_publish_not_wired() -> None:
    etl = DeidentificationETL(dataset_name="golden_eligibility")
    safe = etl.transform({"routing_status": "CLEARED", "payer_id": "84103"})
    with pytest.raises(NotImplementedError, match="Phase 5"):
        etl.publish(safe)
