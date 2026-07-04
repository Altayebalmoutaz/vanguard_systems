"""Dashboard BFF routes — Neon-backed reads/writes for the Next.js UI."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

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
)
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
        log_message="dashboard route requires NEON_DATABASE_URL",
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
