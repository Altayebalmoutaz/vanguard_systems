"""Eligibility retry worker sweep logic (app/eligibility/retry_worker.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.eligibility import retry_worker

_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
_SENTINEL = object()  # stands in for the supabase client; never dereferenced.


def _settings(batch: int = 10) -> Any:
    return SimpleNamespace(eligibility_retry_batch_size=batch)


def _patch(monkeypatch, *, agent_settings, due):
    """Patch the db helpers the worker imports; record requeue/exhaust/event calls."""
    calls: dict[str, list[Any]] = {"requeue": [], "exhaust": [], "events": []}
    monkeypatch.setattr(
        retry_worker, "get_eligibility_agent_settings", lambda sb, **kwargs: agent_settings
    )
    monkeypatch.setattr(
        retry_worker,
        "fetch_retryable_requests",
        lambda sb, now_iso, limit, **kwargs: due,
    )
    monkeypatch.setattr(
        retry_worker, "requeue_eligibility_request", lambda sb, rid, **kwargs: calls["requeue"].append(rid)
    )
    monkeypatch.setattr(
        retry_worker,
        "fail_eligibility_request_exhausted",
        lambda sb, rid, **kwargs: calls["exhaust"].append(rid),
    )
    monkeypatch.setattr(
        retry_worker,
        "insert_eligibility_request_event",
        lambda sb, rid, event_type, detail=None, **kwargs: calls["events"].append((rid, event_type)),
    )
    monkeypatch.setattr(
        retry_worker,
        "create_pipeline_run",
        lambda *args, **kwargs: calls.setdefault("pipeline", []).append(kwargs),
    )
    return calls


def test_requeues_due_request_with_budget_left(monkeypatch) -> None:
    due = [
        {
            "id": "r1",
            "practice_id": "practice-1",
            "attempt_count": 1,
            "max_attempts": 3,
            "next_retry_at": "2026-06-15T11:59:00Z",
        }
    ]
    calls = _patch(monkeypatch, agent_settings={"auto_retry_enabled": True}, due=due)

    out = retry_worker.run_retry_sweep(_settings(), supabase=_SENTINEL, now=_NOW)

    assert calls["requeue"] == ["r1"]
    assert calls["exhaust"] == []
    assert ("r1", "requeued") in calls["events"]
    assert len(calls.get("pipeline", [])) == 1
    assert calls["pipeline"][0]["run_type"] == "eligibility_request"
    assert out == {"requeued": 1, "exhausted": 0, "considered": 1}


def test_exhausts_request_at_max_attempts(monkeypatch) -> None:
    due = [{"id": "r2", "attempt_count": 3, "max_attempts": 3, "next_retry_at": "2026-06-15T11:00:00Z"}]
    calls = _patch(monkeypatch, agent_settings={"auto_retry_enabled": True}, due=due)

    out = retry_worker.run_retry_sweep(_settings(), supabase=_SENTINEL, now=_NOW)

    assert calls["exhaust"] == ["r2"]
    assert calls["requeue"] == []
    assert ("r2", "retry_exhausted") in calls["events"]
    assert out == {"requeued": 0, "exhausted": 1, "considered": 1}


def test_skips_entirely_when_auto_retry_disabled(monkeypatch) -> None:
    # fetch should never be called when the toggle is off.
    def _boom(*_args, **_kwargs):
        raise AssertionError("fetch_retryable_requests must not run when auto_retry is disabled")

    monkeypatch.setattr(
        retry_worker, "get_eligibility_agent_settings", lambda sb, **kwargs: {"auto_retry_enabled": False}
    )
    monkeypatch.setattr(retry_worker, "fetch_retryable_requests", _boom)

    out = retry_worker.run_retry_sweep(_settings(), supabase=_SENTINEL, now=_NOW)

    assert out["skipped"] == "auto_retry_disabled"
    assert out["requeued"] == 0
    assert out["exhausted"] == 0


def test_runs_when_settings_row_missing(monkeypatch) -> None:
    # No settings row at all → default to running (do not silently stall retries).
    due = [
        {
            "id": "r3",
            "practice_id": "practice-1",
            "attempt_count": 0,
            "max_attempts": 3,
            "next_retry_at": "2026-06-15T10:00:00Z",
        }
    ]
    calls = _patch(monkeypatch, agent_settings=None, due=due)

    out = retry_worker.run_retry_sweep(_settings(), supabase=_SENTINEL, now=_NOW)

    assert calls["requeue"] == ["r3"]
    assert out["requeued"] == 1


def test_mixed_batch_splits_requeue_and_exhaust(monkeypatch) -> None:
    due = [
        {
            "id": "a",
            "practice_id": "practice-1",
            "attempt_count": 0,
            "max_attempts": 3,
            "next_retry_at": "2026-06-15T11:00:00Z",
        },
        {"id": "b", "attempt_count": 3, "max_attempts": 3, "next_retry_at": "2026-06-15T11:00:00Z"},
        {"id": "c", "attempt_count": 5, "max_attempts": 2, "next_retry_at": "2026-06-15T11:00:00Z"},
    ]
    calls = _patch(monkeypatch, agent_settings={"auto_retry_enabled": True}, due=due)

    out = retry_worker.run_retry_sweep(_settings(), supabase=_SENTINEL, now=_NOW)

    assert calls["requeue"] == ["a"]
    assert sorted(calls["exhaust"]) == ["b", "c"]
    assert out == {"requeued": 1, "exhausted": 2, "considered": 3}


def test_passes_batch_size_and_now_to_fetch(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _capture(sb, now_iso, limit, **kwargs):
        captured["now_iso"] = now_iso
        captured["limit"] = limit
        return []

    monkeypatch.setattr(
        retry_worker, "get_eligibility_agent_settings", lambda sb, **kwargs: {"auto_retry_enabled": True}
    )
    monkeypatch.setattr(retry_worker, "fetch_retryable_requests", _capture)

    retry_worker.run_retry_sweep(_settings(batch=7), supabase=_SENTINEL, now=_NOW)

    assert captured["limit"] == 7
    assert captured["now_iso"] == _NOW.isoformat()
