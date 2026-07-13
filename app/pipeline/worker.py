"""Background worker for ``platform.pipeline_runs``."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid

from app.config import Settings
from app.pipeline.dlq_monitor import check_pipeline_dlq_and_alert
from app.pipeline.executor import execute_pipeline_run
from app.pipeline.store import claim_pipeline_runs

logger = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def run_pipeline_sweep(settings: Settings) -> dict[str, int]:
    """Claim and execute due pipeline runs once."""
    runs = claim_pipeline_runs(
        settings,
        worker_id=_WORKER_ID,
        limit=settings.pipeline_worker_batch_size,
    )
    processed = 0
    for run in runs:
        execute_pipeline_run(settings, run)
        processed += 1
    dlq = check_pipeline_dlq_and_alert(settings)
    return {"claimed": len(runs), "processed": processed, **dlq}


async def _pipeline_loop(settings: Settings) -> None:
    interval = max(2.0, float(settings.pipeline_worker_interval_seconds))
    logger.info("pipeline worker started (interval=%ss, worker_id=%s)", interval, _WORKER_ID)
    while True:
        try:
            summary = await asyncio.to_thread(run_pipeline_sweep, settings)
            if summary.get("processed"):
                logger.warning("pipeline sweep: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("pipeline sweep failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


def start_pipeline_worker(settings: Settings) -> asyncio.Task[None]:
    return asyncio.create_task(_pipeline_loop(settings))
