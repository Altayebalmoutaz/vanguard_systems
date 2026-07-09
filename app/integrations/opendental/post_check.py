"""Post-eligibility hooks: OpenDental writeback after a completed request."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.config import Settings
from app.eligibility.config import get_settings as get_eligibility_settings
from app.integrations.opendental.connections_store import get_connection
from app.pipeline.writeback_queue import (
    build_opendental_writeback_payload,
    enqueue_opendental_writeback,
)

logger = logging.getLogger(__name__)


def _writeback_flags(input_json: dict[str, Any]) -> dict[str, bool]:
    if bool(input_json.get("writeback_full")):
        return {
            "write_benefit_notes": True,
            "write_subscriber_note": True,
            "write_commlog": True,
            "write_insadjust": True,
            "write_benefits_grid": True,
        }
    return {
        "write_benefit_notes": True,
        "write_subscriber_note": True,
        "write_commlog": True,
        "write_insadjust": False,
        "write_benefits_grid": False,
    }


def maybe_enqueue_od_writeback(
    app_settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
    row: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    elig_settings = get_eligibility_settings()
    if elig_settings.pilot_shadow_mode:
        return None

    input_json = row.get("input_json") or {}
    if not isinstance(input_json, dict) or input_json.get("source") != "opendental":
        return None
    if not input_json.get("writeback_enabled"):
        return None

    connection = get_connection(app_settings, practice_id=practice_id)
    if not connection or not connection.get("writeback_enabled"):
        return None

    pat_num = input_json.get("pat_num")
    primary = result.get("primary")
    if pat_num is None or not isinstance(primary, dict):
        return None

    flags = _writeback_flags(input_json)
    wb_payload = build_opendental_writeback_payload(
        pat_num=int(pat_num),
        primary_pat_plan_num=int(input_json["primary_pat_plan_num"]),
        primary_plan_num=int(input_json["primary_plan_num"]),
        primary_ins_sub_num=int(input_json["primary_ins_sub_num"]),
        primary_result=primary,
        carrier_name=input_json.get("primary_carrier_name"),
        check_id=primary.get("check_id"),
        patient_id=str(row.get("patient_id") or ""),
        **flags,
        respect_manual_edits=elig_settings.opendental_write_benefits_grid_respect_manual_edits,
    )
    wb_payload["practice_id"] = practice_id
    check_id = primary.get("check_id")
    idempotency_key = (
        f"od_writeback:{practice_id}:{pat_num}:{check_id}"
        if check_id
        else f"od_writeback:{practice_id}:{request_id}"
    )
    pipeline_run_id = enqueue_opendental_writeback(
        app_settings,
        practice_id=practice_id,
        payload=wb_payload,
        idempotency_key=idempotency_key,
    )
    if pipeline_run_id:
        logger.info(
            "queued OD writeback request_id=%s pat_num=%s pipeline_run_id=%s",
            request_id,
            pat_num,
            pipeline_run_id,
        )
        return {"queued": True, "pipeline_run_id": str(pipeline_run_id)}
    return None
