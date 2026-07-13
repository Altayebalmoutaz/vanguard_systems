"""In-process eligibility retry worker.

The eligibility pipeline parks transient failures in ``status='retrying'`` with a
``next_retry_at`` timestamp. This background task re-queues due rows and enqueues
``platform.pipeline_runs`` so the in-process pipeline worker processes them (replacing
the Supabase Edge Function dispatch path).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.config import Settings
from app.config import get_settings as get_app_settings
from app.eligibility.config import EligibilitySettings
from app.eligibility.db import (
    fail_eligibility_request_exhausted,
    fetch_retryable_requests,
    get_eligibility_agent_settings,
    get_supabase,
    insert_eligibility_request_event,
    requeue_eligibility_request,
)
from app.pipeline.store import RUN_TYPE_ELIGIBILITY_REQUEST, create_pipeline_run

logger = logging.getLogger(__name__)


def _enqueue_eligibility_pipeline_run(
    app_settings: Settings,
    *,
    practice_id: str,
    request_id: str | UUID,
) -> None:
    create_pipeline_run(
        app_settings,
        practice_id=practice_id,
        run_type=RUN_TYPE_ELIGIBILITY_REQUEST,
        payload={"request_id": str(request_id)},
        idempotency_key=f"eligibility_pipeline:{request_id}",
    )


def run_retry_sweep(
    settings: EligibilitySettings,
    *,
    supabase: Any | None = None,
    now: datetime | None = None,
    app_settings: Settings | None = None,
) -> dict[str, Any]:
    """Run a single retry sweep. Returns a summary dict (safe to call from a thread)."""
    supabase = supabase if supabase is not None else get_supabase(settings)
    app_settings = app_settings or get_app_settings()
    now = now or datetime.now(UTC)

    agent_settings = get_eligibility_agent_settings(supabase, settings=app_settings)
    if agent_settings is not None and agent_settings.get("auto_retry_enabled") is False:
        return {"skipped": "auto_retry_disabled", "requeued": 0, "exhausted": 0, "considered": 0}

    due = fetch_retryable_requests(
        supabase,
        now_iso=now.isoformat(),
        limit=settings.eligibility_retry_batch_size,
        settings=app_settings,
    )

    requeued = 0
    exhausted = 0
    for row in due:
        request_id = row.get("id")
        if not request_id:
            continue
        practice_id = row.get("practice_id")
        attempt_count = int(row.get("attempt_count") or 0)
        max_attempts = int(row.get("max_attempts") or 0) or 3

        if attempt_count >= max_attempts:
            fail_eligibility_request_exhausted(
                supabase,
                request_id,
                practice_id=str(practice_id) if practice_id else None,
                settings=app_settings,
            )
            insert_eligibility_request_event(
                supabase,
                request_id,
                "retry_exhausted",
                {"attempt_count": attempt_count, "max_attempts": max_attempts},
                practice_id=str(practice_id) if practice_id else None,
                settings=app_settings,
            )
            exhausted += 1
        else:
            requeue_eligibility_request(
                supabase,
                request_id,
                practice_id=str(practice_id) if practice_id else None,
                settings=app_settings,
            )
            if practice_id:
                try:
                    _enqueue_eligibility_pipeline_run(
                        app_settings,
                        practice_id=str(practice_id),
                        request_id=request_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "eligibility retry pipeline enqueue failed for %s: %s",
                        request_id,
                        exc,
                    )
            insert_eligibility_request_event(
                supabase,
                request_id,
                "requeued",
                {"attempt_count": attempt_count, "source": "retry_worker"},
                practice_id=str(practice_id) if practice_id else None,
                settings=app_settings,
            )
            requeued += 1

    return {"requeued": requeued, "exhausted": exhausted, "considered": len(due)}


def _leased_retry_sweep(settings: EligibilitySettings) -> dict[str, Any]:
    from app.db.leases import LEASE_RETRY_WORKER, try_lease

    with try_lease(get_app_settings(), LEASE_RETRY_WORKER) as acquired:
        if not acquired:
            return {"skipped": "lease_held_elsewhere", "requeued": 0, "exhausted": 0}
        return run_retry_sweep(settings)


async def _retry_loop(settings: EligibilitySettings) -> None:
    interval = max(5.0, float(settings.eligibility_retry_worker_interval_seconds))
    logger.info("eligibility retry worker started (interval=%ss)", interval)
    while True:
        try:
            summary = await asyncio.to_thread(_leased_retry_sweep, settings)
            if summary.get("requeued") or summary.get("exhausted"):
                logger.warning("eligibility retry sweep: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("eligibility retry sweep failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


def start_retry_worker(settings: EligibilitySettings) -> asyncio.Task[None]:
    """Launch the retry sweep loop as a background asyncio task."""
    return asyncio.create_task(_retry_loop(settings))
