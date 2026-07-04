"""Tests for freezing scrubbed eligibility normalizer fixtures."""

from __future__ import annotations

import json

import pytest

from scripts.freeze_eligibility_fixture import build_fixture, write_fixture


def _sample_payload() -> dict:
    return {
        "raw_271": {
            "tradingPartnerServiceId": "94036",
            "payer": {"name": "Delta Dental of California", "payorIdentification": "94036"},
            "subscriber": {
                "firstName": "Alice",
                "lastName": "Patient",
                "memberId": "MEM123456",
                "dateOfBirth": "19800101",
                "subscriberStatus": "Active",
                "address": {
                    "address1": "123 Main St",
                    "city": "Austin",
                    "state": "TX",
                    "postalCode": "78701",
                },
            },
            "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
            "benefitsInformation": [
                {
                    "code": "1",
                    "name": "Active Coverage",
                    "serviceTypeCodes": ["35"],
                    "procedureCode": "D0120",
                }
            ],
        }
    }


def test_build_fixture_scrubs_identity_without_redacting_benefit_names() -> None:
    fixture = build_fixture(
        _sample_payload(),
        fixture_name="delta_baseline",
        trading_partner_service_id=None,
        coverage_order="primary",
        request_procedure_codes=["D0120"],
    )

    raw_271 = fixture["raw_271"]
    subscriber = raw_271["subscriber"]

    assert fixture["trading_partner_service_id"] == "94036"
    assert fixture["expected"] == {}
    assert fixture["request_procedure_codes"] == ["D0120"]
    assert subscriber["firstName"] == "TEST"
    assert subscriber["lastName"] == "SUBSCRIBER"
    assert subscriber["memberId"] == "REDACTED_MEMBER_ID"
    assert subscriber["dateOfBirth"] == "19000101"
    assert subscriber["subscriberStatus"] == "Active"
    assert subscriber["address"] == "[REDACTED_ADDRESS]"
    assert raw_271["benefitsInformation"][0]["name"] == "Active Coverage"


def test_write_fixture_refuses_overwrite_by_default(tmp_path) -> None:
    fixture = {
        "fixture_name": "delta_baseline",
        "trading_partner_service_id": "94036",
        "coverage_order": "primary",
        "request_procedure_codes": [],
        "raw_271": {},
        "expected": {},
    }

    first_path = write_fixture(fixture, tmp_path, overwrite=False)

    assert json.loads(first_path.read_text(encoding="utf-8")) == fixture
    with pytest.raises(FileExistsError):
        write_fixture(fixture, tmp_path, overwrite=False)
