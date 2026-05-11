from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CodingAgentRequest(BaseModel):
    """HTTP input for the Dental Coding Agent."""

    clinical_note: str = Field(..., min_length=1, description="Free-text clinical note")
    patient_age: int = Field(..., ge=0, le=130)
    insurance: str = Field(..., min_length=1, description="e.g. Delta Dental PPO")
    patient_id: UUID | None = None
    practice_id: str | None = None


class CodingAgentResponse(BaseModel):
    """Structured coding output; always human-reviewable."""

    cdt_codes: list[str] = Field(default_factory=list)
    icd10_codes: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    justification: str = ""
    payer_flags: list[str] = Field(default_factory=list)
    payer_rules_matched: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["pending_review"] = "pending_review"
