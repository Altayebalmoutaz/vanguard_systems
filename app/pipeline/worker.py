"""Background worker for ``platform.pipeline_runs``."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
import uuid

from app.config import Settings
from app.pipeline.dlq_monitor import check_pipeline_dlq_and_alert
from app.pipeline.executor import execute_pipeline_run
from app.pipeline.store import claim_pipeline_runs

logger = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


# region agent log
def _agent_debug_log(hypothesis_id: str, message: str, data: dict) -> None:
    try:
        with open("debug-c16f79.log", "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": "c16f79",
                        "runId": "post-fix",
                        "hypothesisId": hypothesis_id,
                        "location": "app/pipeline/worker.py",
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    default=str,
                )
                + "\n"
            )
    except Exception:
        pass
# endregion


def run_pipeline_sweep(settings: Settings) -> dict[str, int]:
    """Claim and execute due pipeline runs once."""
    # region agent log
    _agent_debug_log(
        "H8,H12",
        "pipeline sweep start",
        {"workerId": _WORKER_ID, "batchSize": settings.pipeline_worker_batch_size},
    )
    # endregion
    runs = claim_pipeline_runs(
        settings,
        worker_id=_WORKER_ID,
        limit=settings.pipeline_worker_batch_size,
    )
    # region agent log
    _agent_debug_log(
        "H8,H12",
        "pipeline sweep claimed",
        {
            "workerId": _WORKER_ID,
            "claimed": len(runs),
            "runTypes": [str(run.get("run_type")) for run in runs],
        },
    )
    # endregion
    processed = 0
    for run in runs:
        execute_pipeline_run(settings, run)
        processed += 1
    dlq = check_pipeline_dlq_and_alert(settings)
    # region agent log
    _agent_debug_log(
        "H8,H12",
        "pipeline sweep complete",
        {"workerId": _WORKER_ID, "claimed": len(runs), "processed": processed, **dlq},
    )
    # endregion
    return {"claimed": len(runs), "processed": processed, **dlq}


async def _pipeline_loop(settings: Settings) -> None:
    interval = max(2.0, float(settings.pipeline_worker_interval_seconds))
    logger.info("pipeline worker started (interval=%ss, worker_id=%s)", interval, _WORKER_ID)
    # region agent log
    _agent_debug_log(
        "H8,H12",
        "pipeline worker loop started",
        {"workerId": _WORKER_ID, "interval": interval},
    )
    # endregion
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
