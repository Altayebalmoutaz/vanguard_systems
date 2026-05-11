from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_agent_context
from app.api.schemas import AgentRunResponse, LegacyRunAgentRequest
from app.runtime.context import AgentContext
from app.runtime.executor import run_agent

router = APIRouter(tags=["legacy"])


@router.post("/run-agent", response_model=AgentRunResponse)
async def run_agent_legacy(
    body: LegacyRunAgentRequest,
    ctx: Annotated[AgentContext, Depends(get_agent_context)],
) -> AgentRunResponse:
    """Original endpoint shape from the starter app."""
    if body.agent != "prior_auth":
        raise HTTPException(status_code=404, detail="unknown_agent")
    payload = {
        "patient_id": body.patient_id,
        "cpt_code": body.cpt_code,
        "practice_id": body.practice_id,
    }
    ctx.practice_id = body.practice_id
    result = await run_agent("prior_auth", payload, ctx)
    return AgentRunResponse(ok=True, result=result)
