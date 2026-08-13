"""Execute claimed pipeline runs."""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any
from uuid import UUID

from app.agents.rcm_pipeline import run_full_rcm_pipeline
from app.audit.writer import write_audit_log
from app.config import Settings
from app.config import get_settings as get_app_settings
from app.eligibility.config import get_settings as get_eligibility_settings
from app.eligibility.request_processor import (
    EligibilityRequestSkipped,
    process_eligibility_request,
)
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.writeback import (
    run_opendental_writeback,
    writeback_has_failures,
    writeback_step_summary,
)
from app.integrations.supabase_client import create_supabase
from app.pipeline.gating import (
    create_hitl_task_from_pipeline,
    extract_coding_confidence,
    should_route_to_hitl,
)
from app.pipeline.store import (
    RUN_TYPE_ELIGIBILITY_REQUEST,
    RUN_TYPE_FULL_RCM_PIPELINE,
    RUN_TYPE_OPENDENTAL_POLL,
    RUN_TYPE_OPENDENTAL_WRITEBACK,
    complete_pipeline_run,
    fail_pipeline_run,
)
from app.rcm.claims_store import persist_claim_draft
from app.schemas.claim import FullRcmPipelineRequest

logger = logging.getLogger(__name__)


def execute_pipeline_run(settings: Settings, run: dict[str, Any]) -> None:
    """Run one claimed pipeline job and persist terminal status."""
    run_id = UUID(str(run["id"]))
    practice_id = str(run["practice_id"])
    run_type = str(run["run_type"])
    payload = run.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    locked_by = str(run.get("locked_by") or "pipeline_worker")

    write_audit_log(
        settings,
        practice_id=practice_id,
        action="pipeline.run.started",
        entity_type="pipeline_run",
        entity_id=run_id,
        performed_by=locked_by,
        metadata={"run_type": run_type},
    )

    try:
        confidence: float | None = None
        hitl_task_id: str | None = None

        if run_type == RUN_TYPE_FULL_RCM_PIPELINE:
            result = _execute_full_rcm_pipeline(settings, payload, practice_id=practice_id)
            if payload.get("clinical_note"):
                result["clinical_note"] = payload["clinical_note"]
            if payload.get("insurance"):
                result["insurance"] = payload["insurance"]
            claim_id = _persist_pipeline_claim_draft(
                settings,
                practice_id=practice_id,
                payload=payload,
                result=result,
            )
            if claim_id:
                result["claim_record_id"] = claim_id
            threshold = settings.confidence_hitl_threshold
            confidence = extract_coding_confidence(result)
            if should_route_to_hitl(confidence, threshold):
                hitl_task_id = create_hitl_task_from_pipeline(
                    settings,
                    practice_id=practice_id,
                    pipeline_run_id=str(run_id),
                    pipeline_result=result,
                    confidence=confidence,
                )
                result = {**result, "hitl_required": True, "hitl_task_id": hitl_task_id}
        elif run_type == RUN_TYPE_ELIGIBILITY_REQUEST:
            result = _execute_eligibility_request(
                settings,
                payload,
                practice_id=practice_id,
                locked_by=locked_by,
            )
        elif run_type == RUN_TYPE_OPENDENTAL_POLL:
            result = _execute_opendental_poll(settings, practice_id=practice_id)
        elif run_type == RUN_TYPE_OPENDENTAL_WRITEBACK:
            if settings.pilot_shadow_mode:
                result = {"skipped": True, "reason": "pilot_shadow_mode"}
            else:
                result = _execute_opendental_writeback(payload)
                # Per-step OD errors are isolated inside run_opendental_writeback
                # and recorded on the result dict. Do not fail/retry the pipeline
                # run — that discarded the payload and re-wrote notes 5 times.
                # Client construction / transport exceptions still raise and retry.
        else:
            raise ValueError(f"Unknown pipeline run_type: {run_type}")

        complete_pipeline_run(settings, run_id, practice_id=practice_id, result=result)
        write_audit_log(
            settings,
            practice_id=practice_id,
            action="pipeline.run.completed",
            entity_type="pipeline_run",
            entity_id=run_id,
            performed_by=locked_by,
            metadata={
                "run_type": run_type,
                "confidence": confidence,
                "hitl_task_id": hitl_task_id,
                "terminal_status": result.get("terminal_status"),
            },
        )
    except NotImplementedError as exc:
        fail_pipeline_run(
            settings,
            run_id,
            practice_id=practice_id,
            error_message=str(exc),
            error_code="not_implemented",
            retry=False,
        )
        write_audit_log(
            settings,
            practice_id=practice_id,
            action="pipeline.run.failed",
            entity_type="pipeline_run",
            entity_id=run_id,
            performed_by=locked_by,
            metadata={"error_code": "not_implemented"},
        )
    except Exception as exc:
        logger.exception("pipeline run %s failed", run_id)
        # Exception/traceback text can echo payload PHI; scrub before persisting.
        from app.security.phi import scrub_for_log

        safe_error = scrub_for_log(str(exc))
        safe_trace = scrub_for_log(traceback.format_exc()[-500:])
        fail_pipeline_run(
            settings,
            run_id,
            practice_id=practice_id,
            error_message=safe_error,
            error_code=type(exc).__name__,
            retry=True,
            retry_delay_seconds=settings.pipeline_retry_delay_seconds,
        )
        write_audit_log(
            settings,
            practice_id=practice_id,
            action="pipeline.run.failed",
            entity_type="pipeline_run",
            entity_id=run_id,
            performed_by=locked_by,
            metadata={"error": safe_error, "trace": safe_trace},
        )


def _execute_eligibility_request(
    settings: Settings,
    payload: dict[str, Any],
    *,
    practice_id: str,
    locked_by: str,
) -> dict[str, Any]:
    request_id_raw = payload.get("request_id") or payload.get("eligibility_request_id")
    if not request_id_raw:
        raise ValueError("eligibility_request payload requires request_id")
    request_id = UUID(str(request_id_raw))
    try:
        return process_eligibility_request(
            settings,
            practice_id=practice_id,
            request_id=request_id,
            locked_by=locked_by,
            payload=payload,
        )
    except EligibilityRequestSkipped as exc:
        return {"request_id": str(request_id), "skipped": True, "reason": str(exc)}


def _execute_opendental_poll(settings: Settings, *, practice_id: str) -> dict[str, Any]:
    """Dashboard 'Poll now': enqueue eligibility requests for today's OD appointments."""
    from app.integrations.opendental.connections_store import get_connection
    from app.integrations.opendental.poller import run_connection_poll

    connection = get_connection(settings, practice_id=practice_id)
    if not connection:
        raise ValueError(f"No OpenDental connection configured for practice {practice_id!r}")
    elig_settings = get_eligibility_settings()
    return run_connection_poll(elig_settings, settings, connection, for_auto_poll=False)


def _execute_opendental_writeback(payload: dict[str, Any]) -> dict[str, Any]:
    elig_settings = get_eligibility_settings()
    app_settings = get_app_settings()
    # Feature flag: when ODWB_SERVICE_URL is set, call the standalone service.
    # Default (unset) keeps the existing in-process path unchanged.
    if (app_settings.odwb_service_url or "").strip():
        return _execute_opendental_writeback_via_service(
            app_settings, elig_settings, payload
        )

    practice_id = str(payload.get("practice_id") or "").strip()
    if practice_id:
        from app.integrations.opendental.connections_store import get_connection

        connection = get_connection(app_settings, practice_id=practice_id)
        if connection:
            client = OpenDentalClient.from_connection(connection, settings=elig_settings)
        else:
            client = OpenDentalClient.from_settings(elig_settings)
    else:
        client = OpenDentalClient.from_settings(elig_settings)
    primary_result = payload.get("primary_result") or {}
    writeback_result = run_opendental_writeback(
        client,
        pat_num=int(payload["pat_num"]),
        primary_pat_plan_num=int(payload["primary_pat_plan_num"]),
        primary_plan_num=int(payload["primary_plan_num"]),
        primary_ins_sub_num=int(payload["primary_ins_sub_num"]),
        primary_result=primary_result,
        carrier_name=payload.get("carrier_name"),
        plan_name=payload.get("plan_name"),
        write_benefit_notes=bool(payload.get("write_benefit_notes", True)),
        write_subscriber_note=bool(payload.get("write_subscriber_note", True)),
        write_commlog=bool(payload.get("write_commlog", True)),
        write_inshist=bool(payload.get("write_inshist", False)),
        write_insadjust=bool(payload.get("write_insadjust", False)),
        write_benefits_grid=bool(payload.get("write_benefits_grid", False)),
        respect_manual_edits=bool(payload.get("respect_manual_edits", True)),
        dry_run_financial=bool(payload.get("dry_run_financial", False)),
        od_snapshot=payload.get("od_snapshot")
        if isinstance(payload.get("od_snapshot"), dict)
        else None,
        coverage_order=str(payload.get("coverage_order") or "primary"),
        check_id=payload.get("check_id"),
        patient_id=payload.get("patient_id"),
    )
    partial = writeback_has_failures(writeback_result)
    coverage_order = str(payload.get("coverage_order") or "primary")
    summary = {
        "status": "partial" if partial else "written",
        "partial_failure": partial,
        "coverage_order": coverage_order,
        "steps": writeback_step_summary(writeback_result),
    }
    request_id = payload.get("eligibility_request_id")
    if request_id and practice_id:
        try:
            from app.eligibility.db_phi import merge_eligibility_request_output_json

            merge_eligibility_request_output_json(
                app_settings,
                practice_id=practice_id,
                request_id=str(request_id),
                patch={"opendental_writeback": summary},
            )
        except Exception as exc:
            logger.warning("failed to stamp writeback summary on request %s: %s", request_id, exc)
    return {
        "write_back_result": writeback_result,
        "partial_failure": partial,
        "dry_run_financial": bool(payload.get("dry_run_financial", False)),
        "coverage_order": coverage_order,
        "opendental_writeback": summary,
    }


def _load_writeback_audit_rows(
    elig_settings: Any,
    *,
    patient_id: Any,
    practice_id: str,
) -> list[dict[str, Any]]:
    if not patient_id:
        return []
    try:
        from uuid import UUID

        from app.eligibility.db import get_supabase, list_audit_for_patient

        supabase = get_supabase(elig_settings)
        return list_audit_for_patient(
            supabase,
            UUID(str(patient_id)),
            practice_id=practice_id or None,
        )
    except Exception as exc:
        logger.warning("OpenDental audit load failed: %s", exc)
        return []


def _resolve_odwb_customer_key_ref(
    app_settings: Settings, *, practice_id: str
) -> str:
    """Map practice connection customer_key_ref for the OD writeback service allowlist."""
    if practice_id:
        from app.integrations.opendental.connections_store import get_connection

        connection = get_connection(app_settings, practice_id=practice_id)
        if connection:
            ref = str(connection.get("customer_key_ref") or "").strip()
            if ref:
                return ref
    return "OPENDENTAL_CUSTOMER_KEY"


def _execute_opendental_writeback_via_service(
    app_settings: Settings,
    elig_settings: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from app.eligibility.audit import write_audit_event
    from app.integrations.opendental.writeback_client import (
        call_opendental_writeback_service,
    )

    practice_id = str(payload.get("practice_id") or "").strip()
    patient_id = payload.get("patient_id")
    coverage_order = str(payload.get("coverage_order") or "primary")
    check_id = payload.get("check_id")
    pat_num = int(payload["pat_num"])
    idempotency_key = (
        f"od_writeback:{practice_id}:{pat_num}:{coverage_order}:{check_id}"
        if check_id
        else f"od_writeback:{practice_id}:{pat_num}:{coverage_order}"
    )
    audit_rows = _load_writeback_audit_rows(
        elig_settings, patient_id=patient_id, practice_id=practice_id
    )
    body = {
        "source_agent": "eligibility",
        "idempotency_key": idempotency_key,
        "od_target": {
            "customer_key_ref": _resolve_odwb_customer_key_ref(
                app_settings, practice_id=practice_id
            )
        },
        "command": {
            "type": "eligibility.apply_vob",
            "payload": {
                "pat_num": pat_num,
                "primary_pat_plan_num": int(payload["primary_pat_plan_num"]),
                "primary_plan_num": int(payload["primary_plan_num"]),
                "primary_ins_sub_num": int(payload["primary_ins_sub_num"]),
                "primary_result": payload.get("primary_result") or {},
                "carrier_name": payload.get("carrier_name"),
                "plan_name": payload.get("plan_name"),
                "write_benefit_notes": bool(payload.get("write_benefit_notes", True)),
                "write_subscriber_note": bool(payload.get("write_subscriber_note", True)),
                "write_commlog": bool(payload.get("write_commlog", True)),
                "write_inshist": bool(payload.get("write_inshist", False)),
                "write_insadjust": bool(payload.get("write_insadjust", False)),
                "write_benefits_grid": bool(payload.get("write_benefits_grid", False)),
                "respect_manual_edits": bool(payload.get("respect_manual_edits", True)),
                "dry_run_financial": bool(payload.get("dry_run_financial", False)),
                "od_snapshot": payload.get("od_snapshot")
                if isinstance(payload.get("od_snapshot"), dict)
                else None,
                "coverage_order": coverage_order,
                "check_id": check_id,
            },
        },
        "provenance": {"audit_rows": audit_rows},
        "config": {
            "confidence_gating": bool(
                elig_settings.opendental_write_benefits_grid_confidence_gating
            ),
            "respect_manual_edits": bool(payload.get("respect_manual_edits", True)),
        },
    }
    response = call_opendental_writeback_service(app_settings, body=body)
    for event in response.get("audit_events") or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        if not event_type or not patient_id:
            continue
        try:
            write_audit_event(
                patient_id=patient_id,
                event_type=str(event_type),
                detail=detail,
                practice_id=practice_id or None,
            )
        except Exception as exc:
            logger.warning("OD writeback audit replay failed: %s", exc)

    return {
        "write_back_result": response.get("write_back_result") or {},
        "partial_failure": bool(response.get("partial_failure")),
        "dry_run_financial": bool(payload.get("dry_run_financial", False)),
        "coverage_order": coverage_order,
    }


def _execute_full_rcm_pipeline(
    settings: Settings,
    payload: dict[str, Any],
    *,
    practice_id: str,
) -> dict[str, Any]:
    body = FullRcmPipelineRequest.model_validate({**payload, "practice_id": practice_id})
    supabase = create_supabase(settings)
    response = run_full_rcm_pipeline(settings, supabase, body)
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return dict(response)


def _persist_pipeline_claim_draft(
    settings: Settings,
    *,
    practice_id: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    coding = result.get("coding") if isinstance(result.get("coding"), dict) else {}
    prior_auth = result.get("prior_auth") if isinstance(result.get("prior_auth"), dict) else {}
    claim_draft = result.get("claim_draft") if isinstance(result.get("claim_draft"), dict) else {}
    if not claim_draft:
        return None

    patient_id_raw = payload.get("patient_id")
    patient_uuid = UUID(str(patient_id_raw)) if patient_id_raw else None
    provider_name = None
    billing = payload.get("billing")
    if isinstance(billing, dict):
        billing_provider = billing.get("billing_provider")
        if isinstance(billing_provider, dict):
            provider_name = billing_provider.get("name")

    return persist_claim_draft(
        settings,
        practice_id=practice_id,
        patient_id=patient_uuid,
        clinical_note=str(payload.get("clinical_note") or ""),
        provider=provider_name,
        coding=coding,
        prior_auth=prior_auth,
        claim_draft=claim_draft,
    )
