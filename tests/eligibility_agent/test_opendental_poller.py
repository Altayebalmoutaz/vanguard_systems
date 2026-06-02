from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.integrations.opendental.poller as poller


def _settings(window_days: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        opendental_developer_key="dev",
        opendental_customer_key="cust",
        opendental_base_url="http://localhost:30222/api/v1",
        opendental_timeout_seconds=5.0,
        opendental_auto_poll_date_window_days=window_days,
        opendental_auto_poll_cdt_codes="D1110",
        opendental_auto_poll_interval_seconds=60.0,
    )


def _run_once(monkeypatch, *, appointments, checked_today, seen):  # type: ignore[no-untyped-def]
    calls: list[int] = []

    def fake_fetch(*, base_url, headers, on_date, timeout):  # type: ignore[no-untyped-def]
        return list(appointments)

    def fake_checked(pat_num):  # type: ignore[no-untyped-def]
        return pat_num in checked_today

    def fake_runner(*, pat_num, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(pat_num)
        return {"primary": {"routing": {"status": "CLEARED"}}}

    monkeypatch.setattr(poller, "fetch_appointments", fake_fetch)
    monkeypatch.setattr(poller, "_checked_today", fake_checked)

    asyncio.run(
        poller._poll_once(fake_runner, _settings(), seen=seen, cdt_codes=["D1110"])
    )
    return calls


def test_poller_processes_new_patient_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    calls = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24}, {"AptNum": 2, "PatNum": 24}],
        checked_today=set(),
        seen=seen,
    )
    # Same patient on two appointments -> processed exactly once; recorded in seen.
    assert calls == [24]
    assert 24 in seen


def test_poller_skips_patient_checked_today(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = set()
    calls = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24}],
        checked_today={24},
        seen=seen,
    )
    # DB says already verified today -> skip the run but mark seen for the fast path.
    assert calls == []
    assert 24 in seen


def test_poller_skips_already_seen(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: set[int] = {24}
    calls = _run_once(
        monkeypatch,
        appointments=[{"AptNum": 1, "PatNum": 24}],
        checked_today=set(),
        seen=seen,
    )
    assert calls == []


def test_parent_app_lifespan_starts_poller_when_enabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: mounted sub-app lifespans don't run, so the top-level app.main
    lifespan must start the OpenDental poller itself."""
    import app.main as main_module
    from fastapi.testclient import TestClient

    async def _sleep_forever() -> None:
        await asyncio.Event().wait()

    started: dict[str, bool] = {}

    def fake_start(runner, settings):  # type: ignore[no-untyped-def]
        started["called"] = True
        return asyncio.ensure_future(_sleep_forever())

    monkeypatch.setattr(main_module, "start_appointment_poller", fake_start)
    monkeypatch.setattr(
        main_module,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            opendental_auto_poll_enabled=True,
            opendental_auto_poll_interval_seconds=30.0,
            opendental_auto_poll_date_window_days=0,
        ),
    )

    app = main_module.create_app()
    with TestClient(app):
        pass
    assert started.get("called") is True


def test_parent_app_lifespan_skips_poller_when_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.main as main_module
    from fastapi.testclient import TestClient

    started: dict[str, bool] = {}

    def fake_start(runner, settings):  # type: ignore[no-untyped-def]
        started["called"] = True
        return asyncio.ensure_future(asyncio.sleep(0))

    monkeypatch.setattr(main_module, "start_appointment_poller", fake_start)
    monkeypatch.setattr(
        main_module,
        "get_eligibility_settings",
        lambda: SimpleNamespace(
            opendental_auto_poll_enabled=False,
            opendental_auto_poll_interval_seconds=30.0,
            opendental_auto_poll_date_window_days=0,
        ),
    )

    app = main_module.create_app()
    with TestClient(app):
        pass
    assert started.get("called") is None
