"""UniversalDentalRecord v1 — semantic view derived from Layer 3 canonical (heuristic only)."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T")


class ConfidenceLevel(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class NormalizationMethod(str, Enum):
    HEURISTIC = "heuristic"
    LLM_FALLBACK = "llm_fallback"
    MANUAL_VERIFY = "manual_verify"


class NetworkStatus(str, Enum):
    IN_NETWORK = "in_network"
    OUT_OF_NETWORK = "out_of_network"
    UNKNOWN = "unknown"


class BenefitCategory(str, Enum):
    PREVENTIVE = "PREVENTIVE"
    DIAGNOSTIC = "DIAGNOSTIC"
    BASIC = "BASIC"
    MAJOR = "MAJOR"
    ORTHO = "ORTHO"
    PERIO = "PERIO"
    ENDO = "ENDO"
    MAXILLOFACIAL = "MAXILLOFACIAL"


class DataPoint(BaseModel, Generic[T]):
    """Wraps a value with confidence for audit and downstream rules."""

    value: T | None = None
    confidence: ConfidenceLevel
    source_field: str = Field(
        default="canonical", description="Origin hint; v1 is mostly canonical"
    )


class FinancialSummary(BaseModel):
    annual_max: DataPoint[float]
    annual_max_used: DataPoint[float]
    annual_max_remaining: DataPoint[float]
    deductible_total: DataPoint[float]
    deductible_met: DataPoint[float]
    deductible_remaining: DataPoint[float]
    ortho_lifetime_max: DataPoint[float]
    ortho_lifetime_used: DataPoint[float]


class CategoryBenefit(BaseModel):
    category: BenefitCategory
    covered: DataPoint[bool]
    coinsurance_patient_pct: DataPoint[float]  # 0–100 patient share, aligned with canonical


class OrthoDetail(BaseModel):
    eligible: DataPoint[bool]
    lifetime_max: DataPoint[float]
    age_cutoff: DataPoint[int]
    in_progress_treatment: DataPoint[bool]
    months_remaining: DataPoint[int]


class UniversalDentalRecord(BaseModel):
    """
    Semantic dental eligibility snapshot (v1).

    Built from existing :func:`~app.eligibility.normalizer.normalize` output — no second parser.
    Downstream may consume this for UI/reporting; Layer 4–6 still use flat ``canonical`` until migrated.
    """

    record_id: UUID
    stedi_payer_id: str
    payer_name: str | None = None
    subscriber_id: str | None = None
    plan_begin_date: date | None = None
    plan_end_date: date | None = None
    group_number: str | None = None
    network_status: NetworkStatus
    financial: FinancialSummary
    categories: list[CategoryBenefit] = Field(default_factory=list)
    ortho: OrthoDetail | None = None
    waiting_periods_present: bool = False
    limitation_notes: list[str] = Field(default_factory=list)
    normalization_method: NormalizationMethod = NormalizationMethod.HEURISTIC
    normalization_timestamp: datetime
    raw_payload_hash: str
    canonical_version: str = Field(
        default="1.0", description="Matches canonical normalization_version when present"
    )

    model_config = {"extra": "ignore"}


def data_point_float(
    value: float | None,
    *,
    confidence: ConfidenceLevel,
    source_field: str = "canonical",
) -> DataPoint[float]:
    return DataPoint[float](value=value, confidence=confidence, source_field=source_field)


def data_point_bool(
    value: bool | None,
    *,
    confidence: ConfidenceLevel,
    source_field: str = "canonical",
) -> DataPoint[bool]:
    return DataPoint[bool](value=value, confidence=confidence, source_field=source_field)


def data_point_int(
    value: int | None,
    *,
    confidence: ConfidenceLevel,
    source_field: str = "canonical",
) -> DataPoint[int]:
    return DataPoint[int](value=value, confidence=confidence, source_field=source_field)
