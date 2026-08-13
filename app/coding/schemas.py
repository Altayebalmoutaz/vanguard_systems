"""Versioned JSON contract for the scribe → coding agent handoff."""

from __future__ import annotations

import re
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


class Quadrant(str, Enum):
    """ADA quadrant for per-quad CDT families (SRP, irrigation, perio surgery)."""

    UR = "UR"
    UL = "UL"
    LR = "LR"
    LL = "LL"


class Arch(str, Enum):
    maxillary = "maxillary"
    mandibular = "mandibular"
    full_mouth = "full_mouth"


_QUADRANT_ALIASES = {
    "ur": Quadrant.UR,
    "ul": Quadrant.UL,
    "lr": Quadrant.LR,
    "ll": Quadrant.LL,
    "urq": Quadrant.UR,
    "ulq": Quadrant.UL,
    "lrq": Quadrant.LR,
    "llq": Quadrant.LL,
    "upper right": Quadrant.UR,
    "upper left": Quadrant.UL,
    "lower right": Quadrant.LR,
    "lower left": Quadrant.LL,
    "maxillary right": Quadrant.UR,
    "maxillary left": Quadrant.UL,
    "mandibular right": Quadrant.LR,
    "mandibular left": Quadrant.LL,
}
_ARCH_ALIASES = {
    "maxillary": Arch.maxillary,
    "maxilla": Arch.maxillary,
    "upper": Arch.maxillary,
    "upper arch": Arch.maxillary,
    "mandibular": Arch.mandibular,
    "mandible": Arch.mandibular,
    "lower": Arch.mandibular,
    "lower arch": Arch.mandibular,
    "full_mouth": Arch.full_mouth,
    "full mouth": Arch.full_mouth,
    "both": Arch.full_mouth,
    "both arches": Arch.full_mouth,
}
_QUAD_TOKEN_RE = re.compile(r"quadrant\s*:\s*(UR|UL|LR|LL)\b", re.IGNORECASE)
_ARCH_TOKEN_RE = re.compile(
    r"\barch\s*:\s*(maxillary|mandibular|full[_\s-]?mouth)\b", re.IGNORECASE
)


def _norm_alias_key(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def coerce_quadrant(value: object) -> Quadrant | None:
    if value is None or value == "":
        return None
    if isinstance(value, Quadrant):
        return value
    key = _norm_alias_key(value)
    mapped = _QUADRANT_ALIASES.get(key) or _QUADRANT_ALIASES.get(key.replace(" ", ""))
    if mapped is None:
        raise ValueError("quadrant must be UR, UL, LR, or LL")
    return mapped


def coerce_arch(value: object) -> Arch | None:
    if value is None or value == "":
        return None
    if isinstance(value, Arch):
        return value
    key = _norm_alias_key(value)
    mapped = _ARCH_ALIASES.get(key) or _ARCH_ALIASES.get(key.replace(" ", "_"))
    if mapped is None:
        raise ValueError("arch must be maxillary, mandibular, or full_mouth")
    return mapped


def try_coerce_quadrant(value: object) -> Quadrant | None:
    try:
        return coerce_quadrant(value)
    except ValueError:
        return None


def try_coerce_arch(value: object) -> Arch | None:
    try:
        return coerce_arch(value)
    except ValueError:
        return None


def resolved_quadrant(line: ProcedureLine) -> Quadrant | None:
    """Prefer the structured field; fall back to a findings token like ``quadrant: UR``."""
    if line.quadrant is not None:
        return line.quadrant
    for finding in line.findings:
        match = _QUAD_TOKEN_RE.search(finding)
        if match:
            return Quadrant(match.group(1).upper())
        mapped = try_coerce_quadrant(finding)
        if mapped is not None:
            return mapped
    return None


def resolved_arch(line: ProcedureLine) -> Arch | None:
    if line.arch is not None:
        return line.arch
    for finding in line.findings:
        match = _ARCH_TOKEN_RE.search(finding)
        if match:
            return try_coerce_arch(match.group(1))
        mapped = try_coerce_arch(finding)
        if mapped is not None:
            return mapped
    return None


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
    quadrant: Quadrant | None = Field(
        default=None,
        description="UR, UL, LR, or LL. Additive; omit when not a per-quadrant procedure.",
    )
    arch: Arch | None = Field(
        default=None,
        description="maxillary, mandibular, or full_mouth. Additive; omit when not applicable.",
    )

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

    @field_validator("quadrant", mode="before")
    @classmethod
    def _coerce_quadrant(cls, value: object) -> Quadrant | None:
        return coerce_quadrant(value)

    @field_validator("arch", mode="before")
    @classmethod
    def _coerce_arch(cls, value: object) -> Arch | None:
        return coerce_arch(value)


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


class AutonomyTier(str, Enum):
    """How much the scribe UI can trust this line without dentist scrutiny."""

    auto = "auto"  # calibrated-confident, valid, no blocking gaps, allowlisted/low-stakes
    review = "review"  # default: show for a quick dentist confirm
    ask = "ask"  # no CDT, blocking gap, invalid, or low confidence — dentist must confirm


class LineRecommendation(BaseModel):
    line_id: str
    cdt_code: str | None = None
    cdt_description: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    explanation: str = ""
    icd10_codes: list[str] = Field(default_factory=list)
    required_supporting_documentation: list[str] = Field(default_factory=list)
    missing_info: list[MissingInfoItem] = Field(default_factory=list)
    autonomy: AutonomyTier = AutonomyTier.review


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


class DecisionAction(str, Enum):
    """What the dentist did with a suggested line, for ground-truth capture."""

    approved = "approved"  # accepted the suggested CDT unchanged
    edited = "edited"  # changed the CDT to a different code
    rejected = "rejected"  # removed the line / no code billed
    added = "added"  # dentist added a line the agent did not suggest


class CodingDecisionLine(BaseModel):
    line_id: str = Field(..., min_length=1, max_length=64)
    action: DecisionAction
    suggested_cdt: str | None = None
    final_cdt: str | None = None
    edit_reason: str | None = Field(default=None, max_length=500)

    @field_validator("suggested_cdt", "final_cdt", mode="before")
    @classmethod
    def _norm_code(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).upper().strip()
        return text or None


class CodingDecisionRequest(BaseModel):
    """Dentist approve/edit/reject decisions for a prior suggest run (ground truth)."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    practice_id: str = Field(..., min_length=1, max_length=128)
    coding_run_id: UUID
    request_id: UUID | None = None
    decided_by: str | None = Field(default=None, max_length=128)
    decisions: list[CodingDecisionLine] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _unique_line_ids(self) -> CodingDecisionRequest:
        ids = [d.line_id for d in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("decisions[].line_id values must be unique")
        return self


class CodingDecisionResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    coding_run_id: UUID
    recorded: int = 0
    status: Literal["recorded"] = "recorded"
