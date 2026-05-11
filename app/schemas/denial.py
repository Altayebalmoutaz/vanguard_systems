from typing import Literal

from pydantic import BaseModel, Field


class MockEraResponse(BaseModel):
    """Simulated 835 / ERA slice from payer (replace with real parser later)."""

    status: Literal["paid", "denied", "partial"] = "paid"
    reason: str = ""


class DenialAgentRequest(BaseModel):
    """Inputs for ERA / denial processing (matches pipeline + standalone API)."""

    claim_id: str = ""
    cdt_codes: list[str] = Field(default_factory=list)
    icd10_codes: list[str] = Field(default_factory=list)
    patient_name: str | None = None
    mock_era: MockEraResponse = Field(default_factory=MockEraResponse)
    # Optional context for appeal letter / correspondence
    insurance_company_name: str | None = None
    provider_name: str | None = None


class DenialAgentResponse(BaseModel):
    """Structured denial workflow output (JSON-serializable)."""

    claim_id: str = ""
    status: Literal["paid", "denied", "partial"] = "paid"
    reason: str = ""
    next_action: str = ""
    appeal_letter: str = ""
    resubmission_steps: list[str] = Field(
        default_factory=list,
        description="Checklist auto-generated from next_action (empty when paid / none).",
    )
    llm_reason_token: str = ""
    deterministic_reason_token: str = ""
    llm_confidence: float = 0.0
    reasoning_summary: str = ""
    required_evidence: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
