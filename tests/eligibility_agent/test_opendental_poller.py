from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.integrations.opendental.poller as poller
from app.integrations.opendental.models import ODProcedureLog


def _settings(window_days: int = 0, *, auto_poll_enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        opendental_developer_key="dev",
        opendental_customer_key="cust",
        opendental_base_url="http://localhost:30222/api/v1",
        opendental_timeout_seconds=5.0,
        opendental_auto_poll_enabled=auto_poll_enabled,
        opendental_auto_poll_date_window_days=window_days,
        opendental_auto_poll_cdt_codes="D1110",
        opendental_auto_poll_interval_seconds=60.0,
        opendental_writeback_enabled=False,
        eligibility_retry_worker_enabled=False,
        eligibility_retry_worker_interval_seconds=60.0,
        eligibility_retry_batch_size=20,
        voice_verification_worker_enabled=False,
        voice_verification_enabled=False,
        voice_call_provider="",
        voice_verification_worker_interval_seconds=60.0,
        voice_verification_batch_size=20,
        pilot_shadow_mode=False,
    )


def _connection(*, window_days: int = 0) -> dict:
    return {
        "practice_id": "clinic_a",
        "base_url": "http://localhost:30222/api/v1",
        "customer_key_ref": "OD_KEY",
        "poll_window_days": window_days,
        "cdt_codes": "D1110",
        "writeback_enabled": True,
        "writeback_full": False,
    }


def _run_once(  # type: ignore[no-untyped-def]
    monkeypatch,
    *,
    appointments,
    checked_today,
    queued_today,
    seen,
    procedurelogs_by_apt: dict[int, list[ODProcedureLog]] | None = None,
):
    enqueued: list[dict] = []
    procedurelogs_by_apt = procedurelogs_by_apt or {}

    def fake_fetch(*, base_url, headers, on_date, timeout):  # type: ignore[no-untyped-def]
        return list(appointments)

    def fake_checked(pat_num):  # type: ignore[no-untyped-def]
        return pat_num in checked_today

    def fake_queued(settings, *, practice_id, pat_num):  # type: ignore[no-untyped-def]
        return pat_num in queued_today

    def fake_enqueue(  # type: ignore[no-untyped-def]
        app_settings,
        *,
        practice_id,
        pat_num,
        connection,
        client,
        cdt_codes,
        trigger_event,
        resolve=None,
        apt_nums=None,
        appointment_date=None,
    ):
        enqueued.append(
            {
                "pat_num": pat_num,
                "cdt_codes": list(cdt_codes or []),
                "apt_nums": list(apt_nums or []),
                "cdt_source": getattr(resolve, "cdt_source", None),
                "appointment_date": appointment_date,
            }
        )
        return {"id": f"req-{pat_num}"}

    class FakeClient:
        developer_key = "dev"
        customer_key = "cust"
        base_url = "http://localhost:30222/api/v1"

        def get_procedurelogs_for_appointment(self, apt_num: int) -> list[ODProcedureLog]:
            return list(procedurelogs_by_apt.get(int(apt_num), []))

    monkeypatch.setattr(poller, "fetch_appointments", fake_fetch)
    monkeypatch.setattr(poller, "_checked_today", fake_checked)
    monkeypatch.setattr(poller, "od_request_exists_today", fake_queued)
    monkeypatch.setattr(poller, "enqueue_od_eligibility_check", fake_enqueue)
    monkeypatch.setattr(poller.OpenDentalClient, "from_connection", lambda *a, **k: FakeClient())
    monkeypatch.setattr(poller, "record_poll_result", lambda *a, **k: None)

    poller.run_connection_poll(
        _settings(),
        SimpleNamespace(),
        _connection(),
        seen=seen,
    )
    return enqueued


def test_poller_processes_new_patient_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}, {"AptNum": 2, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
    )
    assert len(enqueued) == 1
    assert enqueued[0]["pat_num"] == 24
    assert enqueued[0]["apt_nums"] == [1, 2]
    assert 24 in seen
    assert enqueued[0]["appointment_date"] is not None


def test_poller_merges_cdt_codes_from_multiple_apts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}, {"AptNum": 2, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
        procedurelogs_by_apt={
            1: [ODProcedureLog(ProcNum=1, AptNum=1, procCode="T3541", descript="Prophy")],
            2: [ODProcedureLog(ProcNum=2, AptNum=2, procCode="T1665", descript="Pano")],
        },
    )
    assert len(enqueued) == 1
    assert enqueued[0]["cdt_codes"] == ["D1110", "D0330"]
    assert enqueued[0]["cdt_source"] == "appointment"
    assert enqueued[0]["apt_nums"] == [1, 2]


def test_poller_uses_clinic_default_when_no_procedurelogs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
        procedurelogs_by_apt={1: []},
    )
    assert enqueued[0]["cdt_codes"] == ["D1110"]
    assert enqueued[0]["cdt_source"] == "clinic_default"


def test_poller_skips_patient_checked_today(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today={24},
        queued_today=set(),
        seen=seen,
    )
    assert enqueued == []
    assert 24 in seen


def test_poller_skips_already_seen(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = {24}
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
    )
    assert enqueued == []


def test_poller_skips_when_request_queued_today(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today={24},
        seen=seen,
    )
    assert enqueued == []
    assert 24 in seen


def test_parent_app_lifespan_starts_poller_when_enabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: mounted sub-app lifespans don't run, so the top-level app.main
    lifespan must start the OpenDental poller itself."""
    from fastapi.testclient import TestClient

    import app.main as main_module

    async def _sleep_forever() -> None:
        await asyncio.Event().wait()

    started: dict[str, bool] = {}

    def fake_start(settings):  # type: ignore[no-untyped-def]
        started["called"] = True
        return asyncio.ensure_future(_sleep_forever())

    monkeypatch.setattr(main_module, "start_appointment_poller", fake_start)
    monkeypatch.setattr(
        main_module,
        "get_eligibility_settings",
        lambda: _settings(auto_poll_enabled=True),
    )

    app = main_module.create_app()
    with TestClient(app):
        pass
    assert started.get("called") is True


def test_parent_app_lifespan_skips_poller_when_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    import app.main as main_module

    started: dict[str, bool] = {}

    def fake_start(settings):  # type: ignore[no-untyped-def]
        started["called"] = True
        return asyncio.ensure_future(asyncio.sleep(0))

    monkeypatch.setattr(main_module, "start_appointment_poller", fake_start)
    monkeypatch.setattr(
        main_module,
        "get_eligibility_settings",
        lambda: _settings(auto_poll_enabled=False),
    )

    app = main_module.create_app()
    with TestClient(app):
        pass
    assert started.get("called") is None


def test_poller_skips_complete_and_broken_appointments(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[
            {"AptNum": 1, "PatNum": 10, "AptStatus": "Complete"},
            {"AptNum": 2, "PatNum": 11, "AptStatus": "Broken"},
            {"AptNum": 3, "PatNum": 12, "AptStatus": "UnschedList"},
            {"AptNum": 4, "PatNum": 13, "AptStatus": "Planned"},
            {"AptNum": 5, "PatNum": 14, "AptStatus": "Scheduled"},
            {"AptNum": 6, "PatNum": 15, "AptStatus": "ASAP"},
        ],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
    )
    assert {row["pat_num"] for row in enqueued} == {14, 15}


def test_poller_uses_earliest_apt_date(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[
            {
                "AptNum": 2,
                "PatNum": 24,
                "AptStatus": "Scheduled",
                "AptDateTime": "2026-08-14 09:00:00",
            },
            {
                "AptNum": 1,
                "PatNum": 24,
                "AptStatus": "Scheduled",
                "AptDateTime": "2026-08-13 08:00:00",
            },
        ],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
    )
    assert str(enqueued[0]["appointment_date"]) == "2026-08-13"


def test_poller_http_error_records_error_not_ok(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    recorded: list[dict] = []

    def boom(**kwargs):  # type: ignore[no-untyped-def]
        raise poller.OpenDentalAPIError("OpenDental GET /appointments failed: 400", status_code=400)

    class FakeClient:
        developer_key = "dev"
        customer_key = "cust"
        base_url = "http://localhost:30222/api/v1"

    monkeypatch.setattr(poller, "fetch_appointments", boom)
    monkeypatch.setattr(poller.OpenDentalClient, "from_connection", lambda *a, **k: FakeClient())
    monkeypatch.setattr(
        poller,
        "record_poll_result",
        lambda *a, **k: recorded.append(k),
    )

    result = poller.run_connection_poll(_settings(), SimpleNamespace(), _connection())
    assert result["status"] == "error"
    assert result["appointments"] == 0
    assert result["processed"] == 0
    assert recorded[0]["status"] == "error"


def test_poller_does_not_keep_failed_patient_in_seen(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()

    def fake_enqueue(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("enqueue failed")

    class FakeClient:
        developer_key = "dev"
        customer_key = "cust"
        base_url = "http://localhost:30222/api/v1"

        def get_procedurelogs_for_appointment(self, apt_num: int):  # type: ignore[no-untyped-def]
            return []

    monkeypatch.setattr(
        poller,
        "fetch_appointments",
        lambda **k: [{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
    )
    monkeypatch.setattr(poller, "_checked_today", lambda pat_num: False)
    monkeypatch.setattr(poller, "od_request_exists_today", lambda *a, **k: False)
    monkeypatch.setattr(poller, "enqueue_od_eligibility_check", fake_enqueue)
    monkeypatch.setattr(poller.OpenDentalClient, "from_connection", lambda *a, **k: FakeClient())
    monkeypatch.setattr(poller, "record_poll_result", lambda *a, **k: None)

    result = poller.run_connection_poll(
        _settings(), SimpleNamespace(), _connection(), seen=seen
    )
    assert result["failed"] == 1
    assert 24 not in seen


def test_fetch_appointments_raises_on_http_400(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakeResp:
        status_code = 400
        text = "bad date"

        def json(self):  # type: ignore[no-untyped-def]
            return []

    class FakeClient:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a):  # type: ignore[no-untyped-def]
            return False

        def get(self, url, headers=None, params=None):  # type: ignore[no-untyped-def]
            return FakeResp()

    monkeypatch.setattr(poller.httpx, "Client", lambda **k: FakeClient())
    try:
        poller.fetch_appointments(
            base_url="http://localhost/api/v1",
            headers={},
            on_date="2026-08-13",
            timeout=5,
        )
        raise AssertionError("expected OpenDentalAPIError")
    except poller.OpenDentalAPIError as exc:
        assert exc.status_code == 400


def test_second_poll_pass_reenqueues_when_prior_request_failed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Auto-poll uses a fresh seen set each pass, so failed patients can retry."""
    first = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today=set(),
        seen=set(),
    )
    second = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24, "AptStatus": "Scheduled"}],
        checked_today=set(),
        queued_today=set(),
        seen=set(),
    )
    assert [row["pat_num"] for row in first] == [24]
    assert [row["pat_num"] for row in second] == [24]


def test_auto_poll_loop_does_not_keep_process_lifetime_seen() -> None:
    import inspect

    source = inspect.getsource(poller._poll_loop)
    assert "seen_by_practice" not in source
    assert "for_auto_poll=True" in source
    assert "seen=" not in source

