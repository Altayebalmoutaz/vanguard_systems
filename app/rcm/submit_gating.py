"""Claim submit guardrails — block clearinghouse submit while HITL is pending."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from psycopg.rows import dict_row

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.workflow.rcm_tasks import HITL_STATUS_APPROVED, HITL_STATUS_PENDING, HITL_STATUS_REJECTED


def _find_pending_hitl_for_claim(
    settings: Settings,
    *,
    practice_id: str,
    claim_record_id: str,
) -> dict | None:
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select id, status, task_type
            from agents.rcm_tasks
            where practice_id = %s
              and status = %s
              and (
                backend_claim_id = %s
                or pipeline_json->'claim_draft'->>'id' = %s
                or backend_record_id = %s
              )
            limit 1
            """,
            (
                practice_id,
                HITL_STATUS_PENDING,
                claim_record_id,
                claim_record_id,
                claim_record_id,
            ),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _get_hitl_task_status(
    settings: Settings,
    *,
    practice_id: str,
    task_id: str,
) -> str | None:
    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(
            """
            select status
            from agents.rcm_tasks
            where practice_id = %s and id = %s
            limit 1
            """,
            (practice_id, UUID(str(task_id))),
        )
        row = cur.fetchone()
    return str(row["status"]) if row else None


def assert_claim_submission_allowed(
    settings: Settings,
    *,
    practice_id: str,
    claim_record_id: str | None = None,
    hitl_task_id: str | None = None,
) -> None:
    """
    Fail closed when a linked HITL task is still pending or was rejected.
    Approved / overridden tasks (status approved) are allowed through.
    """
    if settings.pilot_shadow_mode:
        raise HTTPException(status_code=403, detail="pilot_shadow_mode")

    if not get_neon_dsn(settings):
        return

    if hitl_task_id:
        status = _get_hitl_task_status(settings, practice_id=practice_id, task_id=hitl_task_id)
        if status is None:
            raise HTTPException(status_code=404, detail="hitl_task_not_found")
        if status == HITL_STATUS_PENDING:
            raise HTTPException(status_code=409, detail="hitl_review_pending")
        if status == HITL_STATUS_REJECTED:
            raise HTTPException(status_code=409, detail="hitl_task_rejected")
        if status != HITL_STATUS_APPROVED:
            raise HTTPException(status_code=409, detail="hitl_task_not_approved")
        return

    if claim_record_id:
        pending = _find_pending_hitl_for_claim(
            settings,
            practice_id=practice_id,
            claim_record_id=str(claim_record_id),
        )
        if pending:
            raise HTTPException(
                status_code=409,
                detail="hitl_review_pending",
            )
