"""Unit tests for Layer 7 COB logic."""

from __future__ import annotations

import pytest

from app.eligibility.cob import calculate_cob


def test_cob_happy_path_and_policy_version() -> None:
    primary = {
        "response_complete": True,
        "check_id": "p1",
        "procedure_estimates": [
            {"cdt_code": "D1110", "patient_responsibility": 120.0},
            {"cdt_code": "D2740", "patient_responsibility": 300.0},
        ],
    }
    secondary = {
        "response_complete": True,
        "check_id": "s1",
        "procedure_estimates": [
            {"cdt_code": "D1110", "insurance_pays": 80.0},
            {"cdt_code": "D2740", "insurance_pays": 150.0},
        ],
    }

    out = calculate_cob(primary, secondary)
    assert out["cob_policy_version"] == "2.0"
    by = {r["cdt_code"]: r for r in out["cob_lines"]}
    assert by["D1110"]["secondary_pays"] == 80.0
    assert by["D1110"]["final_patient_responsibility"] == 40.0
    assert by["D1110"]["flags"] == []
    assert by["D2740"]["secondary_pays"] == 150.0
    assert by["D2740"]["final_patient_responsibility"] == 150.0


def test_cob_missing_secondary_line_flagged() -> None:
    primary = {
        "response_complete": True,
        "procedure_estimates": [{"cdt_code": "D1110", "patient_responsibility": 50.0}],
    }
    secondary = {
        "response_complete": True,
        "procedure_estimates": [],
    }
    out = calculate_cob(primary, secondary)
    line = out["cob_lines"][0]
    assert line["secondary_pays"] == 0.0
    assert line["final_patient_responsibility"] == 50.0
    assert "missing_secondary_line" in line["flags"]


def test_cob_caps_secondary_and_flags() -> None:
    primary = {
        "response_complete": True,
        "procedure_estimates": [{"cdt_code": "D2740", "patient_responsibility": 100.0}],
    }
    secondary = {
        "response_complete": True,
        "procedure_estimates": [{"cdt_code": "D2740", "insurance_pays": 180.0}],
    }
    out = calculate_cob(primary, secondary)
    line = out["cob_lines"][0]
    assert line["secondary_pays"] == 100.0
    assert line["final_patient_responsibility"] == 0.0
    assert "secondary_cap_applied" in line["flags"]


def test_cob_aggregates_duplicate_codes() -> None:
    primary = {
        "response_complete": True,
        "procedure_estimates": [
            {"cdt_code": "D1110", "patient_responsibility": 20.0},
            {"cdt_code": "d1110", "patient_responsibility": 30.0},
        ],
    }
    secondary = {
        "response_complete": True,
        "procedure_estimates": [{"cdt_code": "D1110", "insurance_pays": 10.0}],
    }
    out = calculate_cob(primary, secondary)
    line = out["cob_lines"][0]
    assert line["patient_responsibility_after_primary"] == 50.0
    assert line["secondary_pays"] == 10.0
    assert line["final_patient_responsibility"] == 40.0


def test_cob_requires_complete_checks() -> None:
    with pytest.raises(ValueError):
        calculate_cob({"response_complete": False}, {"response_complete": True})
    with pytest.raises(ValueError):
        calculate_cob({"response_complete": True}, {"response_complete": False})
