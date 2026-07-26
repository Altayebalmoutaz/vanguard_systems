"""Dashboard BFF routes — Postgres-backed reads/writes for the Next.js UI."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.api.errors import sanitized_http_exception
from app.api.tenancy import PracticeContextDep
from app.config import get_settings
from app.dashboard.rcm_store import (
    get_dashboard_analytics,
    get_dashboard_overview,
    list_claim_cases,
    list_coding_cases,
    list_denial_cases,
    list_prior_auth_cases,
)
from app.dashboard.store import (
    DashboardHitlTaskConflictError,
    DashboardHitlTaskNotFoundError,
    DashboardPatientNotFoundError,
    DashboardRequestNotFoundError,
    create_eligibility_request,
    get_eligibility_agent_settings_row,
    get_hitl_task,
    get_patient_360,
    list_eligibility_activity,
    list_eligibility_queue,
    list_eligibility_request_events,
    list_hitl_tasks,
    list_procedure_estimates_for_request,
    resolve_hitl_task,
    update_eligibility_agent_settings,
)
from app.db.connection import NeonNotConfiguredError
from app.pilot.shadow_store import get_shadow_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_DB_UNAVAILABLE_MSG = "Database is unavailable"
_RESOLVE_HITL_ROLES = frozenset({"admin", "billing_lead"})


class CreateEligibilityRequestBody(BaseModel):
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str = Field(min_length=1, max_length=200)
    dob: str = Field(min_length=1, max_length=32)
    subscriber_id: str = Field(min_length=1, max_length=200)
    primary_payer_id: str = Field(min_length=1, max_length=200)
    secondary_payer_id: str | None = Field(default=None, max_length=200)
    plan_id: str | None = Field(default=None, max_length=200)
    cdt_codes: list[str] = Field(default_factory=list)
    trigger_event: Literal[
        "NEW_PATIENT",
        "APPOINTMENT_BOOKED",
        "PRE_APPOINTMENT",
        "BATCH_SWEEP",
    ] = "APPOINTMENT_BOOKED"
    priority: Literal["low", "medium", "high"] = "medium"
    appointment_date: str | None = None
    appointment_time: str | None = None
    provider_name: str | None = Field(default=None, max_length=200)
    estimated_claim_value: float | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)
    patient_id: UUID | None = None
    input_json: dict[str, Any] = Field(default_factory=dict)


class ResolveHitlTaskBody(BaseModel):
    action: Literal["approve", "reject", "override"]
    actor_label: str = Field(default="dashboard_staff", max_length=200)
    reason: str | None = Field(default=None, max_length=2000)
    final_codes: list[str] | None = None
    override_codes: list[str] | None = None
    final_summary: str | None = Field(default=None, max_length=4000)


def _require_hitl_resolve_role(tenant: PracticeContextDep) -> None:
    if tenant.role not in _RESOLVE_HITL_ROLES:
        raise HTTPException(status_code=403, detail="role_forbidden")


def _neon_unavailable(exc: BaseException) -> HTTPException:
    return sanitized_http_exception(
        503,
        public_message=_DB_UNAVAILABLE_MSG,
        log_message="dashboard route requires DATABASE_URL",
        exc=exc,
    )


def _db_failure(exc: BaseException, *, log_message: str) -> HTTPException:
    return sanitized_http_exception(
        503,
        public_message=_DB_UNAVAILABLE_MSG,
        log_message=log_message,
        exc=exc,
    )


@router.get("/eligibility/queue")
def get_eligibility_queue(tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        rows = list_eligibility_queue(settings, practice_id=tenant.practice_id, limit=75)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_eligibility_queue failure") from exc
    return {"rows": rows, "practice_id": tenant.practice_id}


@router.get("/eligibility/settings")
def get_eligibility_settings(tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        row = get_eligibility_agent_settings_row(settings, practice_id=tenant.practice_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="get_eligibility_agent_settings failure") from exc
    return {"settings": row, "practice_id": tenant.practice_id}


class UpdateEligibilitySettingsBody(BaseModel):
    voice_verification_enabled: bool | None = None
    voice_verification_auto_queue: bool | None = None
    auto_check_enabled: bool | None = None
    auto_retry_enabled: bool | None = None


@router.put("/eligibility/settings")
def put_eligibility_settings(
    body: UpdateEligibilitySettingsBody,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        row = update_eligibility_agent_settings(
            settings,
            practice_id=tenant.practice_id,
            updates=body.model_dump(exclude_none=True),
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="update_eligibility_agent_settings failure") from exc
    return {"settings": row, "practice_id": tenant.practice_id}


@router.post("/eligibility/requests/{request_id}/voice/queue")
def post_voice_queue_for_request(
    request_id: UUID,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    from app.eligibility.config import get_settings as get_eligibility_settings
    from app.eligibility.db_phi import fetch_eligibility_request_row
    from app.eligibility.models import EligibilityRequest, TriggerEvent
    from app.eligibility.voice.queue import queue_voice_verification

    app_settings = get_settings()
    row = fetch_eligibility_request_row(
        app_settings,
        practice_id=tenant.practice_id,
        request_id=request_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="eligibility_request_not_found")
    check_id = row.get("primary_check_id")
    if not check_id:
        raise HTTPException(status_code=422, detail="request_has_no_primary_check")

    output = row.get("output_json") or {}
    primary = output.get("primary") if isinstance(output, dict) else None
    if not isinstance(primary, dict):
        raise HTTPException(status_code=422, detail="request_missing_primary_output")

    er = EligibilityRequest(
        patient_id=row["patient_id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        dob=row["dob"],
        subscriber_id=row["subscriber_id"],
        primary_payer_id=row["primary_payer_id"],
        secondary_payer_id=row.get("secondary_payer_id"),
        plan_id=row.get("plan_id"),
        cdt_codes=list(row.get("cdt_codes") or []),
        trigger_event=TriggerEvent(row.get("trigger_event") or "PRE_APPOINTMENT"),
        practice_id=tenant.practice_id,
    )
    return queue_voice_verification(
        eligibility_check_id=check_id,
        patient_id=er.patient_id,
        payer_id=er.primary_payer_id,
        canonical=primary.get("canonical") or {},
        routing=primary.get("routing") or {},
        cdt_codes=list(er.cdt_codes or []),
        practice_id=tenant.practice_id,
        request_id=request_id,
        settings=get_eligibility_settings(),
        force=False,
    )


@router.get("/eligibility/requests/{request_id}/estimates")
def get_request_estimates(request_id: UUID, tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        estimates = list_procedure_estimates_for_request(
            settings,
            practice_id=tenant.practice_id,
            request_id=request_id,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except DashboardRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="eligibility_request_not_found") from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_procedure_estimates_for_request failure") from exc
    return {
        "request_id": str(request_id),
        "practice_id": tenant.practice_id,
        "estimates": estimates,
    }


@router.get("/eligibility/requests/{request_id}/events")
def get_request_events(request_id: UUID, tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        events = list_eligibility_request_events(
            settings,
            practice_id=tenant.practice_id,
            request_id=request_id,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except DashboardRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="eligibility_request_not_found") from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_eligibility_request_events failure") from exc
    return {
        "request_id": str(request_id),
        "practice_id": tenant.practice_id,
        "events": events,
    }


_SSE_HEARTBEAT_SECONDS = 15.0


@router.get("/eligibility/stream")
async def stream_eligibility_events(tenant: PracticeContextDep) -> StreamingResponse:
    """Server-Sent Events stream of eligibility/pipeline changes for the tenant.

    Emits small "something changed" events (source table, ids, status); clients
    refetch details through the regular BFF reads. Heartbeats every 15s keep
    proxies from closing the connection.
    """
    from app.realtime.bus import bus

    practice_id = tenant.practice_id

    async def event_source():
        yield f'event: ready\ndata: {{"practice_id": "{practice_id}"}}\n\n'
        subscription = bus.subscribe(practice_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        subscription.__anext__(), timeout=_SSE_HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                except StopAsyncIteration:
                    return
                seq = event.get("seq", "")
                yield f"id: {seq}\nevent: rcm\ndata: {json.dumps(event)}\n\n"
        finally:
            await subscription.aclose()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/eligibility/activity")
def get_eligibility_activity(
    tenant: PracticeContextDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        events = list_eligibility_activity(
            settings,
            practice_id=tenant.practice_id,
            limit=limit,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_eligibility_activity failure") from exc
    return {"practice_id": tenant.practice_id, "events": events}


@router.post("/eligibility/requests", status_code=201)
def post_eligibility_request(
    body: CreateEligibilityRequestBody,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    settings = get_settings()
    payload = body.model_dump(mode="json")
    payload["input_json"] = {
        **payload.get("input_json", {}),
        "submitted_from": "dashboard_bff",
    }
    try:
        row = create_eligibility_request(
            settings,
            practice_id=tenant.practice_id,
            payload=payload,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except ValueError as exc:
        if str(exc) == "idempotency_conflict":
            raise HTTPException(status_code=409, detail="idempotency_conflict") from exc
        raise HTTPException(status_code=400, detail="invalid_request") from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="create_eligibility_request failure") from exc
    return {
        "practice_id": tenant.practice_id,
        "request": row,
    }


@router.get("/hitl/tasks")
def get_hitl_tasks(
    tenant: PracticeContextDep,
    status: str = Query(default="pending"),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        tasks = list_hitl_tasks(
            settings,
            practice_id=tenant.practice_id,
            status=status,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_hitl_tasks failure") from exc
    return {"practice_id": tenant.practice_id, "status": status, "tasks": tasks}


@router.get("/hitl/tasks/{task_id}")
def get_hitl_task_detail(task_id: UUID, tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        task = get_hitl_task(settings, practice_id=tenant.practice_id, task_id=task_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except DashboardHitlTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="hitl_task_not_found") from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="get_hitl_task failure") from exc
    return {"practice_id": tenant.practice_id, "task": task}


@router.post("/hitl/tasks/{task_id}/resolve")
def post_hitl_task_resolve(
    task_id: UUID,
    body: ResolveHitlTaskBody,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    _require_hitl_resolve_role(tenant)
    settings = get_settings()
    performed_by = body.actor_label.strip() or tenant.principal.subject
    try:
        result = resolve_hitl_task(
            settings,
            practice_id=tenant.practice_id,
            task_id=task_id,
            action=body.action,
            performed_by=performed_by,
            final_codes=body.final_codes,
            final_summary=body.final_summary,
            override_codes=body.override_codes,
            reason=body.reason,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except DashboardHitlTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="hitl_task_not_found") from exc
    except DashboardHitlTaskConflictError as exc:
        raise HTTPException(status_code=409, detail="task_not_pending") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_action") from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="resolve_hitl_task failure") from exc
    return {
        "message": "Task resolved successfully",
        "practice_id": tenant.practice_id,
        **result,
    }


@router.get("/patients/{patient_id}")
def get_patient_profile(patient_id: UUID, tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        profile = get_patient_360(
            settings,
            practice_id=tenant.practice_id,
            patient_id=patient_id,
            performed_by=tenant.principal.subject,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except DashboardPatientNotFoundError as exc:
        raise HTTPException(status_code=404, detail="patient_not_found") from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="get_patient_360 failure") from exc
    return {"practice_id": tenant.practice_id, **profile}


@router.get("/overview")
def get_overview(tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        overview = get_dashboard_overview(settings, practice_id=tenant.practice_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="get_dashboard_overview failure") from exc
    return overview


@router.get("/analytics")
def get_analytics(tenant: PracticeContextDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        analytics = get_dashboard_analytics(settings, practice_id=tenant.practice_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="get_dashboard_analytics failure") from exc
    return analytics


@router.get("/pilot/shadow-summary")
def get_pilot_shadow_summary(
    tenant: PracticeContextDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Shadow pilot ROI metrics — eligibility volume and agent vs human accuracy."""
    settings = get_settings()
    try:
        summary = get_shadow_summary(
            settings,
            practice_id=tenant.practice_id,
            days=days,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="get_shadow_summary failure") from exc
    return {**summary, "shadow_mode_active": settings.pilot_shadow_mode}


@router.get("/coding/cases")
def get_coding_cases(
    tenant: PracticeContextDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=75, ge=1, le=200),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        cases = list_coding_cases(
            settings,
            practice_id=tenant.practice_id,
            status=status,
            limit=limit,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_coding_cases failure") from exc
    return {"practice_id": tenant.practice_id, "cases": cases}


@router.get("/prior-auth/cases")
def get_prior_auth_cases(
    tenant: PracticeContextDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=75, ge=1, le=200),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        cases = list_prior_auth_cases(
            settings,
            practice_id=tenant.practice_id,
            status=status,
            limit=limit,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_prior_auth_cases failure") from exc
    return {"practice_id": tenant.practice_id, "cases": cases}


@router.get("/claims/cases")
def get_claim_cases(
    tenant: PracticeContextDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=75, ge=1, le=200),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        cases = list_claim_cases(
            settings,
            practice_id=tenant.practice_id,
            status=status,
            limit=limit,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_claim_cases failure") from exc
    return {"practice_id": tenant.practice_id, "cases": cases}


@router.get("/denials/cases")
def get_denial_cases(
    tenant: PracticeContextDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=75, ge=1, le=200),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        cases = list_denial_cases(
            settings,
            practice_id=tenant.practice_id,
            status=status,
            limit=limit,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list_denial_cases failure") from exc
    return {"practice_id": tenant.practice_id, "cases": cases}


# ---------------------------------------------------------------------------
# OpenDental connections: control + visibility from the dashboard
# ---------------------------------------------------------------------------
class UpdateOpenDentalConnectionBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    customer_key_ref: str | None = Field(default=None, max_length=200)
    poll_enabled: bool | None = None
    poll_interval_seconds: float | None = Field(default=None, ge=5, le=86_400)
    poll_window_days: int | None = Field(default=None, ge=0, le=30)
    cdt_codes: str | None = Field(default=None, max_length=500)
    writeback_enabled: bool | None = None
    writeback_full: bool | None = None
    writeback_shadow_compare: bool | None = None


@router.get("/opendental/connections")
def get_opendental_connections(tenant: PracticeContextDep) -> dict[str, Any]:
    from app.integrations.opendental.connections_store import list_connections

    settings = get_settings()
    try:
        connections = list_connections(settings, practice_id=tenant.practice_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list opendental_connections failure") from exc
    return {"practice_id": tenant.practice_id, "connections": connections}


@router.put("/opendental/connections/{practice_id}")
def put_opendental_connection(
    practice_id: str,
    body: UpdateOpenDentalConnectionBody,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    _require_hitl_resolve_role(tenant)
    if practice_id != tenant.practice_id:
        raise HTTPException(status_code=403, detail="practice_forbidden")
    from app.integrations.opendental.connections_store import upsert_connection

    settings = get_settings()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no_fields_to_update")
    try:
        connection = upsert_connection(settings, practice_id=practice_id, updates=updates)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="upsert opendental_connection failure") from exc
    return {"connection": connection}


@router.get("/opendental/connections/{practice_id}/onboarding-key")
def get_opendental_onboarding_key(
    practice_id: str,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    """Return the clinic Customer Key for the Connect wizard copy step.

    Control roles only. The key is resolved from the env var named by
    ``customer_key_ref`` — never stored in list/SSE payloads.
    """
    _require_hitl_resolve_role(tenant)
    if practice_id != tenant.practice_id:
        raise HTTPException(status_code=403, detail="practice_forbidden")
    from app.audit.writer import write_audit_log
    from app.integrations.opendental.connections_store import get_connection, resolve_customer_key

    settings = get_settings()
    try:
        connection = get_connection(settings, practice_id=practice_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    if not connection:
        return {
            "configured": False,
            "customer_key": None,
            "message": "No OpenDental connection is provisioned for this clinic yet.",
        }
    key = resolve_customer_key(
        str(connection.get("customer_key_ref") or "") or None,
        fallback=None,
    )
    write_audit_log(
        settings,
        practice_id=practice_id,
        action="opendental.onboarding_key.viewed",
        entity_type="opendental_connection",
        entity_id=None,
        performed_by=tenant.role,
        metadata={"configured": bool(key)},
    )
    if not key:
        return {
            "configured": False,
            "customer_key": None,
            "message": "Your clinic key isn’t loaded yet. Contact your setup partner.",
        }
    return {"configured": True, "customer_key": key}


@router.post("/opendental/connections/{practice_id}/test")
def post_opendental_connection_test(
    practice_id: str,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    _require_hitl_resolve_role(tenant)
    if practice_id != tenant.practice_id:
        raise HTTPException(status_code=403, detail="practice_forbidden")
    from app.eligibility.config import get_settings as get_elig_settings
    from app.integrations.opendental.client import OpenDentalClient
    from app.integrations.opendental.connections_store import get_connection, record_health
    from app.integrations.opendental.errors import OpenDentalConfigError
    from app.integrations.opendental.onboarding_errors import friendly_opendental_test_error

    settings = get_settings()
    try:
        connection = get_connection(settings, practice_id=practice_id)
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    if not connection:
        raise HTTPException(status_code=404, detail="connection_not_found")
    try:
        client = OpenDentalClient.from_connection(connection, settings=get_elig_settings())
    except OpenDentalConfigError as exc:
        record_health(settings, practice_id=practice_id, healthy=False, error=str(exc))
        friendly = friendly_opendental_test_error(str(exc))
        return {"ok": False, "error": str(exc), "friendly": friendly}
    result = client.check_connection()
    record_health(
        settings,
        practice_id=practice_id,
        healthy=bool(result.get("ok")),
        error=None if result.get("ok") else str(result.get("error") or "connection test failed"),
    )
    if not result.get("ok"):
        result = {
            **result,
            "friendly": friendly_opendental_test_error(str(result.get("error") or "")),
        }
    return result


@router.post("/opendental/connections/{practice_id}/poll-now")
def post_opendental_poll_now(
    practice_id: str,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    _require_hitl_resolve_role(tenant)
    if practice_id != tenant.practice_id:
        raise HTTPException(status_code=403, detail="practice_forbidden")
    from app.integrations.opendental.connections_store import get_connection
    from app.pipeline.store import RUN_TYPE_OPENDENTAL_POLL, create_pipeline_run

    settings = get_settings()
    try:
        connection = get_connection(settings, practice_id=practice_id)
        if not connection:
            raise HTTPException(status_code=404, detail="connection_not_found")
        run_id = create_pipeline_run(
            settings,
            practice_id=practice_id,
            run_type=RUN_TYPE_OPENDENTAL_POLL,
            payload={"practice_id": practice_id, "trigger": "dashboard_poll_now"},
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="enqueue opendental poll failure") from exc
    return {"queued": True, "pipeline_run_id": str(run_id)}


@router.get("/opendental/runs")
def get_opendental_runs(
    tenant: PracticeContextDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    from app.pipeline.store import (
        RUN_TYPE_OPENDENTAL_POLL,
        RUN_TYPE_OPENDENTAL_WRITEBACK,
        list_pipeline_runs,
    )

    settings = get_settings()
    try:
        runs = list_pipeline_runs(
            settings,
            practice_id=tenant.practice_id,
            run_types=[RUN_TYPE_OPENDENTAL_POLL, RUN_TYPE_OPENDENTAL_WRITEBACK],
            limit=limit,
        )
    except NeonNotConfiguredError as exc:
        raise _neon_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _db_failure(exc, log_message="list opendental runs failure") from exc
    return {"practice_id": tenant.practice_id, "runs": runs}


@router.get("/opendental/writeback-review")
def get_opendental_writeback_review(
    tenant: PracticeContextDep,
    patient_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """Track C exception queue: review / fee / drift / reverify alerts for one patient."""
    from uuid import UUID

    from app.eligibility.config import get_settings as get_eligibility_settings
    from app.eligibility.db import get_supabase, list_audit_for_patient
    from app.integrations.opendental.review_queue import summarize_review_queue

    try:
        pid = UUID(str(patient_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_patient_id") from exc
    try:
        supabase = get_supabase(get_eligibility_settings())
        rows = list_audit_for_patient(supabase, pid)
    except Exception as exc:
        raise _db_failure(exc, log_message="opendental writeback review failure") from exc
    return {
        "practice_id": tenant.practice_id,
        "patient_id": str(pid),
        "queue": summarize_review_queue(rows),
    }
