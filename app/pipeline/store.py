"""Persist and query ``platform.pipeline_runs`` on Neon."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection

logger = logging.getLogger(__name__)

RUN_TYPE_FULL_RCM_PIPELINE = "full_rcm_pipeline"
RUN_TYPE_ELIGIBILITY_REQUEST = "eligibility_request"
RUN_TYPE_OPENDENTAL_WRITEBACK = "opendental_writeback"


class PipelineNotConfiguredError(RuntimeError):
    """Raised when pipeline queue requires Neon but it is not configured."""


def _require_neon(settings: Settings) -> None:
    if not get_neon_dsn(settings):
        raise PipelineNotConfiguredError("NEON_DATABASE_URL is required for pipeline_runs")


def create_pipeline_run(
    settings: Settings,
    *,
    practice_id: str,
    run_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> UUID:
    """Enqueue a durable pipeline job."""
    _require_neon(settings)
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into platform.pipeline_runs (
                  practice_id, run_type, payload, idempotency_key, max_attempts
                )
                values (%s, %s, %s, %s, %s)
                on conflict (practice_id, idempotency_key)
                  where idempotency_key is not null
                do update set updated_at = now()
                returning id
                """,
                (practice_id, run_type, Jsonb(payload), idempotency_key, max_attempts),
            )
            row = cur.fetchone()
        conn.commit()
    if not row or not row.get("id"):
        raise RuntimeError("pipeline_runs insert returned no id")
    return UUID(str(row["id"]))


def get_pipeline_run(
    settings: Settings,
    run_id: UUID,
    *,
    practice_id: str,
) -> dict[str, Any] | None:
    _require_neon(settings)
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select *
                from platform.pipeline_runs
                where id = %s and practice_id = %s
                limit 1
                """,
                (run_id, practice_id),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def claim_pipeline_runs(
    settings: Settings,
    *,
    worker_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Claim due queued/retrying runs with row-level locking."""
    _require_neon(settings)
    with neon_connection(settings, bypass_rls=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                update platform.pipeline_runs
                set status = 'processing',
                    locked_at = now(),
                    locked_by = %s,
                    started_at = coalesce(started_at, now()),
                    attempt_count = attempt_count + 1,
                    updated_at = now()
                where id in (
                  select id
                  from platform.pipeline_runs
                  where status in ('queued', 'retrying')
                    and (next_retry_at is null or next_retry_at <= now())
                  order by created_at
                  limit %s
                  for update skip locked
                )
                returning *
                """,
                (worker_id, limit),
            )
            rows = cur.fetchall()
        conn.commit()
    return [dict(row) for row in rows]


def complete_pipeline_run(
    settings: Settings,
    run_id: UUID,
    *,
    practice_id: str,
    result: dict[str, Any],
) -> None:
    _require_neon(settings)
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update platform.pipeline_runs
                set status = 'completed',
                    result = %s,
                    error_message = null,
                    error_code = null,
                    completed_at = now(),
                    locked_at = null,
                    locked_by = null,
                    next_retry_at = null,
                    updated_at = now()
                where id = %s and practice_id = %s
                """,
                (Jsonb(result), run_id, practice_id),
            )
        conn.commit()


def fail_pipeline_run(
    settings: Settings,
    run_id: UUID,
    *,
    practice_id: str,
    error_message: str,
    error_code: str | None = None,
    retry: bool = False,
    retry_delay_seconds: float = 30.0,
) -> None:
    _require_neon(settings)
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select attempt_count, max_attempts
                from platform.pipeline_runs
                where id = %s and practice_id = %s
                """,
                (run_id, practice_id),
            )
            row = cur.fetchone()
            attempt_count = int(row["attempt_count"]) if row else 0
            max_attempts = int(row["max_attempts"]) if row else 3

            should_retry = retry and attempt_count < max_attempts
            if should_retry:
                backoff_multiplier = 2 ** max(0, attempt_count - 1)
                delay_seconds = retry_delay_seconds * backoff_multiplier
                next_retry = datetime.now(UTC) + timedelta(seconds=delay_seconds)
                cur.execute(
                    """
                    update platform.pipeline_runs
                    set status = 'retrying',
                        error_message = %s,
                        error_code = %s,
                        next_retry_at = %s,
                        locked_at = null,
                        locked_by = null,
                        updated_at = now()
                    where id = %s and practice_id = %s
                    """,
                    (error_message, error_code, next_retry, run_id, practice_id),
                )
            else:
                cur.execute(
                    """
                    update platform.pipeline_runs
                    set status = 'failed',
                        error_message = %s,
                        error_code = %s,
                        completed_at = now(),
                        locked_at = null,
                        locked_by = null,
                        next_retry_at = null,
                        updated_at = now()
                    where id = %s and practice_id = %s
                    """,
                    (error_message, error_code, run_id, practice_id),
                )
        conn.commit()


def serialize_pipeline_run(row: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe view for API responses."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
