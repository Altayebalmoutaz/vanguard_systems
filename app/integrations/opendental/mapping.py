"""Mapping from OpenDental payloads to EligibilityRequest."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.eligibility.mock_clinic import DEFAULT_MOCK_PRACTICE_ID, DEFAULT_MOCK_RENDERING_NPI
from app.eligibility.models import EligibilityRequest, TriggerEvent
from app.integrations.opendental.errors import OpenDentalMappingError
from app.integrations.opendental.models import ODCarrier, ODInsuranceRow, ODPatient


@dataclass(frozen=True)
class MappedEligibility:
    """Result of mapping OD records -> EligibilityRequest plus write-back identifiers."""

    request: EligibilityRequest
    primary_pat_plan_num: int
    primary_plan_num: int
    primary_ins_sub_num: int
    primary_carrier_name: str | None = None
    secondary_pat_plan_num: int | None = None
    secondary_plan_num: int | None = None
    secondary_ins_sub_num: int | None = None
    secondary_carrier_name: str | None = None
    # OD-side plan metadata for read-only drift detection (Track G).
    primary_od_snapshot: dict | None = None
    secondary_od_snapshot: dict | None = None


def _pick_primary_row(rows: list[ODInsuranceRow]) -> ODInsuranceRow:
    for row in rows:
        if row.Ordinal == 1:
            return row
    return rows[0]


def _pick_secondary_row(
    rows: list[ODInsuranceRow], primary_row: ODInsuranceRow
) -> ODInsuranceRow | None:
    for row in rows:
        if row is primary_row:
            continue
        if row.Ordinal == 2:
            return row
    for row in rows:
        if row is not primary_row:
            return row
    return None


def _payer_id_for_row(row: ODInsuranceRow, carriers_by_num: dict[int, ODCarrier]) -> str:
    carrier = carriers_by_num.get(row.CarrierNum)
    if carrier is None:
        raise OpenDentalMappingError(f"Carrier {row.CarrierNum} was not loaded from OpenDental")
    payer_id = (carrier.ElectID or "").strip()
    if not payer_id:
        raise OpenDentalMappingError(f"Carrier {row.CarrierNum} has no ElectID")
    return payer_id


def _od_plan_snapshot(
    row: ODInsuranceRow,
    carriers_by_num: dict[int, ODCarrier],
    *,
    elect_id: str | None,
) -> dict:
    carrier = carriers_by_num.get(row.CarrierNum)
    return {
        "pat_plan_num": row.PatPlanNum,
        "plan_num": row.PlanNum,
        "ins_sub_num": row.InsSubNum,
        "ordinal": row.Ordinal,
        "subscriber_id": (row.SubscriberID or "").strip() or None,
        "group_number": (row.GroupNum or "").strip() or None,
        "employer": (row.Employer or "").strip() or None,
        "relationship": (row.Relationship or "").strip() or None,
        "carrier_name": row.CarrierName or (carrier.CarrierName if carrier else None),
        "elect_id": elect_id,
        "claims_address": _claims_address(carrier) if carrier else None,
    }


def _claims_address(carrier: ODCarrier) -> str | None:
    parts = [
        (carrier.Address or "").strip(),
        (carrier.City or "").strip(),
        (carrier.State or "").strip(),
        (carrier.Zip or "").strip(),
    ]
    text = ", ".join(p for p in parts if p)
    return text or None


def od_to_eligibility_request(
    patient: ODPatient,
    insurance_rows: list[ODInsuranceRow],
    carriers_by_num: dict[int, ODCarrier],
    *,
    trigger_event: TriggerEvent,
    cdt_codes: list[str] | None,
    practice_id: str | None,
    rendering_provider_npi: str | None,
) -> MappedEligibility:
    """Map OpenDental records to EligibilityRequest plus primary/secondary write-back IDs."""
    if not insurance_rows:
        raise OpenDentalMappingError("Patient has no insurance rows in OpenDental")

    primary_row = _pick_primary_row(insurance_rows)
    subscriber_id = (primary_row.SubscriberID or "").strip()
    if not subscriber_id:
        raise OpenDentalMappingError("Primary insurance row is missing SubscriberID")

    primary_payer_id = _payer_id_for_row(primary_row, carriers_by_num)
    secondary_row = _pick_secondary_row(insurance_rows, primary_row)
    secondary_payer_id = (
        _payer_id_for_row(secondary_row, carriers_by_num) if secondary_row else None
    )

    carrier = carriers_by_num.get(primary_row.CarrierNum)
    carrier_name = primary_row.CarrierName or (carrier.CarrierName if carrier else None) or None
    secondary_carrier = (
        carriers_by_num.get(secondary_row.CarrierNum) if secondary_row is not None else None
    )
    secondary_carrier_name = None
    if secondary_row is not None:
        secondary_carrier_name = (
            secondary_row.CarrierName
            or (secondary_carrier.CarrierName if secondary_carrier else None)
            or None
        )

    patient_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"opendental:{patient.PatNum}")
    req = EligibilityRequest(
        patient_id=patient_uuid,
        first_name=patient.FName,
        last_name=patient.LName,
        dob=patient.Birthdate,
        subscriber_id=subscriber_id,
        primary_payer_id=primary_payer_id,
        secondary_payer_id=secondary_payer_id,
        cdt_codes=cdt_codes,
        trigger_event=trigger_event,
        practice_id=practice_id or DEFAULT_MOCK_PRACTICE_ID,
        rendering_provider_npi=rendering_provider_npi or DEFAULT_MOCK_RENDERING_NPI,
    )
    return MappedEligibility(
        request=req,
        primary_pat_plan_num=primary_row.PatPlanNum,
        primary_plan_num=primary_row.PlanNum,
        primary_ins_sub_num=primary_row.InsSubNum,
        primary_carrier_name=carrier_name,
        secondary_pat_plan_num=secondary_row.PatPlanNum if secondary_row else None,
        secondary_plan_num=secondary_row.PlanNum if secondary_row else None,
        secondary_ins_sub_num=secondary_row.InsSubNum if secondary_row else None,
        secondary_carrier_name=secondary_carrier_name,
        primary_od_snapshot=_od_plan_snapshot(
            primary_row, carriers_by_num, elect_id=primary_payer_id
        ),
        secondary_od_snapshot=(
            _od_plan_snapshot(secondary_row, carriers_by_num, elect_id=secondary_payer_id)
            if secondary_row
            else None
        ),
    )
