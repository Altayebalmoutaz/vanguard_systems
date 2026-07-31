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
    shadow = bool(input_json.get("writeback_shadow_compare"))
    if bool(input_json.get("writeback_full")):
        return {
            "write_benefit_notes": True,
            "write_subscriber_note": True,
            "write_commlog": True,
            # Shadow-compare still *runs* L3/L4/InsHist but in dry-run (see writeback dry_run flag).
            "write_inshist": True,
            "write_insadjust": True,
            "write_benefits_grid": True,
            "dry_run_financial": shadow,
        }
    return {
        "write_benefit_notes": True,
        "write_subscriber_note": True,
        "write_commlog": True,
        "write_inshist": False,
        "write_insadjust": False,
        "write_benefits_grid": False,
        "dry_run_financial": False,
    }


def _enqueue_one(
    app_settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
    row: dict[str, Any],
    input_json: dict[str, Any],
    plan_result: dict[str, Any],
    pat_num: int,
    pat_plan_num: int,
    plan_num: int,
    ins_sub_num: int,
    carrier_name: str | None,
    od_snapshot: dict[str, Any] | None,
    flags: dict[str, bool],
    respect_manual_edits: bool,
    coverage_order: str,
) -> dict[str, Any] | None:
    check_id = plan_result.get("check_id")
    wb_payload = build_opendental_writeback_payload(
        pat_num=int(pat_num),
        primary_pat_plan_num=int(pat_plan_num),
        primary_plan_num=int(plan_num),
        primary_ins_sub_num=int(ins_sub_num),
        primary_result=plan_result,
        carrier_name=carrier_name,
        check_id=check_id,
        patient_id=str(row.get("patient_id") or ""),
        write_benefit_notes=flags["write_benefit_notes"],
        write_subscriber_note=flags["write_subscriber_note"],
        write_commlog=flags["write_commlog"],
        write_inshist=flags["write_inshist"],
        write_insadjust=flags["write_insadjust"],
        write_benefits_grid=flags["write_benefits_grid"],
        respect_manual_edits=respect_manual_edits,
        dry_run_financial=flags.get("dry_run_financial", False),
        od_snapshot=od_snapshot,
        coverage_order=coverage_order,
    )
    wb_payload["practice_id"] = practice_id
    idempotency_key = (
        f"od_writeback:{practice_id}:{pat_num}:{coverage_order}:{check_id}"
        if check_id
        else f"od_writeback:{practice_id}:{request_id}:{coverage_order}"
    )
    pipeline_run_id = enqueue_opendental_writeback(
        app_settings,
        practice_id=practice_id,
        payload=wb_payload,
        idempotency_key=idempotency_key,
    )
    if pipeline_run_id:
        logger.info(
            "queued OD writeback request_id=%s pat_num=%s order=%s pipeline_run_id=%s dry_run=%s",
            request_id,
            pat_num,
            coverage_order,
            pipeline_run_id,
            flags.get("dry_run_financial"),
        )
        return {
            "queued": True,
            "pipeline_run_id": str(pipeline_run_id),
            "coverage_order": coverage_order,
            "dry_run_financial": bool(flags.get("dry_run_financial")),
        }
    return None


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

    # Prefer live connection flags (dashboard) over stale input_json snapshot.
    if connection.get("writeback_full") is not None:
        input_json = {
            **input_json,
            "writeback_full": bool(connection.get("writeback_full")),
            "writeback_shadow_compare": bool(connection.get("writeback_shadow_compare")),
        }

    pat_num = input_json.get("pat_num")
    primary = result.get("primary")
    if pat_num is None or not isinstance(primary, dict):
        return None

    flags = _writeback_flags(input_json)
    respect = elig_settings.opendental_write_benefits_grid_respect_manual_edits
    queued: list[dict[str, Any]] = []

    primary_queued = _enqueue_one(
        app_settings,
        practice_id=practice_id,
        request_id=request_id,
        row=row,
        input_json=input_json,
        plan_result=primary,
        pat_num=int(pat_num),
        pat_plan_num=int(input_json["primary_pat_plan_num"]),
        plan_num=int(input_json["primary_plan_num"]),
        ins_sub_num=int(input_json["primary_ins_sub_num"]),
        carrier_name=input_json.get("primary_carrier_name"),
        od_snapshot=input_json.get("primary_od_snapshot")
        if isinstance(input_json.get("primary_od_snapshot"), dict)
        else None,
        flags=flags,
        respect_manual_edits=respect,
        coverage_order="primary",
    )
    if primary_queued:
        queued.append(primary_queued)

    # Track D: secondary plan Layer 1–4 when OD Family Insurance + Stedi secondary exist.
    secondary = result.get("secondary")
    sec_pat_plan = input_json.get("secondary_pat_plan_num")
    sec_plan = input_json.get("secondary_plan_num")
    sec_ins_sub = input_json.get("secondary_ins_sub_num")
    if (
        isinstance(secondary, dict)
        and sec_pat_plan is not None
        and sec_plan is not None
        and sec_ins_sub is not None
        and flags.get("write_benefits_grid")
    ):
        secondary_queued = _enqueue_one(
            app_settings,
            practice_id=practice_id,
            request_id=request_id,
            row=row,
            input_json=input_json,
            plan_result=secondary,
            pat_num=int(pat_num),
            pat_plan_num=int(sec_pat_plan),
            plan_num=int(sec_plan),
            ins_sub_num=int(sec_ins_sub),
            carrier_name=input_json.get("secondary_carrier_name"),
            od_snapshot=input_json.get("secondary_od_snapshot")
            if isinstance(input_json.get("secondary_od_snapshot"), dict)
            else None,
            flags=flags,
            respect_manual_edits=respect,
            coverage_order="secondary",
        )
        if secondary_queued:
            queued.append(secondary_queued)

    if not queued:
        return None
    return {"queued": True, "runs": queued}
