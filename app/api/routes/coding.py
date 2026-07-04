"""Dental coding routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.errors import sanitized_http_exception
from app.api.tenancy import PracticeContextDep
from app.config import get_settings
from app.services.decision_service import run_agent_for_encounter

router = APIRouter(tags=["dental-coding"])


class RunCodingAgentRequest(BaseModel):
    encounter_id: str


@router.post("/run-coding-agent")
def run_coding_agent_for_encounter(
    body: RunCodingAgentRequest,
    tenant: PracticeContextDep,
) -> dict:
    """
    Run coding agent against one encounter and persist a reviewable decision.
    """
    try:
        settings = get_settings()
        return run_agent_for_encounter(
            settings,
            body.encounter_id,
            practice_id=tenant.practice_id,
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message="Database is unavailable",
            log_message="run_agent_for_encounter Supabase failure",
            exc=e,
        ) from e
    except Exception as e:
        raise sanitized_http_exception(
            500,
            public_message="Failed to run coding agent",
            log_message="run_agent_for_encounter unexpected failure",
            exc=e,
        ) from e
