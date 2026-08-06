"""Versioned JSON contract for the scribe → coding agent handoff."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


class MissingInfoCode(str, Enum):
    """Machine-readable gaps the scribe UI can prompt for."""

    TOOTH_MISSING = "TOOTH_MISSING"
    SURFACE_MISSING = "SURFACE_MISSING"
    FINDING_MISSING = "FINDING_MISSING"
    RADIOGRAPH_MISSING = "RADIOGRAPH_MISSING"
    PAYER_MISSING = "PAYER_MISSING"
    AGE_MISSING = "AGE_MISSING"
    PROCEDURE_EMPTY = "PROCEDURE_EMPTY"
    SUPPORTING_NOTE_THIN = "SUPPORTING_NOTE_THIN"
    CDT_UNCERTAIN = "CDT_UNCERTAIN"
    OTHER = "OTHER"


class PlannedOrPerformed(str, Enum):
    planned = "planned"
    performed = "performed"
    unknown = "unknown"


class PayerInfo(BaseModel):
    id: str | None = Field(default=None, description="Trading partner / Stedi payer id")
    name: str | None = Field(default=None, description="Display name, e.g. Delta Dental PPO")


class PatientInfo(BaseModel):
    age: int | None = Field(default=None, ge=0, le=130)


class ProcedureLine(BaseModel):
    line_id: str = Field(..., min_length=1, max_length=64)
    tooth_numbers: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    planned_or_performed: PlannedOrPerformed = PlannedOrPerformed.unknown

    @field_validator("tooth_numbers", "surfaces", "findings", mode="before")
    @classmethod
    def _coerce_str_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []


class CodingSuggestRequest(BaseModel):
    """Inbound structured payload from the scribe agent."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: UUID
    practice_id: str = Field(..., min_length=1, max_length=128)
    patient_id: str = Field(..., min_length=1, max_length=128)
    provider_id: str = Field(..., min_length=1, max_length=128)
    encounter_datetime: datetime
    payer: PayerInfo = Field(default_factory=PayerInfo)
    patient: PatientInfo = Field(default_factory=PatientInfo)
    procedures: list[ProcedureLine] = Field(default_factory=list, min_length=1)
    supporting_note: str | None = None
    attachments_present: list[str] = Field(default_factory=list)
    # When true, skip Jina/pgvector retrieval for lower latency.
    fast: bool = False

    @field_validator("attachments_present", mode="before")
    @classmethod
    def _coerce_attachments(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(v).strip().lower() for v in value if str(v).strip()]
        return []

    @model_validator(mode="after")
    def _unique_line_ids(self) -> CodingSuggestRequest:
        ids = [p.line_id for p in self.procedures]
        if len(ids) != len(set(ids)):
            raise ValueError("procedures[].line_id values must be unique")
        return self


class MissingInfoItem(BaseModel):
    code: MissingInfoCode
    message: str


class LineRecommendation(BaseModel):
    line_id: str
    cdt_code: str | None = None
    cdt_description: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    explanation: str = ""
    icd10_codes: list[str] = Field(default_factory=list)
    required_supporting_documentation: list[str] = Field(default_factory=list)
    missing_info: list[MissingInfoItem] = Field(default_factory=list)


class CodingSuggestResponse(BaseModel):
    """Outbound result for real-time dentist review in the scribe UI."""

    schema_version: str = SCHEMA_VERSION
    request_id: UUID
    coding_run_id: UUID | None = None
    status: Literal["pending_review", "needs_info"] = "pending_review"
    recommendations: list[LineRecommendation] = Field(default_factory=list)
    global_missing_info: list[MissingInfoItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(0.0, ge=0.0, le=1.0)
    idempotent_replay: bool = False
