from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.eligibility.models import TriggerEvent
from app.integrations.opendental import eligibility_enqueue as mod


def test_build_od_eligibility_payload_includes_writeback_flags(monkeypatch) -> None:
    patient = SimpleNamespace(
        FName="Jane",
        LName="Doe",
        Birthdate="1980-01-01",
        PatNum=42,
    )
    mapped = SimpleNamespace(
        request=SimpleNamespace(
            patient_id=mod.opendental_patient_uuid(42),
            first_name="Jane",
            last_name="Doe",
            dob="1980-01-01",
            subscriber_id="MEM123",
            primary_payer_id="84103",
            secondary_payer_id=None,
            cdt_codes=["D1110"],
        ),
        primary_pat_plan_num=1,
        primary_plan_num=2,
        primary_ins_sub_num=3,
        primary_carrier_name="Delta",
    )

    client = MagicMock()
    client.get_patient.return_value = patient
    client.get_patient_insurance.return_value = []
    monkeypatch.setattr(mod, "od_to_eligibility_request", lambda *a, **k: mapped)

    connection = {"writeback_enabled": True, "writeback_full": True}
    payload = mod.build_od_eligibility_payload(
        client,
        pat_num=42,
        practice_id="clinic_a",
        connection=connection,
        cdt_codes=["D1110"],
        trigger_event=TriggerEvent.PRE_APPOINTMENT,
    )

    assert payload["input_json"]["source"] == "opendental"
    assert payload["input_json"]["pat_num"] == 42
    assert payload["input_json"]["writeback_enabled"] is True
    assert payload["input_json"]["writeback_full"] is True
    assert payload["idempotency_key"].startswith("od:clinic_a:42:")


def test_enqueue_skips_when_request_exists_today(monkeypatch) -> None:
    monkeypatch.setattr(mod, "od_request_exists_today", lambda *a, **k: True)
    create = MagicMock()
    monkeypatch.setattr(mod, "create_eligibility_request", create)

    result = mod.enqueue_od_eligibility_check(
        SimpleNamespace(),
        practice_id="clinic_a",
        pat_num=24,
        connection={"writeback_enabled": False, "writeback_full": False},
        client=MagicMock(),
    )

    assert result is None
    create.assert_not_called()
