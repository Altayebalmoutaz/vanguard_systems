from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.coding import CodingAgentResponse


class PriorAuthAgentRequest(BaseModel):
    """Input: Coding Agent output plus insurance and optional note context."""

    coding: CodingAgentResponse
    insurance: str = Field(..., min_length=1)
    clinical_note: str | None = None
    patient_age: int | None = Field(default=None, ge=0, le=130)
    patient_id: UUID | None = None
    practice_id: str | None = None


class PriorAuthAgentResponse(BaseModel):
    """Prior authorization assessment; always pending human review."""

    requires_auth: bool = False
    required_documents: list[str] = Field(default_factory=list)
    payer_rules: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    risk_reason: str = ""
    status: Literal["pending_review"] = "pending_review"


class RcmPipelineResponse(BaseModel):
    """Full RCM slice: coding then prior authorization."""

    coding: CodingAgentResponse
    prior_auth: PriorAuthAgentResponse
