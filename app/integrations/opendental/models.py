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

