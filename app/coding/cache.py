"""Small in-process TTL cache for coding reference reads."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}


def cache_get(key: str) -> Any | None:
    now = time.monotonic()
    with _lock:
        item = _store.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < now:
            del _store[key]
            return None
        return value


def cache_set(key: str, value: Any, ttl_seconds: float) -> None:
    ttl = max(0.0, float(ttl_seconds))
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def cached[T](
    key: str,
    ttl_seconds: float,
    factory: Callable[[], T],
) -> T:
    hit = cache_get(key)
    if hit is not None:
        return hit  # type: ignore[no-any-return]
    value = factory()
    cache_set(key, value, ttl_seconds)
    return value


def cache_clear() -> None:
    with _lock:
        _store.clear()
