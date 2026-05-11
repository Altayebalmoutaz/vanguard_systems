"""Stedi v3 payload shape (encounter + ADA procedure codes)."""

from __future__ import annotations

from uuid import uuid4

from app.eligibility.api_client import build_payload
from app.eligibility.config import EligibilitySettings
from app.eligibility.models import EligibilityRequest, TriggerEvent


def test_build_payload_uses_encounter_and_ada_for_single_cdt() -> None:
    req = EligibilityRequest(
        patient_id=uuid4(),
        first_name="Jane",
        last_name="Doe",
        dob="1990-01-15",
        subscriber_id="M123",
        primary_payer_id="60054",
        cdt_codes=["D0120"],
        trigger_event=TriggerEvent.PRE_APPOINTMENT,
    )
    s = EligibilitySettings.model_validate(
        {
            "STEDI_API_KEY": "x",
            "SUPABASE_URL": "http://localhost",
            "SUPABASE_KEY": "x",
            "PROVIDER_NPI": "1999999984",
            "PROVIDER_NAME": "Test Org",
        }
    )
    p = build_payload(req, s)
    assert "serviceTypeCodes" not in p
    assert p["encounter"]["serviceTypeCodes"] == ["35"]
    assert p["encounter"]["procedureCode"] == "D0120"
    assert p["encounter"]["productOrServiceIDQualifier"] == "AD"
    assert p["provider"]["npi"] == "1999999984"
    assert "serviceProviderNumber" not in p["provider"]


def test_build_payload_person_provider_style() -> None:
    """Stedi mock sometimes uses dentist firstName/lastName + npi instead of organizationName."""
    req = EligibilityRequest(
        patient_id=uuid4(),
        first_name="Falcon",
        last_name="Dent",
        dob="1985-06-07",
        subscriber_id="007007007",
        primary_payer_id="AMTAS00425",
        cdt_codes=["D1110"],
        trigger_event=TriggerEvent.PRE_APPOINTMENT,
        provider_first_name="Plaque",
        provider_last_name="Penguin",
        stedi_provider_npi="1999999984",
    )
    s = EligibilitySettings.model_validate({"STEDI_API_KEY": "x", "PROVIDER_TAX_ID": "123456789"})
    p = build_payload(req, s)
    assert p["provider"] == {"firstName": "Plaque", "lastName": "Penguin", "npi": "1999999984"}


def test_build_payload_organization_name_override() -> None:
    req = EligibilityRequest(
        patient_id=uuid4(),
        first_name="Jaguar",
        last_name="Dent",
        dob="1996-05-05",
        subscriber_id="U3141592653",
        primary_payer_id="62308",
        trigger_event=TriggerEvent.PRE_APPOINTMENT,
        provider_organization_name="One",
        stedi_provider_npi="1999999984",
    )
    s = EligibilitySettings.model_validate({"STEDI_API_KEY": "x"})
    p = build_payload(req, s)
    assert p["provider"]["organizationName"] == "One"
    assert p["provider"]["npi"] == "1999999984"
    assert p["encounter"]["serviceTypeCodes"] == ["35"]
    assert "procedureCode" not in p["encounter"]


def test_build_payload_medical_procedures_for_multiple_cdt() -> None:
    req = EligibilityRequest(
        patient_id=uuid4(),
        first_name="Jane",
        last_name="Doe",
        dob="1990-01-15",
        subscriber_id="M123",
        primary_payer_id="60054",
        cdt_codes=["D0120", "D1110"],
        trigger_event=TriggerEvent.PRE_APPOINTMENT,
    )
    s = EligibilitySettings.model_validate(
        {
            "STEDI_API_KEY": "x",
            "SUPABASE_URL": "http://localhost",
            "SUPABASE_KEY": "x",
        }
    )
    p = build_payload(req, s)
    mp = p["encounter"]["medicalProcedures"]
    assert len(mp) == 2
    assert {x["procedureCode"] for x in mp} == {"D0120", "D1110"}
    assert all(x["productOrServiceIDQualifier"] == "AD" for x in mp)


def test_build_payload_supports_dependent_and_portal_password() -> None:
    req = EligibilityRequest(
        patient_id=uuid4(),
        first_name="Child",
        last_name="Doe",
        dob="2015-03-20",
        subscriber_id="SUB123",
        primary_payer_id="100065",
        cdt_codes=None,
        trigger_event=TriggerEvent.PRE_APPOINTMENT,
        patient_is_dependent=True,
        subscriber_first_name="Jane",
        subscriber_last_name="Doe",
        subscriber_dob="1980-01-15",
        subscriber_member_id="SUB123",
        dependent_relationship_code="19",
        portal_password="PIN123",
    )
    s = EligibilitySettings.model_validate({"STEDI_API_KEY": "x"})
    p = build_payload(req, s)
    assert p["portalPassword"] == "PIN123"
    assert p["subscriber"] == {
        "firstName": "Jane",
        "lastName": "Doe",
        "dateOfBirth": "19800115",
        "memberId": "SUB123",
    }
    assert p["dependents"] == [
        {
            "firstName": "Child",
            "lastName": "Doe",
            "dateOfBirth": "20150320",
            "individualRelationshipCode": "19",
        }
    ]
