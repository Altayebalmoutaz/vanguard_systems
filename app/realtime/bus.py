"""Realtime event bus.

One background task per process holds a dedicated Postgres connection with
``LISTEN rcm_events`` (triggers from ``schema/migrations/008_realtime_notify.sql``)
and republishes every notification to in-process subscribers keyed by
``practice_id``. SSE endpoints subscribe per request. Postgres broadcasts NOTIFY
to every listening connection, so this is safe with multiple app replicas.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.config import Settings
from app.db.connection import get_neon_dsn

logger = logging.getLogger(__name__)

CHANNEL = "rcm_events"
_QUEUE_MAXSIZE = 200


@dataclass
class RealtimeBus:
    """In-process pub/sub with per-practice fan-out."""

    _subscribers: dict[str, dict[int, asyncio.Queue[dict[str, Any]]]] = field(default_factory=dict)
    _counter: itertools.count = field(default_factory=itertools.count)
    _seq: int = 0

    def subscriber_count(self) -> int:
        return sum(len(qs) for qs in self._subscribers.values())

    def publish(self, practice_id: str, event: dict[str, Any]) -> None:
        self._seq += 1
        event = {**event, "seq": self._seq, "ts": time.time()}
        for queue in self._subscribers.get(practice_id, {}).values():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest to keep the stream live.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    async def subscribe(self, practice_id: str) -> AsyncIterator[dict[str, Any]]:
        """Async iterator of events for one practice; cleans up on cancel/close."""
        token = next(self._counter)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.setdefault(practice_id, {})[token] = queue
        try:
            while True:
                yield await queue.get()
        finally:
            practice_queues = self._subscribers.get(practice_id, {})
            practice_queues.pop(token, None)
            if not practice_queues:
                self._subscribers.pop(practice_id, None)


bus = RealtimeBus()


def _handle_notify_payload(payload: str) -> None:
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("rcm_events notification was not valid JSON; dropped")
        return
    practice_id = str(event.get("practice_id") or "").strip()
    if not practice_id:
        return
    bus.publish(practice_id, event)


async def _listen_once(dsn: str) -> None:
    """Hold one LISTEN connection and pump notifications until it drops."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f"listen {CHANNEL}")
        logger.info("realtime listener connected (channel=%s)", CHANNEL)
        async for notify in conn.notifies():
            _handle_notify_payload(notify.payload)


async def _listen_loop(settings: Settings) -> None:
    backoff = 1.0
    while True:
        dsn = get_neon_dsn(settings)
        if not dsn:
            logger.warning("realtime listener idle: DATABASE_URL not configured")
            await asyncio.sleep(30)
            continue
        try:
            await _listen_once(dsn)
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "realtime listener dropped (%s: %s); reconnecting in %.0fs",
                type(exc).__name__,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def start_realtime_listener(settings: Settings) -> asyncio.Task[None]:
    """Launch the LISTEN loop as a background asyncio task."""
    return asyncio.create_task(_listen_loop(settings))
