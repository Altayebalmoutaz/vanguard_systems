"""In-process window for a suggest response before Neon persist lands."""

from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID

_TTL_SECONDS = 300.0
_lock = threading.Lock()
_by_request: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_by_id: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def remember_pending_run(
    *,
    practice_id: str,
    request_id: UUID,
    coding_run_id: UUID,
    payer_id: str | None,
    response_payload: dict[str, Any],
) -> None:
    row = {
        "id": str(coding_run_id),
        "practice_id": practice_id,
        "request_id": str(request_id),
        "payer_id": payer_id,
        "response_payload": response_payload,
        "status": response_payload.get("status"),
        "overall_confidence": response_payload.get("overall_confidence"),
    }
    expires = time.monotonic() + _TTL_SECONDS
    with _lock:
        _purge_locked(time.monotonic())
        _by_request[(practice_id, str(request_id))] = (expires, row)
        _by_id[(practice_id, str(coding_run_id))] = (expires, row)


def peek_pending_by_request(practice_id: str, request_id: UUID) -> dict[str, Any] | None:
    return _peek(_by_request, (practice_id, str(request_id)))


def peek_pending_by_id(practice_id: str, coding_run_id: UUID) -> dict[str, Any] | None:
    return _peek(_by_id, (practice_id, str(coding_run_id)))


def clear_pending() -> None:
    with _lock:
        _by_request.clear()
        _by_id.clear()


def _peek(
    store: dict[tuple[str, str], tuple[float, dict[str, Any]]],
    key: tuple[str, str],
) -> dict[str, Any] | None:
    now = time.monotonic()
    with _lock:
        _purge_locked(now)
        item = store.get(key)
        if item is None:
            return None
        expires_at, row = item
        if expires_at < now:
            store.pop(key, None)
            return None
        return dict(row)


def _purge_locked(now: float) -> None:
    for store in (_by_request, _by_id):
        dead = [key for key, (expires_at, _) in store.items() if expires_at < now]
        for key in dead:
            del store[key]
