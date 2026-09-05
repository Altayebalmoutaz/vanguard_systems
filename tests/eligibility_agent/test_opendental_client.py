from __future__ import annotations

import json
from pathlib import Path

import respx
from httpx import Response

from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.errors import OpenDentalConfigError
from app.integrations.opendental.models import ODInsVerifyCreate


def _client(*, replay_dir: str | None = None) -> OpenDentalClient:
    return OpenDentalClient(
        base_url="http://localhost:30222/api/v1",
        developer_key="dev",
        customer_key="cust",
        timeout_seconds=5.0,
        replay_dir=replay_dir,
    )


@respx.mock
def test_get_patient_uses_odfhir_header() -> None:
    route = respx.get("http://localhost:30222/api/v1/patients/1").mock(
        return_value=Response(
            200, json={"PatNum": 1, "FName": "A", "LName": "B", "Birthdate": "1970-01-01"}
        )
    )
    out = _client().get_patient(1)
    assert out.PatNum == 1
    assert route.called
    sent = route.calls[0].request.headers.get("Authorization")
    assert sent == "ODFHIR dev/cust"


@respx.mock
def test_create_insverify_put() -> None:
    route = respx.put("http://localhost:30222/api/v1/insverifies").mock(
        return_value=Response(
            200,
            json={
                "InsVerifyNum": 999,
                "DateLastVerified": "2026-05-11",
                "VerifyType": "PatientEnrollment",
                "FKey": 101,
                "Note": "ok",
            },
        )
    )
    out = _client().create_insverify(
        ODInsVerifyCreate(
            DateLastVerified="2026-05-11", VerifyType="PatientEnrollment", FKey=101, Note="ok"
        )
    )
    assert route.called
    assert out.InsVerifyNum == 999


@respx.mock
def test_get_procedurelogs_for_appointment() -> None:
    route = respx.get("http://localhost:30222/api/v1/procedurelogs").mock(
        return_value=Response(
            200,
            json=[
                {
                    "ProcNum": 9,
                    "AptNum": 42,
                    "procCode": "T3541",
                    "descript": "Prophy, Adult",
                }
            ],
        )
    )
    out = _client().get_procedurelogs_for_appointment(42)
    assert route.called
    assert len(out) == 1
    assert out[0].procCode == "T3541"
    assert "AptNum=42" in str(route.calls[0].request.url)


@respx.mock
def test_get_procedurelogs_for_appointment_errors_return_empty() -> None:
    respx.get("http://localhost:30222/api/v1/procedurelogs").mock(
        return_value=Response(500, text="boom")
    )
    assert _client().get_procedurelogs_for_appointment(7) == []


def test_replay_mode_short_circuits_http(tmp_path: Path) -> None:
    fixtures = tmp_path / "od"
    fixtures.mkdir()
    (fixtures / "patient_1.json").write_text(
        json.dumps({"PatNum": 1, "FName": "A", "LName": "B", "Birthdate": "1970-01-01"}),
        encoding="utf-8",
    )
    c = _client(replay_dir=str(fixtures))
    out = c.get_patient(1)
    assert out.FName == "A"


def test_replay_patient_reads() -> None:
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "opendental"
    c = _client(replay_dir=str(fixtures))
    procs = c.get_procedures_for_patient(1)
    claims = c.get_claims_for_patient(1)
    assert procs[0]["ProcCode"] == "D1110"
    assert claims[0]["ClaimNum"] == 77


def test_replay_od_read_layer() -> None:
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "opendental"
    c = _client(replay_dir=str(fixtures))
    assert c.get_appointments_for_patient(1)[0]["AptStatus"] == "Scheduled"
    assert c.get_treatment_plan_for_patient(1)[0]["ProcCode"] == "D2393"
    assert c.get_account_summary_for_patient(1)[0]["BalTotal"] == 240.0
    assert c.get_payments_for_patient(1)[0]["PayAmt"] == 50.0
    assert c.get_adjustments_for_patient(1)[0]["AdjAmt"] == -24.0
    assert c.get_claim_procedures_for_patient(1)[0]["Status"] == "Received"
    assert c.get_recalls_for_patient(1)[0]["RecallNum"] == 12
    assert c.get_commlogs_for_patient(1)[0]["CommlogNum"] == 77
    assert c.get_documents_for_patient(1)[0]["DocNum"] == 19
    assert c.get_referrals_for_patient(1)[0]["LName"] == "Endo"
    assert c.get_statements_for_patient(1)[0]["StatementNum"] == 61
    assert c.get_medications_for_patient(1)[0]["MedName"] == "Amoxicillin"
    assert c.get_allergies_for_patient(1)[0]["Description"] == "Penicillin"
    assert c.get_problems_for_patient(1)[0]["ProbStatus"] == "Active"
    assert c.get_perio_exams_for_patient(1)[0]["PerioExamNum"] == 2
    assert c.get_clinical_notes_for_patient(1)[0]["Note"].startswith("Prophy")
    assert len(c.get_family_members_for_patient(1)) == 2


def test_missing_keys_raise() -> None:
    try:
        OpenDentalClient(
            base_url="http://localhost:30222/api/v1",
            developer_key="",
            customer_key="",
            timeout_seconds=5.0,
        )
        assert False, "expected OpenDentalConfigError"
    except OpenDentalConfigError:
        pass
