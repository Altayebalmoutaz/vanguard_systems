"""
Prior authorization + end-to-end RCM pipeline routes (synchronous).
"""

import json
from uuid import UUID

import httpx
from fastapi import APIRouter

from app.agents.claim_agent import submit_reviewed_claim
from app.agents.denial_agent import run_denial_agent
from app.agents.prior_auth_agent import run_prior_auth_agent
from app.agents.rcm_pipeline import run_full_rcm_pipeline, run_rcm_pipeline
from app.api.errors import sanitized_http_exception
from app.config import get_settings
from app.integrations.agent_runs import AGENT_PRIOR_AUTH, list_agent_runs_for_patient
from app.integrations.supabase_client import create_supabase
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

router = APIRouter(prefix="/agents", tags=["rcm"])


_LLM_NON_JSON_MSG = "LLM returned a non-JSON response"
_LLM_REQUEST_MSG = "LLM request failed"
_DB_UNAVAILABLE_MSG = "Database is unavailable"


@router.get("/prior-auth/runs/{patient_id}", tags=["prior-auth"])
def list_prior_auth_runs_for_patient(patient_id: UUID) -> dict:
    """Recent persisted prior-auth assessments for a patient (from `agent_runs`)."""
    settings = get_settings()
    try:
        supabase = create_supabase(settings)
    except Exception as e:
        raise sanitized_http_exception(
            503,
            public_message=_DB_UNAVAILABLE_MSG,
            log_message="create_supabase failed in list_prior_auth_runs_for_patient",
            exc=e,
        ) from e
    rows = list_agent_runs_for_patient(supabase, patient_id, agent=AGENT_PRIOR_AUTH)
    return {"patient_id": str(patient_id), "runs": rows}


@router.post(
    "/prior-auth/run",
    response_model=PriorAuthAgentResponse,
    tags=["prior-auth"],
)
def run_prior_auth_endpoint(body: PriorAuthAgentRequest) -> PriorAuthAgentResponse:
    """
    Run prior authorization on an existing Coding Agent response (+ insurance / note).
    """
    settings = get_settings()
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
def run_denial_agent_endpoint(body: DenialAgentRequest) -> DenialAgentResponse:
    """Process a mock ERA / 835 response for a claim (no LLM)."""
    return run_denial_agent(body)


@router.post("/rcm/pipeline", response_model=RcmPipelineResponse, tags=["rcm-pipeline"])
def run_rcm_pipeline_endpoint(body: CodingAgentRequest) -> RcmPipelineResponse:
    """
    Coding Agent → Prior Auth Agent in one call (same input as /agents/coding/run).
    """
    settings = get_settings()
    supabase = create_supabase(settings)
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
    "/rcm/full-pipeline",
    response_model=FullRcmPipelineResponse,
    tags=["rcm-full-pipeline"],
)
def run_full_rcm_pipeline_endpoint(body: FullRcmPipelineRequest) -> FullRcmPipelineResponse:
    """
    Coding → Prior Auth → Claim draft (for biller edit/submit).
    """
    settings = get_settings()
    supabase = create_supabase(settings)
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
def submit_claim_draft_endpoint(body: ClaimDraftSubmitRequest) -> ClaimSubmissionResponse:
    """
    Submit a reviewed draft claim payload to the clearinghouse adapter.
    """
    try:
        return submit_reviewed_claim(body.claim_payload.model_dump())
    except Exception as e:
        raise sanitized_http_exception(
            400,
            public_message="Failed to submit claim draft",
            log_message="submit_reviewed_claim failure",
            exc=e,
        ) from e
