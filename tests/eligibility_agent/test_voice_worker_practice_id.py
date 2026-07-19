"""Voice worker must pass practice_id on Neon PHI session updates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.eligibility.voice import worker


def test_run_voice_sweep_passes_practice_id_on_session_updates(monkeypatch) -> None:
    settings = SimpleNamespace(
        voice_verification_worker_enabled=True,
        voice_verification_enabled=True,
        voice_call_provider="bland",
        voice_verification_batch_size=5,
        twilio_webhook_base_url="https://ezfi.smilesuite.ai/eligibility-agent",
        voice_demo_auto_complete=False,
        voice_demo_transcript="",
    )
    session = {
        "id": "sess-queued-1",
        "practice_id": "vgd_mock_brooklyn",
        "payer_id": "62308",
        "request_id": "req-1",
    }
    updates: list[tuple[str, dict, str | None]] = []
    events: list[dict] = []

    monkeypatch.setattr(worker, "bland_configured", lambda _s: True)
    monkeypatch.setattr(worker, "voice_infra_ready", lambda _s: True)
    monkeypatch.setattr(worker, "get_supabase_client", lambda _s: MagicMock())
    monkeypatch.setattr(worker, "fetch_queued_sessions", lambda *_a, **_k: [session])
    monkeypatch.setattr(
        worker,
        "get_eligibility_agent_settings",
        lambda *_a, **_k: {"voice_verification_enabled": True},
    )

    def _capture_update(_supabase, session_id, values, *, practice_id=None, settings=None):
        updates.append((str(session_id), dict(values), practice_id))

    def _capture_event(
        _supabase,
        request_id,
        event_type,
        detail=None,
        *,
        practice_id=None,
        settings=None,
    ):
        events.append(
            {
                "request_id": str(request_id),
                "event_type": event_type,
                "practice_id": practice_id,
            }
        )

    monkeypatch.setattr(worker, "update_verification_session", _capture_update)
    monkeypatch.setattr(worker, "insert_eligibility_request_event", _capture_event)
    monkeypatch.setattr(
        worker,
        "initiate_bland_call",
        lambda *_a, **_k: "bland-call-1",
    )

    summary = worker.run_voice_sweep(settings)  # type: ignore[arg-type]
    assert summary["started"] == 1
    assert summary["errors"] == 0
    assert updates
    assert all(practice_id == "vgd_mock_brooklyn" for _, _, practice_id in updates)
    assert events
    assert all(e["practice_id"] == "vgd_mock_brooklyn" for e in events)
