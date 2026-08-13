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
    assert payload["appointment_date"] is None


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


def test_build_od_eligibility_payload_includes_appointment_date(monkeypatch) -> None:
    from datetime import date

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

    payload = mod.build_od_eligibility_payload(
        client,
        pat_num=42,
        practice_id="clinic_a",
        connection={"writeback_enabled": False, "writeback_full": False},
        appointment_date=date(2026, 8, 13),
    )
    assert payload["appointment_date"] == "2026-08-13"


def test_enqueue_retries_idempotency_on_conflict(monkeypatch) -> None:
    monkeypatch.setattr(mod, "od_request_exists_today", lambda *a, **k: False)
    monkeypatch.setattr(
        mod,
        "build_od_eligibility_payload",
        lambda *a, **k: {
            "first_name": "A",
            "last_name": "B",
            "dob": "1980-01-01",
            "subscriber_id": "1",
            "primary_payer_id": "84103",
            "idempotency_key": "od:clinic_a:24:2026-08-13",
            "input_json": {},
        },
    )
    calls: list[str] = []

    def fake_create(settings, *, practice_id, payload):  # type: ignore[no-untyped-def]
        calls.append(str(payload["idempotency_key"]))
        if len(calls) == 1:
            raise ValueError("idempotency_conflict")
        return {"id": "req-retry"}

    monkeypatch.setattr(mod, "create_eligibility_request", fake_create)
    result = mod.enqueue_od_eligibility_check(
        SimpleNamespace(),
        practice_id="clinic_a",
        pat_num=24,
        connection={},
        client=MagicMock(),
    )
    assert result == {"id": "req-retry"}
    assert calls[0] == "od:clinic_a:24:2026-08-13"
    assert calls[1].startswith("od:clinic_a:24:2026-08-13:r")


def test_od_request_exists_today_blocks_in_flight_and_completed(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCur:
        def execute(self, sql, params):  # type: ignore[no-untyped-def]
            captured["sql"] = sql
            captured["params"] = params

        def fetchone(self):  # type: ignore[no-untyped-def]
            return {"ok": 1}

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a):  # type: ignore[no-untyped-def]
            return False

    class FakeConn:
        def cursor(self, **_k):  # type: ignore[no-untyped-def]
            return FakeCur()

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a):  # type: ignore[no-untyped-def]
            return False

    monkeypatch.setattr(mod, "neon_connection", lambda *a, **k: FakeConn())
    assert mod.od_request_exists_today(SimpleNamespace(), practice_id="clinic_a", pat_num=24) is True
    statuses = captured["params"][3]
    assert set(statuses) == {"queued", "processing", "completed"}
    assert "failed" not in statuses
    assert "needs_attention" not in statuses
    assert "status = any(%s)" in str(captured["sql"])


def test_enqueue_proceeds_after_failed_request_today(monkeypatch) -> None:
    monkeypatch.setattr(mod, "od_request_exists_today", lambda *a, **k: False)
    monkeypatch.setattr(
        mod,
        "build_od_eligibility_payload",
        lambda *a, **k: {
            "first_name": "A",
            "last_name": "B",
            "dob": "1980-01-01",
            "subscriber_id": "1",
            "primary_payer_id": "84103",
            "idempotency_key": "od:clinic_a:24:2026-08-13",
            "input_json": {"pat_num": 24, "source": "opendental"},
        },
    )
    monkeypatch.setattr(
        mod,
        "create_eligibility_request",
        lambda *a, **k: {"id": "req-retry-failed"},
    )
    result = mod.enqueue_od_eligibility_check(
        SimpleNamespace(),
        practice_id="clinic_a",
        pat_num=24,
        connection={},
        client=MagicMock(),
    )
    assert result == {"id": "req-retry-failed"}
