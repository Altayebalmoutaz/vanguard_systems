"""Pydantic models for OpenDental REST payloads used by eligibility."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ODPatient(BaseModel):
    PatNum: int = Field(..., ge=1)
    FName: str
    LName: str
    Birthdate: date
    SSN: str | None = None


class ODInsuranceRow(BaseModel):
    PatPlanNum: int = Field(..., ge=1)
    InsSubNum: int = Field(..., ge=1)
    PlanNum: int = Field(..., ge=1)
    CarrierNum: int = Field(..., ge=1)
    CarrierName: str | None = None
    SubscriberID: str | None = None
    Ordinal: int | None = None


class ODCarrier(BaseModel):
    CarrierNum: int = Field(..., ge=1)
    CarrierName: str | None = None
    ElectID: str | None = None


class ODInsVerifyCreate(BaseModel):
    DateLastVerified: date
    VerifyType: Literal["PatientEnrollment", "InsuranceBenefit"] = "PatientEnrollment"
    FKey: int = Field(..., ge=1)
    DefNum: int | None = None
    Note: str | None = None


class ODInsVerifyResponse(BaseModel):
    InsVerifyNum: int = Field(..., ge=0)
    DateLastVerified: date | None = None
    UserNum: int | None = None
    VerifyType: str | None = None
    FKey: int | None = None
    DefNum: int | None = None
    Note: str | None = None
    DateLastAssigned: date | None = None
    SecDateTEdit: datetime | str | None = None


class ODInsSubBenefitNotesUpdate(BaseModel):
    """PUT /inssubs/{InsSubNum} - only PlanNum and BenefitNotes are honored by OD."""

    PlanNum: int = Field(..., ge=1)
    BenefitNotes: str


class ODInsSubSubscNoteUpdate(BaseModel):
    """PUT /inssubs/{InsSubNum} - SubscNote shows in bold red on the insurance grid."""

    PlanNum: int = Field(..., ge=1)
    SubscNote: str


class ODCommlogCreate(BaseModel):
    """POST /commlogs - automated eligibility summary for front-desk visibility."""

    PatNum: int = Field(..., ge=1)
    Note: str
    # OD field is literally "Mode_"; "None" means no contact mode (automated entry).
    Mode_: str = "None"
    SentOrReceived: str = "Neither"
    # definition.ItemName where definition.Category=27; "Insurance" is the built-in insurance type.
    commType: str = "Insurance"
    CommDateTime: str | None = None


class ODCommlogResponse(BaseModel):
    CommlogNum: int | None = None
    PatNum: int | None = None
    Note: str | None = None
    CommDateTime: str | None = None


class ODClaimProcInsAdjust(BaseModel):
    """PUT /claimprocs/InsAdjust - set insurance/deductible used totals (Phase 2)."""

    PatPlanNum: int = Field(..., ge=1)
    insUsed: str | None = None
    deductibleUsed: str | None = None
    date: str | None = None


class ODCovCat(BaseModel):
    """GET /covcats row - coverage category lookup (maps EbenefitCat -> CovCatNum)."""

    CovCatNum: int = Field(..., ge=0)
    Description: str | None = None
    DefaultPercent: int | None = None
    EbenefitCat: str | None = None
    IsHidden: str | bool | None = None


# OD BenefitType / CoverageLevel / TimePeriod string enums (subset the agent writes).
ODBenefitType = Literal[
    "ActiveCoverage",
    "CoInsurance",
    "Deductible",
    "CoPayment",
    "Exclusions",
    "Limitations",
    "WaitingPeriod",
]
ODCoverageLevel = Literal["None", "Individual", "Family"]


class ODBenefit(BaseModel):
    """GET /benefits row - a single structured benefit-grid entry."""

    BenefitNum: int = Field(..., ge=1)
    PlanNum: int | None = None
    PatPlanNum: int | None = None
    CovCatNum: int | None = None
    BenefitType: str | None = None
    Percent: int | None = None
    MonetaryAmt: float | None = None
    TimePeriod: str | None = None
    QuantityQualifier: str | None = None
    Quantity: int | None = None
    CodeNum: int | None = None
    procCode: str | None = None
    CoverageLevel: str | None = None
    CodeGroupNum: int | None = None
    TreatArea: str | None = None


class ODBenefitCreate(BaseModel):
    """POST /benefits - create a structured benefit row (CoInsurance %, Deductible/Limit $)."""

    BenefitType: ODBenefitType
    CoverageLevel: ODCoverageLevel = "None"
    PlanNum: int | None = Field(default=None, ge=1)
    PatPlanNum: int | None = Field(default=None, ge=1)
    CovCatNum: int | None = None
    Percent: int | None = None
    MonetaryAmt: float | None = None
    TimePeriod: str | None = None
    QuantityQualifier: str | None = None
    Quantity: int | None = None


class ODBenefitUpdate(BaseModel):
    """PUT /benefits/{BenefitNum} - update an existing benefit row's value fields."""

    Percent: int | None = None
    MonetaryAmt: float | None = None
    BenefitType: ODBenefitType | None = None
    CoverageLevel: ODCoverageLevel | None = None
    TimePeriod: str | None = None
