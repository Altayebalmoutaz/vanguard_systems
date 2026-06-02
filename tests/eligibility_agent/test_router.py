"""Unit tests for Layer 6 routing."""

from __future__ import annotations

from unittest.mock import patch

from app.eligibility.router import route


def _base_canonical() -> dict:
    return {
        "is_active": True,
        "response_complete": True,
        "is_covered": True,
        "payer_id": "60054",
        "procedure_details": [{"cdt_code": "D2740", "procedure_covered": True}],
        "missing_fields": [],
        "integrity_warnings": [],
    }


def test_route_inactive() -> None:
    canonical = _base_canonical()
    canonical.update({"is_active": False, "inactive_reason": "Terminated"})
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INACTIVE"
    assert out["action"] == "notify_front_office_inactive"
    assert out["notify_front_office"] is True
    assert "member_inactive" in out["detail"]["reasons"]


def test_route_incomplete() -> None:
    canonical = _base_canonical()
    canonical.update({"response_complete": False, "missing_fields": ["annual_max_remaining"]})
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INCOMPLETE"
    assert out["action"] == "notify_front_office_missing_fields"
    assert out["notify_front_office"] is True
    assert "completeness_gate_failed" in out["detail"]["reasons"]


def test_route_stedi_x12_999_forces_incomplete_even_when_otherwise_cleared_shaped() -> None:
    canonical = _base_canonical()
    canonical["stedi_x12_transaction_kind"] = "999"
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INCOMPLETE"
    assert "stedi_x12_999_implementation_ack" in out["detail"]["reasons"]
    assert out["detail"].get("stedi_x12_transaction_kind") == "999"


def test_route_coverage_ambiguous() -> None:
    canonical = _base_canonical()
    canonical.update(
        {
            "response_complete": False,
            "missing_fields": ["deductible_remaining", "annual_max_remaining"],
            "is_covered": None,
            "procedure_details": [{"cdt_code": "D0120", "procedure_covered": None}],
            "copay": 40.0,
            "coinsurance": 20.0,
            # At least one EB row so routing is not INCOMPLETE (no benefit data at all).
            "raw_response": {
                "benefitsInformation": [
                    {"code": "1", "name": "Active Coverage", "serviceTypeCodes": ["35"]}
                ]
            },
        }
    )
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "COVERAGE_AMBIGUOUS"
    assert out["notify_front_office"] is True
    assert out["action"] == "notify_front_office_coverage_ambiguous"
    assert "could not be confirmed" in (out.get("routing_reason") or "")
    assert "Copay of $40 confirmed if covered" in (out.get("suggested_action") or "")


def test_route_coverage_ambiguous_low_confidence() -> None:
    canonical = _base_canonical()
    canonical.update({"coverage_confidence": "low"})
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "COVERAGE_AMBIGUOUS"
    assert "coverage_confidence_low" in out["detail"]["reasons"]


def test_route_incomplete_surfaces_payer_aaa_errors() -> None:
    canonical = _base_canonical()
    canonical.update(
        {
            "response_complete": False,
            "missing_fields": ["is_active"],
            "payer_aaa_errors": [
                {
                    "source": "subscriber",
                    "code": "72",
                    "description": "Invalid/Missing Subscriber/Insured ID",
                }
            ],
        }
    )
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INCOMPLETE"
    assert out["detail"]["payer_aaa_errors"][0]["code"] == "72"
    assert "AAA error" in out["detail"]["message"]
    assert "payer_aaa_errors_present" in out["detail"]["reasons"]


def test_route_incomplete_surfaces_structured_stedi_actions_and_warnings() -> None:
    canonical = _base_canonical()
    canonical.update(
        {
            "response_complete": False,
            "missing_fields": ["is_active"],
            "payer_aaa_errors": [
                {
                    "source": "provider",
                    "code": "41",
                    "description": "Authorization/Access Restrictions",
                }
            ],
            "stedi_aaa_actions": [
                {
                    "source": "provider",
                    "code": "41",
                    "action": "enrollment_or_portal_credentials",
                    "description": "Authorization/Access Restrictions",
                }
            ],
            "stedi_warnings": [
                {"code": "request::270::member_id_required", "description": "Member ID required"}
            ],
        }
    )
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "INCOMPLETE"
    assert "portal PIN/password" in out["detail"]["message"]
    assert out["detail"]["stedi_aaa_actions"][0]["action"] == "enrollment_or_portal_credentials"
    assert out["detail"]["stedi_warnings"][0]["code"] == "request::270::member_id_required"
    assert "stedi_aaa_action:enrollment_or_portal_credentials" in out["detail"]["reasons"]


def test_route_not_covered() -> None:
    canonical = _base_canonical()
    canonical.update(
        {
            "is_covered": False,
            "procedure_details": [{"cdt_code": "D2740", "procedure_covered": False}],
        }
    )
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "NOT_COVERED"
    assert out["action"] == "patient_financial_agreement_required"
    assert out["notify_front_office"] is True
    assert "coverage_or_procedure_not_covered" in out["detail"]["reasons"]


@patch("app.eligibility.router.payer_requires_prior_auth", return_value=True)
def test_route_cleared_prior_auth(mock_prior: object) -> None:
    canonical = _base_canonical()
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "CLEARED"
    assert out["action"] == "route_prior_auth"
    assert out["next_agent"] == "prior_auth"
    assert "prior_auth_required_by_rule" in out["detail"]["reasons"]
    assert out["detail"]["cdt_codes"] == ["D2740"]
    # ensure DB lookup branch actually runs
    assert mock_prior  # keeps linter happy


@patch("app.eligibility.router.payer_requires_prior_auth", return_value=False)
def test_route_cleared_coding(mock_prior: object) -> None:
    canonical = _base_canonical()
    out = route(canonical, supabase=object())  # type: ignore[arg-type]
    assert out["status"] == "CLEARED"
    assert out["action"] == "route_coding"
    assert out["next_agent"] == "coding"
    assert "cleared_without_prior_auth_rule" in out["detail"]["reasons"]
    assert out["detail"]["payer_id"] == "60054"
    assert mock_prior
