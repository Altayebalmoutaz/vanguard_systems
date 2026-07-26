"""Enqueue OpenDental write-backs onto the durable pipeline queue."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.config import Settings
from app.db.connection import get_neon_dsn
from app.pipeline.store import RUN_TYPE_OPENDENTAL_WRITEBACK, create_pipeline_run

WRITEBACK_MAX_ATTEMPTS = 5


def build_opendental_writeback_payload(
    *,
    pat_num: int,
    primary_pat_plan_num: int,
    primary_plan_num: int,
    primary_ins_sub_num: int,
    primary_result: dict[str, Any],
    carrier_name: str | None = None,
    plan_name: str | None = None,
    write_benefit_notes: bool = True,
    write_subscriber_note: bool = True,
    write_commlog: bool = True,
    write_insadjust: bool = False,
    write_benefits_grid: bool = False,
    respect_manual_edits: bool = True,
    dry_run_financial: bool = False,
    od_snapshot: dict[str, Any] | None = None,
    coverage_order: str = "primary",
    check_id: str | None = None,
    patient_id: str | None = None,
) -> dict[str, Any]:
    return {
        "pat_num": pat_num,
        "primary_pat_plan_num": primary_pat_plan_num,
        "primary_plan_num": primary_plan_num,
        "primary_ins_sub_num": primary_ins_sub_num,
        "primary_result": primary_result,
        "carrier_name": carrier_name,
        "plan_name": plan_name,
        "write_benefit_notes": write_benefit_notes,
        "write_subscriber_note": write_subscriber_note,
        "write_commlog": write_commlog,
        "write_insadjust": write_insadjust,
        "write_benefits_grid": write_benefits_grid,
        "respect_manual_edits": respect_manual_edits,
        "dry_run_financial": dry_run_financial,
        "od_snapshot": od_snapshot,
        "coverage_order": coverage_order,
        "check_id": check_id,
        "patient_id": patient_id,
    }


def enqueue_opendental_writeback(
    settings: Settings,
    *,
    practice_id: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> UUID | None:
    """Queue an OpenDental write-back for the pipeline worker (Neon required)."""
    if not get_neon_dsn(settings):
        return None
    return create_pipeline_run(
        settings,
        practice_id=practice_id,
        run_type=RUN_TYPE_OPENDENTAL_WRITEBACK,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=WRITEBACK_MAX_ATTEMPTS,
    )
