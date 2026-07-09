"""Monitor pipeline DLQ growth and alert via Sentry."""

from __future__ import annotations

import logging

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.pipeline.store import RUN_TYPE_OPENDENTAL_WRITEBACK

logger = logging.getLogger(__name__)

_ALERT_STATE_KEY = "pipeline_dlq_alert"


def _record_alert_count(settings: Settings, failed_total: int) -> bool:
    """Compare-and-set the last alerted count in ``platform.worker_state``.

    Returns True when the count changed (this process should alert). Shared DB
    state dedupes alerts across replicas and restarts. When the table is absent
    (migration 010 not applied yet) we alert rather than stay silent.
    """
    try:
        with neon_connection(settings, bypass_rls=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into platform.worker_state (key, value)
                    values (%s, jsonb_build_object('count', %s::int))
                    on conflict (key) do update
                      set value = excluded.value,
                          updated_at = now()
                      where platform.worker_state.value ->> 'count'
                            is distinct from excluded.value ->> 'count'
                    returning key
                    """,
                    (_ALERT_STATE_KEY, failed_total),
                )
                changed = cur.fetchone() is not None
            conn.commit()
        return changed
    except Exception as exc:
        logger.warning("worker_state alert dedupe unavailable (%s); alerting anyway", exc)
        return True


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
    with neon_connection(settings, bypass_rls=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return int(row[0]) if row else 0


def check_pipeline_dlq_and_alert(settings: Settings) -> dict[str, int]:
    """Emit a Sentry message when failed pipeline runs exceed the configured threshold."""
    threshold = max(1, int(getattr(settings, "pipeline_dlq_alert_threshold", 3)))
    failed_total = count_failed_pipeline_runs(settings)
    failed_writebacks = count_failed_pipeline_runs(settings, run_type=RUN_TYPE_OPENDENTAL_WRITEBACK)

    if failed_total >= threshold and _record_alert_count(settings, failed_total):
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
