from __future__ import annotations

from fastapi.testclient import TestClient

from app.eligibility.main import app
from app.eligibility.models import TriggerEvent
from app.integrations.opendental.models import (
    ODCarrier,
    ODCommlogResponse,
    ODInsuranceRow,
    ODInsVerifyResponse,
    ODPatient,
)


class _StubClient:
    def __init__(self) -> None:
        self.writes: list[dict[str, object]] = []
        self.benefit_notes: list[dict[str, object]] = []
        self.commlogs: list[dict[str, object]] = []

    def get_patient(self, pat_num: int) -> ODPatient:
        return ODPatient(PatNum=pat_num, FName="Aardvark", LName="Dent", Birthdate="1970-12-12")

    def get_patient_insurance(self, pat_num: int) -> list[ODInsuranceRow]:
        return [
            ODInsuranceRow(
                PatPlanNum=101,
                InsSubNum=201,
                PlanNum=301,
                CarrierNum=401,
                CarrierName="Aetna PPO",
                SubscriberID="SUB-1",
                Ordinal=1,
            )
        ]

    def get_carrier(self, carrier_num: int) -> ODCarrier:
        return ODCarrier(CarrierNum=carrier_num, CarrierName="Aetna PPO", ElectID="84103")

    def create_insverify(self, payload):  # type: ignore[no-untyped-def]
        self.writes.append(payload.model_dump(mode="json"))
        return ODInsVerifyResponse(
            InsVerifyNum=777,
            DateLastVerified="2026-05-11",
            VerifyType="PatientEnrollment",
            FKey=101,
            Note="ok",
        )

    def update_inssub_benefit_notes(self, ins_sub_num, plan_num, benefit_notes):  # type: ignore[no-untyped-def]
        self.benefit_notes.append(
            {"ins_sub_num": ins_sub_num, "plan_num": plan_num, "note": benefit_notes}
        )
        return {"InsSubNum": ins_sub_num, "PlanNum": plan_num}

    def create_commlog(self, pat_num, note, **kwargs):  # type: ignore[no-untyped-def]
        self.commlogs.append({"pat_num": pat_num, "note": note})
        return ODCommlogResponse(CommlogNum=55, PatNum=pat_num, Note=note)


def test_from_opendental_route(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    stub = _StubClient()

    monkeypatch.setattr(
        "app.eligibility.main.OpenDentalClient.from_settings",
        lambda _settings: stub,
    )
    monkeypatch.setattr(
        "app.eligibility.main.run_eligibility_check_endpoint",
        lambda req, settings=None: {
            "cached": False,
            "layer0_warnings": [],
            "primary": {
                "check_id": "123",
                "routing": {"status": "CLEARED", "action": "route_coding"},
                "procedure_estimates": [
                    {
                        "cdt_code": "D1110",
                        "procedure_covered": True,
                        "patient_responsibility": 12.5,
                        "insurance_pays": 50.0,
                        "allowed_amount": 62.5,
                    }
                ],
                "canonical": {
                    "is_active": True,
                    "is_covered": True,
                    "payer_id": "84103",
                    "coverage_percent": 100,
                    "deductible_remaining": 50,
                    "annual_max_remaining": 1356,
                },
            },
            "secondary": None,
        },
    )
    monkeypatch.setattr(
        "app.eligibility.main.get_settings",
        lambda: type(
            "S",
            (),
            {
                "opendental_writeback_enabled": True,
                "opendental_write_benefit_notes_enabled": True,
                "opendental_write_subscriber_note_enabled": True,
                "opendental_write_commlog_enabled": True,
                "opendental_write_insadjust_enabled": False,
                "opendental_write_benefits_grid_enabled": False,
                "opendental_auto_poll_enabled": False,
                "eligibility_agent_api_key": "",
            },
        )(),
    )

    client = TestClient(app)
    resp = client.post(
        "/eligibility/from-opendental",
        json={
            "pat_num": 1,
            "trigger_event": TriggerEvent.PRE_APPOINTMENT.value,
            "cdt_codes": ["D1110"],
            "write_back": True,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["primary"]["routing"]["status"] == "CLEARED"
    assert payload["opendental"]["primary_pat_plan_num"] == 101
    assert payload["opendental"]["primary_plan_num"] == 301
    assert payload["opendental"]["primary_ins_sub_num"] == 201
    assert payload["opendental"]["write_back_result"]["InsVerifyNum"] == 777
    assert len(stub.writes) == 2
    verify_types = {w["VerifyType"] for w in stub.writes}
    assert verify_types == {"PatientEnrollment", "InsuranceBenefit"}
    enrollment = next(w for w in stub.writes if w["VerifyType"] == "PatientEnrollment")
    assert "CLEARED" in str(enrollment.get("Note"))
    assert "D1110" in str(enrollment.get("Note")) or "12.5" in str(enrollment.get("Note"))

    # Primary BenefitNotes write (InsSubs) happened with a deterministic snapshot.
    assert len(stub.benefit_notes) == 1
    bn = stub.benefit_notes[0]
    assert bn["ins_sub_num"] == 201
    assert bn["plan_num"] == 301
    assert "[ELIGIBILITY SNAPSHOT | STEDI]" in str(bn["note"])
    assert "Aetna PPO" in str(bn["note"])

    # Front-desk Commlog summary written.
    assert len(stub.commlogs) == 1
    assert "Eligibility" in str(stub.commlogs[0]["note"])

    notes = payload["opendental"]["write_back_notes"]
    assert notes["benefit_notes"]["ins_sub_num"] == 201
    assert notes["commlog"]["pat_num"] == 1
