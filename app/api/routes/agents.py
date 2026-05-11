from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_agent_context
from app.api.errors import sanitized_http_exception
from app.api.schemas import AgentRunRequest, AgentRunResponse
from app.runtime.context import AgentContext
from app.runtime.executor import UnknownAgentError, run_agent

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/run", response_model=AgentRunResponse)
async def agents_run(
    body: AgentRunRequest,
    ctx: Annotated[AgentContext, Depends(get_agent_context)],
) -> AgentRunResponse:
    ctx.practice_id = body.practice_id or ctx.practice_id
    try:
        result = await run_agent(body.agent_id, body.payload, ctx)
        return AgentRunResponse(ok=True, result=result)
    except UnknownAgentError as e:
        # The agent id is caller-supplied (not PHI), so we surface it for UX.
        aid = e.args[0] if e.args else "unknown"
        raise HTTPException(status_code=404, detail=f"unknown_agent:{aid}") from e
    except Exception as e:
        raise sanitized_http_exception(
            500,
            public_message="Agent execution failed",
            log_message=f"agents_run failed for agent_id={body.agent_id!r}",
            exc=e,
        ) from e
