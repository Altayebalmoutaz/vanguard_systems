from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.coding import CodingAgentResponse
from app.schemas.denial import DenialAgentResponse, MockEraResponse
from app.schemas.prior_auth import PriorAuthAgentResponse


class PatientInfo(BaseModel):
    name: str = Field(..., min_length=1)
    dob: str = Field(..., min_length=1, description="Date of birth as string, e.g. YYYY-MM-DD")


class ProviderInfo(BaseModel):
    name: str = Field(..., min_length=1)
    npi: str = Field(..., min_length=1, description="10-digit NPI typical for individuals")


class Address(BaseModel):
    line1: str = Field(..., min_length=1)
    city: str = Field(..., min_length=1)
    state: str = Field(..., min_length=2, max_length=2, description="US state abbreviation")
    postal_code: str = Field(..., min_length=5)


class SubscriberInfo(BaseModel):
    member_id: str = Field(..., min_length=1)
    relationship_to_patient: Literal["self", "spouse", "child", "other"] = "self"
    name: str = Field(..., min_length=1)
    dob: str = Field(..., min_length=1, description="Date of birth as string, e.g. YYYY-MM-DD")
    address: Address


class BillingProviderInfo(BaseModel):
    name: str = Field(..., min_length=1)
    npi: str = Field(..., min_length=1)
    tax_id: str = Field(..., min_length=1)
    taxonomy_code: str = Field(..., min_length=1)
    address: Address


class RenderingProviderInfo(BaseModel):
    name: str = Field(..., min_length=1)
    npi: str = Field(..., min_length=1)
    taxonomy_code: str = Field(..., min_length=1)


class PayerInfo(BaseModel):
    payer_name: str = Field(..., min_length=1)
    payer_id: str = Field(..., min_length=1, description="Payer identifier used by clearinghouse")
    plan_name: str = Field(..., min_length=1)


class ServiceLineInput(BaseModel):
    line_number: int = Field(..., ge=1)
    service_date: str = Field(
        ..., min_length=1, description="Date of service as string, e.g. YYYY-MM-DD"
    )
    cdt_code: str = Field(..., min_length=1)
    units: Decimal = Field(default=Decimal("1"), gt=0)
    charge_amount: Decimal = Field(..., gt=0)
    diagnosis_pointers: list[int] = Field(default_factory=lambda: [1], min_length=1)
    tooth_number: str | None = None
    surface: str | None = None
    prior_auth_number: str | None = None


class ClaimBillingInput(BaseModel):
    claim_frequency_code: Literal["1", "7", "8"] = Field(
        default="1",
        description="1 original, 7 replacement, 8 void",
    )
    place_of_service: str = Field(..., min_length=2, max_length=2)
    patient_account_number: str = Field(..., min_length=1)
    patient_sex: Literal["M", "F", "U"]
    patient_address: Address
    subscriber: SubscriberInfo
    billing_provider: BillingProviderInfo
    rendering_provider: RenderingProviderInfo
    payer: PayerInfo
    diagnosis_codes: list[str] = Field(..., min_length=1)
    service_lines: list[ServiceLineInput] = Field(..., min_length=1)
    total_charge_amount: Decimal = Field(..., gt=0)


class ClaimPatientBlock(BaseModel):
    name: str
    dob: str


class ClaimProviderBlock(BaseModel):
    name: str
    npi: str


class ClaimCodesBlock(BaseModel):
    cdt: list[str]
    icd10: list[str]


class ClaimSubscriberBlock(BaseModel):
    member_id: str
    relationship_to_patient: str
    name: str
    dob: str
    address: Address


class ClaimPayerBlock(BaseModel):
    payer_name: str
    payer_id: str
    plan_name: str


class ClaimServiceLineBlock(BaseModel):
    line_number: int
    service_date: str
    cdt_code: str
    units: Decimal
    charge_amount: Decimal
    diagnosis_pointers: list[int]
    tooth_number: str | None = None
    surface: str | None = None
    prior_auth_number: str | None = None


class ClaimStructure(BaseModel):
    """Simplified claim payload (837-like intent, not a full X12 segment map)."""

    patient: ClaimPatientBlock
    provider: ClaimProviderBlock
    subscriber: ClaimSubscriberBlock
    payer: ClaimPayerBlock
    billing_provider: BillingProviderInfo
    rendering_provider: RenderingProviderInfo
    patient_address: Address
    patient_sex: Literal["M", "F", "U"]
    claim_frequency_code: Literal["1", "7", "8"]
    place_of_service: str
    patient_account_number: str
    diagnosis_codes: list[str]
    service_lines: list[ClaimServiceLineBlock]
    total_charge_amount: Decimal
    codes: ClaimCodesBlock


class ClaimAgentRequest(BaseModel):
    """Everything the claim agent needs after coding + prior auth."""

    coding: CodingAgentResponse
    prior_auth: PriorAuthAgentResponse
    patient: PatientInfo
    provider: ProviderInfo
    billing: ClaimBillingInput


class ClaimSubmissionResponse(BaseModel):
    """Result of claim build + submit (or blocked prior to submit)."""

    claim_id: str = ""
    status: Literal["submitted", "pending_auth"] = "pending_auth"
    submission_channel: str = "none"
    details: dict[str, Any] = Field(default_factory=dict)


class ClaimDraftResponse(BaseModel):
    """Draft claim output for biller review before clearinghouse submit."""

    status: Literal["draft", "pending_auth"] = "draft"
    claim_payload: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    available_actions: list[Literal["edit", "submit"]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ClaimDraftSubmitRequest(BaseModel):
    """Biller-reviewed claim payload ready for submission."""

    claim_payload: ClaimStructure


class FullRcmPipelineRequest(BaseModel):
    """Single HTTP body: clinical context + demographics for the full RCM chain."""

    clinical_note: str = Field(..., min_length=1)
    patient_age: int = Field(..., ge=0, le=130)
    insurance: str = Field(..., min_length=1)
    patient_id: UUID | None = None
    practice_id: str | None = None
    encounter_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional front-desk encounter id for loading claim context snapshot",
    )
    patient: PatientInfo | None = None
    provider: ProviderInfo | None = None
    billing: ClaimBillingInput | None = None
    mock_era: MockEraResponse = Field(
        default_factory=MockEraResponse,
        description="Simulated payer 835 response after claim submit",
    )

    @model_validator(mode="after")
    def _require_direct_claim_context_or_encounter(self) -> "FullRcmPipelineRequest":
        if self.encounter_id:
            return self
        if self.patient is None or self.provider is None or self.billing is None:
            raise ValueError(
                "Provide either encounter_id (for snapshot lookup) or patient+provider+billing directly."
            )
        return self


class FullRcmPipelineResponse(BaseModel):
    """coding → prior_auth → claim draft (review/edit/submit)."""

    coding: CodingAgentResponse
    prior_auth: PriorAuthAgentResponse
    claim_draft: ClaimDraftResponse
    claim: ClaimSubmissionResponse | None = None
    denial: DenialAgentResponse | None = None
