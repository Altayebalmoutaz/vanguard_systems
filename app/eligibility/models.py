"""Layer 0 — Pydantic request/response models (schema + field validation)."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class TriggerEvent(str, Enum):
    NEW_PATIENT = "NEW_PATIENT"
    APPOINTMENT_BOOKED = "APPOINTMENT_BOOKED"
    PRE_APPOINTMENT = "PRE_APPOINTMENT"
    BATCH_SWEEP = "BATCH_SWEEP"


class Layer1ErrorCode(str, Enum):
    INVALID_PRIMARY_PAYER = "L1_INVALID_PRIMARY_PAYER"
    INVALID_SECONDARY_PAYER = "L1_INVALID_SECONDARY_PAYER"


class EligibilityRequest(BaseModel):
    """Inbound eligibility check request (Layer 0 schema)."""

    patient_id: UUID
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    dob: date
    subscriber_id: str = Field(..., min_length=1)
    primary_payer_id: str = Field(..., min_length=1, description="Stedi tradingPartnerServiceId")
    secondary_payer_id: str | None = None
    plan_id: str | None = None
    cdt_codes: list[str] | None = None
    trigger_event: TriggerEvent = TriggerEvent.APPOINTMENT_BOOKED
    ssn: str | None = None
    mbi: str | None = None
    portal_password: str | None = Field(
        default=None,
        description="Stedi portalPassword / payer PIN for payers that require portal credentials.",
    )
    patient_is_dependent: bool = False
    subscriber_first_name: str | None = None
    subscriber_last_name: str | None = None
    subscriber_dob: date | None = None
    subscriber_member_id: str | None = None
    dependent_member_id: str | None = None
    dependent_relationship_code: str | None = None

    practice_id: str | None = Field(
        default=None,
        description="Clinic tenant id; with rendering_provider_npi enables provider_payer_network fee-path lookup.",
        max_length=128,
    )
    rendering_provider_npi: str | None = Field(
        default=None,
        description="10-digit rendering dentist NPI for fee network / contract directory.",
        min_length=10,
        max_length=10,
    )
    provider_service_location_key: str | None = Field(
        default=None,
        description="Optional office/site key matching provider_payer_network (e.g. site_main).",
        max_length=256,
    )
    provider_first_name: str | None = Field(
        default=None,
        description="When set with provider_last_name, Stedi provider block uses person shape (firstName/lastName/npi) instead of organizationName.",
        max_length=120,
    )
    provider_last_name: str | None = Field(
        default=None,
        max_length=120,
    )
    stedi_provider_npi: str | None = Field(
        default=None,
        description="NPI in the Stedi provider object; defaults to PROVIDER_NPI from settings.",
        min_length=10,
        max_length=10,
    )
    provider_organization_name: str | None = Field(
        default=None,
        description="Overrides organizationName in Stedi provider block when not using person-provider fields; default is PROVIDER_NAME.",
        max_length=256,
    )

    @field_validator("stedi_provider_npi", mode="before")
    @classmethod
    def strip_stedi_provider_npi(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator("rendering_provider_npi", mode="before")
    @classmethod
    def strip_npi(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator("subscriber_id", "subscriber_member_id", "dependent_member_id", mode="before")
    @classmethod
    def strip_subscriber_id(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("primary_payer_id", "secondary_payer_id", mode="before")
    @classmethod
    def strip_payer_ids(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @model_validator(mode="after")
    def subscriber_non_null_after_strip(self) -> EligibilityRequest:
        if not (self.subscriber_id or "").strip():
            raise ValueError("subscriber_id must be non-null after stripping spaces")
        if self.patient_is_dependent:
            missing = []
            if not (self.subscriber_first_name or "").strip():
                missing.append("subscriber_first_name")
            if not (self.subscriber_last_name or "").strip():
                missing.append("subscriber_last_name")
            if self.subscriber_dob is None:
                missing.append("subscriber_dob")
            if missing:
                raise ValueError(
                    f"dependent eligibility requires subscriber policyholder fields: {', '.join(missing)}"
                )
        return self


class EligibilityBatchItem(BaseModel):
    """Single row for batch sweep (minimal subscriber + payer context)."""

    patient_id: UUID
    first_name: str
    last_name: str
    dob: date
    subscriber_id: str
    primary_payer_id: str
    cdt_codes: list[str] | None = None

    @field_validator("subscriber_id", mode="before")
    @classmethod
    def strip_sid(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


class EligibilityBatchRequest(BaseModel):
    """POST /eligibility/batch body."""

    items: list[EligibilityBatchItem] = Field(..., min_length=1)
    trigger_event: TriggerEvent = Field(default=TriggerEvent.BATCH_SWEEP)


class CobRequest(BaseModel):
    """POST /eligibility/cob — both primaries must be complete."""

    primary_eligibility_check_id: UUID
    secondary_eligibility_check_id: UUID


class RoutingDecision(BaseModel):
    status: str
    action: str
    next_agent: str | None = None
    notify_front_office: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class EligibilityCheckResponse(BaseModel):
    check_id: UUID
    patient_id: UUID
    payer_id: str
    routing: RoutingDecision
    canonical_summary: dict[str, Any] = Field(default_factory=dict)


class CachedEligibilityResponse(BaseModel):
    cached: bool = True
    record: dict[str, Any]


class BatchEligibilityResponse(BaseModel):
    batch_submitted: bool
    stedi_response: dict[str, Any] = Field(default_factory=dict)


class TwoPassEligibilityCodingRequest(BaseModel):
    """
    Two-pass orchestration input:
    pass 1 eligibility (no CDT) -> coding -> pass 2 eligibility (with CDT).
    """

    eligibility: EligibilityRequest
    clinical_note: str = Field(..., min_length=1)
    patient_age: int = Field(..., ge=0, le=130)
    insurance: str = Field(..., min_length=1)


class TwoPassEligibilityCodingResponse(BaseModel):
    pass1: dict[str, Any]
    coding: dict[str, Any] | None = None
    pass2: dict[str, Any] | None = None
    halted_after_pass1: bool = False
    halt_reason: str | None = None


class AuditLogEntry(BaseModel):
    id: UUID
    patient_id: UUID | None
    event_type: str | None
    detail: dict[str, Any] | None
    created_at: datetime | None


class StediAPIError(Exception):
    def __init__(
        self, message: str, status_code: int | None = None, body: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class Layer1ValidationError(ValueError):
    """Deterministic Layer-1 validation error with stable code and detail."""

    def __init__(
        self, code: Layer1ErrorCode, message: str, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}
