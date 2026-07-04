"""Monitor pipeline DLQ growth and alert via Sentry."""

from __future__ import annotations

import logging

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.pipeline.store import RUN_TYPE_OPENDENTAL_WRITEBACK

logger = logging.getLogger(__name__)

_LAST_ALERT_COUNT: int | None = None


def count_failed_pipeline_runs(settings: Settings, *, run_type: str | None = None) -> int:
    if not get_neon_dsn(settings):
        return 0
    sql = """
        select count(*)::int as cnt
        from platform.pipeline_runs
        where status = 'failed'
    """
    params: tuple = ()
    if run_type:
        sql += " and run_type = %s"
        params = (run_type,)
    with neon_connection(settings, bypass_rls=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    return int(row[0]) if row else 0


def check_pipeline_dlq_and_alert(settings: Settings) -> dict[str, int]:
    """Emit a Sentry message when failed pipeline runs exceed the configured threshold."""
    global _LAST_ALERT_COUNT

    threshold = max(1, int(getattr(settings, "pipeline_dlq_alert_threshold", 3)))
    failed_total = count_failed_pipeline_runs(settings)
    failed_writebacks = count_failed_pipeline_runs(settings, run_type=RUN_TYPE_OPENDENTAL_WRITEBACK)

    if failed_total >= threshold and failed_total != _LAST_ALERT_COUNT:
        _LAST_ALERT_COUNT = failed_total
        message = (
            f"Pipeline DLQ growth: {failed_total} failed runs "
            f"({failed_writebacks} OpenDental writebacks)"
        )
        logger.error(message)
        try:
            import sentry_sdk

            sentry_sdk.capture_message(message, level="error")
        except ImportError:
            pass

    return {"failed_total": failed_total, "failed_writebacks": failed_writebacks}
