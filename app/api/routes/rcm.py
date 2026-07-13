"""
Prior authorization + end-to-end RCM pipeline routes (synchronous).
"""

import contextlib
import json
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.claim_agent import submit_reviewed_claim
from app.agents.denial_agent import run_denial_agent
from app.agents.prior_auth_agent import run_prior_auth_agent
from app.agents.rcm_pipeline import run_full_rcm_pipeline, run_rcm_pipeline
from app.api.errors import sanitized_http_exception
from app.api.tenancy import PracticeContextDep
from app.audit.writer import write_audit_log
from app.config import get_settings
from app.integrations.agent_runs import (
    AGENT_PRIOR_AUTH,
    AgentRunNotFoundError,
    AgentRunTransitionError,
    list_agent_runs_for_patient,
    update_agent_run_status,
)
from app.integrations.supabase_client import create_supabase
from app.pipeline import (
    RUN_TYPE_FULL_RCM_PIPELINE,
    PipelineNotConfiguredError,
    create_pipeline_run,
    get_pipeline_run,
    serialize_pipeline_run,
)
from app.rcm.claims_store import CLAIM_STATUS_SUBMITTED, update_claim_status
from app.rcm.submit_gating import assert_claim_submission_allowed
from app.schemas.claim import (
    ClaimDraftSubmitRequest,
    ClaimSubmissionResponse,
    FullRcmPipelineRequest,
    FullRcmPipelineResponse,
)
from app.schemas.coding import CodingAgentRequest
from app.schemas.denial import DenialAgentRequest, DenialAgentResponse
from app.schemas.prior_auth import (
    PriorAuthAgentRequest,
    PriorAuthAgentResponse,
    RcmPipelineResponse,
)
from app.workflow.rcm_tasks import create_hitl_task_from_denial

router = APIRouter(prefix="/agents", tags=["rcm"])

_RESOLVE_PRIOR_AUTH_ROLES = frozenset({"admin", "billing_lead"})


class ResolvePriorAuthRunRequest(BaseModel):
    status: Literal["approved", "denied", "expired", "superseded"]
    reason: str | None = Field(default=None, max_length=2000)


class PipelineJobResponse(BaseModel):
    run_id: str
    status: str
    run_type: str = RUN_TYPE_FULL_RCM_PIPELINE


class PipelineJobStatusResponse(BaseModel):
    run_id: str
    status: str
    run_type: str
    result: dict[str, Any] | None = None
    error_message: str | None = None
    error_code: str | None = None


def _require_prior_auth_resolve_role(tenant: PracticeContextDep) -> None:
    if tenant.role not in _RESOLVE_PRIOR_AUTH_ROLES:
        raise HTTPException(status_code=403, detail="role_forbidden")


_LLM_NON_JSON_MSG = "LLM returned a non-JSON response"
_LLM_REQUEST_MSG = "LLM request failed"
_DB_UNAVAILABLE_MSG = "Database is unavailable"


@router.get("/prior-auth/runs/{patient_id}", tags=["prior-auth"])
def list_prior_auth_runs_for_patient(patient_id: UUID, tenant: PracticeContextDep) -> dict:
    """Recent persisted prior-auth assessments for a patient (from `agent_runs`)."""
    settings = get_settings()
    rows = list_agent_runs_for_patient(
        settings,
        patient_id,
        practice_id=tenant.practice_id,
        agent=AGENT_PRIOR_AUTH,
    )
    return {"patient_id": str(patient_id), "practice_id": tenant.practice_id, "runs": rows}


@router.post("/prior-auth/runs/{run_id}/resolve", tags=["prior-auth"])
def resolve_prior_auth_run(
    run_id: UUID,
    body: ResolvePriorAuthRunRequest,
    tenant: PracticeContextDep,
) -> dict[str, Any]:
    """Resolve a pending prior-auth run (billing lead or admin only)."""
    _require_prior_auth_resolve_role(tenant)
    settings = get_settings()
    meta_patch: dict[str, Any] | None = None
    if body.reason:
        meta_patch = {"resolve_reason": body.reason.strip()}
    try:
        row = update_agent_run_status(
            settings,
            run_id,
            body.status,
            practice_id=tenant.practice_id,
            meta_patch=meta_patch,
        )
    except AgentRunNotFoundError as e:
        raise HTTPException(status_code=404, detail="Agent run not found") from e
    except AgentRunTransitionError as e:
        raise HTTPException(status_code=409, detail="invalid_transition") from e
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="resolve_prior_auth_run database failure",
            exc=e,
        ) from e
    return {
        "message": "Agent run resolved successfully",
        "run_id": str(run_id),
        "status": body.status,
        "run": row,
    }


@router.post(
    "/prior-auth/run",
    response_model=PriorAuthAgentResponse,
    tags=["prior-auth"],
)
def run_prior_auth_endpoint(
    body: PriorAuthAgentRequest,
    tenant: PracticeContextDep,
) -> PriorAuthAgentResponse:
    """
    Run prior authorization on an existing Coding Agent response (+ insurance / note).
    """
    settings = get_settings()
    body = body.model_copy(update={"practice_id": tenant.practice_id})
    try:
        return run_prior_auth_agent(settings, body)
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="run_prior_auth_agent runtime failure",
            exc=e,
        ) from e
    except json.JSONDecodeError as e:
        raise sanitized_http_exception(
            502,
            public_message=_LLM_NON_JSON_MSG,
            log_message="run_prior_auth_agent JSON decode failure",
            exc=e,
        ) from e
    except httpx.HTTPError as e:
        raise sanitized_http_exception(
            502,
            public_message=_LLM_REQUEST_MSG,
            log_message="run_prior_auth_agent httpx failure",
            exc=e,
        ) from e


@router.post(
    "/denial/run",
    response_model=DenialAgentResponse,
    tags=["denial-era"],
)
def run_denial_agent_endpoint(
    body: DenialAgentRequest,
    tenant: PracticeContextDep,
) -> DenialAgentResponse:
    """Process a mock ERA / 835 response for a claim (no LLM)."""
    settings = get_settings()
    response = run_denial_agent(body)
    hitl_task_id = create_hitl_task_from_denial(
        settings,
        practice_id=tenant.practice_id,
        request=body.model_dump(mode="json"),
        response=response.model_dump(mode="json"),
    )
    if hitl_task_id:
        return response.model_copy(update={"hitl_task_id": hitl_task_id})
    return response


@router.post("/rcm/pipeline", response_model=RcmPipelineResponse, tags=["rcm-pipeline"])
def run_rcm_pipeline_endpoint(
    body: CodingAgentRequest,
    tenant: PracticeContextDep,
) -> RcmPipelineResponse:
    """
    Coding Agent → Prior Auth Agent in one call (same input as /agents/coding/run).
    """
    settings = get_settings()
    supabase = create_supabase(settings)
    body = body.model_copy(update={"practice_id": tenant.practice_id})
    try:
        return run_rcm_pipeline(settings, supabase, body)
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="run_rcm_pipeline runtime failure",
            exc=e,
        ) from e
    except json.JSONDecodeError as e:
        raise sanitized_http_exception(
            502,
            public_message=_LLM_NON_JSON_MSG,
            log_message="run_rcm_pipeline JSON decode failure",
            exc=e,
        ) from e
    except httpx.HTTPError as e:
        raise sanitized_http_exception(
            502,
            public_message=_LLM_REQUEST_MSG,
            log_message="run_rcm_pipeline httpx failure",
            exc=e,
        ) from e


@router.post(
    "/rcm/full-pipeline/jobs",
    response_model=PipelineJobResponse,
    tags=["rcm-full-pipeline"],
)
def enqueue_full_rcm_pipeline_job(
    body: FullRcmPipelineRequest,
    tenant: PracticeContextDep,
) -> PipelineJobResponse:
    """Enqueue async full RCM pipeline; poll GET .../jobs/{run_id} for result."""
    settings = get_settings()
    body = body.model_copy(update={"practice_id": tenant.practice_id})
    try:
        run_id = create_pipeline_run(
            settings,
            practice_id=tenant.practice_id,
            run_type=RUN_TYPE_FULL_RCM_PIPELINE,
            payload=body.model_dump(mode="json"),
        )
    except PipelineNotConfiguredError as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="pipeline queue requires NEON_DATABASE_URL",
            exc=e,
        ) from e
    except Exception as e:
        raise sanitized_http_exception(
            500,
            public_message="Failed to enqueue pipeline job",
            log_message="create_pipeline_run failure",
            exc=e,
        ) from e
    return PipelineJobResponse(
        run_id=str(run_id),
        status="queued",
        run_type=RUN_TYPE_FULL_RCM_PIPELINE,
    )


@router.get(
    "/rcm/full-pipeline/jobs/{run_id}",
    response_model=PipelineJobStatusResponse,
    tags=["rcm-full-pipeline"],
)
def get_full_rcm_pipeline_job(
    run_id: UUID, tenant: PracticeContextDep
) -> PipelineJobStatusResponse:
    settings = get_settings()
    try:
        row = get_pipeline_run(settings, run_id, practice_id=tenant.practice_id)
    except PipelineNotConfiguredError as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="pipeline queue requires NEON_DATABASE_URL",
            exc=e,
        ) from e
    if not row:
        raise HTTPException(status_code=404, detail="pipeline_run_not_found")
    serialized = serialize_pipeline_run(row)
    return PipelineJobStatusResponse(
        run_id=str(run_id),
        status=str(serialized.get("status") or "unknown"),
        run_type=str(serialized.get("run_type") or RUN_TYPE_FULL_RCM_PIPELINE),
        result=serialized.get("result") if isinstance(serialized.get("result"), dict) else None,
        error_message=serialized.get("error_message"),
        error_code=serialized.get("error_code"),
    )


@router.post(
    "/rcm/full-pipeline",
    response_model=FullRcmPipelineResponse,
    tags=["rcm-full-pipeline"],
)
def run_full_rcm_pipeline_endpoint(
    body: FullRcmPipelineRequest,
    tenant: PracticeContextDep,
) -> FullRcmPipelineResponse:
    """
    Coding → Prior Auth → Claim draft (for biller edit/submit).
    """
    settings = get_settings()
    supabase = create_supabase(settings)
    body = body.model_copy(update={"practice_id": tenant.practice_id})
    try:
        return run_full_rcm_pipeline(settings, supabase, body)
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="run_full_rcm_pipeline runtime failure",
            exc=e,
        ) from e
    except json.JSONDecodeError as e:
        raise sanitized_http_exception(
            502,
            public_message=_LLM_NON_JSON_MSG,
            log_message="run_full_rcm_pipeline JSON decode failure",
            exc=e,
        ) from e
    except httpx.HTTPError as e:
        raise sanitized_http_exception(
            502,
            public_message=_LLM_REQUEST_MSG,
            log_message="run_full_rcm_pipeline httpx failure",
            exc=e,
        ) from e


@router.post(
    "/claim/submit-draft",
    response_model=ClaimSubmissionResponse,
    tags=["claim-submission"],
)
def submit_claim_draft_endpoint(
    body: ClaimDraftSubmitRequest,
    tenant: PracticeContextDep,
) -> ClaimSubmissionResponse:
    """
    Submit a reviewed draft claim payload to the clearinghouse adapter.
    """
    settings = get_settings()
    try:
        assert_claim_submission_allowed(
            settings,
            practice_id=tenant.practice_id,
            claim_record_id=body.claim_record_id,
            hitl_task_id=body.task_id,
        )
        result = submit_reviewed_claim(body.claim_payload.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        raise sanitized_http_exception(
            400,
            public_message="Failed to submit claim draft",
            log_message="submit_reviewed_claim failure",
            exc=e,
        ) from e

    if body.claim_record_id:
        with contextlib.suppress(ValueError, TypeError):
            update_claim_status(
                settings,
                practice_id=tenant.practice_id,
                claim_id=UUID(str(body.claim_record_id)),
                status=CLAIM_STATUS_SUBMITTED,
            )

    write_audit_log(
        settings,
        practice_id=tenant.practice_id,
        action="claim.submitted",
        entity_type="claim",
        entity_id=UUID(str(body.claim_record_id)) if body.claim_record_id else None,
        performed_by=tenant.principal.subject,
        metadata={
            "hitl_task_id": body.task_id,
            "submission_status": result.status if hasattr(result, "status") else None,
        },
    )

    return result
