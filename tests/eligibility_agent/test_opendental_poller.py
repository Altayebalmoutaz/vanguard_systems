from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.integrations.opendental.poller as poller


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


def _run_once(monkeypatch, *, appointments, checked_today, queued_today, seen):  # type: ignore[no-untyped-def]
    enqueued: list[int] = []

    def fake_fetch(*, base_url, headers, on_date, timeout):  # type: ignore[no-untyped-def]
        return list(appointments)

    def fake_checked(pat_num):  # type: ignore[no-untyped-def]
        return pat_num in checked_today

    def fake_queued(settings, *, practice_id, pat_num):  # type: ignore[no-untyped-def]
        return pat_num in queued_today

    def fake_enqueue(
        app_settings, *, practice_id, pat_num, connection, client, cdt_codes, trigger_event
    ):  # type: ignore[no-untyped-def]
        enqueued.append(pat_num)
        return {"id": f"req-{pat_num}"}

    class FakeClient:
        developer_key = "dev"
        customer_key = "cust"
        base_url = "http://localhost:30222/api/v1"

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
        appointments=[{"AptNum": 1, "PatNum": 24}, {"AptNum": 2, "PatNum": 24}],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
    )
    assert enqueued == [24]
    assert 24 in seen


def test_poller_skips_patient_checked_today(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24}],
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
        appointments=[{"AptNum": 1, "PatNum": 24}],
        checked_today=set(),
        queued_today=set(),
        seen=seen,
    )
    assert enqueued == []


def test_poller_skips_when_request_queued_today(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    enqueued = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24}],
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
