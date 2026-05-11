from typing import Any

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    agent_id: str = Field(..., description="Registered agent workflow id")
    payload: dict[str, Any] = Field(default_factory=dict)
    practice_id: str | None = None


class AgentRunResponse(BaseModel):
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class LegacyRunAgentRequest(BaseModel):
    """Backward-compatible body for POST /run-agent."""

    agent: str | None = None
    patient_id: str | None = None
    cpt_code: str | None = None
    practice_id: str | None = None
