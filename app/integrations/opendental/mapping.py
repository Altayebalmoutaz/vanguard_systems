"""Mapping from OpenDental payloads to EligibilityRequest."""

from __future__ import annotations

import uuid

from app.eligibility.mock_clinic import DEFAULT_MOCK_PRACTICE_ID, DEFAULT_MOCK_RENDERING_NPI
from app.eligibility.models import EligibilityRequest, TriggerEvent
from app.integrations.opendental.errors import OpenDentalMappingError
from app.integrations.opendental.models import ODCarrier, ODInsuranceRow, ODPatient


def _pick_primary_row(rows: list[ODInsuranceRow]) -> ODInsuranceRow:
    for row in rows:
        if row.Ordinal == 1:
            return row
    return rows[0]


def _pick_secondary_row(rows: list[ODInsuranceRow], primary_row: ODInsuranceRow) -> ODInsuranceRow | None:
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


def od_to_eligibility_request(
    patient: ODPatient,
    insurance_rows: list[ODInsuranceRow],
    carriers_by_num: dict[int, ODCarrier],
    *,
    trigger_event: TriggerEvent,
    cdt_codes: list[str] | None,
    practice_id: str | None,
    rendering_provider_npi: str | None,
) -> tuple[EligibilityRequest, int]:
    """Map OpenDental records to EligibilityRequest and return selected primary PatPlanNum."""
    if not insurance_rows:
        raise OpenDentalMappingError("Patient has no insurance rows in OpenDental")

    primary_row = _pick_primary_row(insurance_rows)
    subscriber_id = (primary_row.SubscriberID or "").strip()
    if not subscriber_id:
        raise OpenDentalMappingError("Primary insurance row is missing SubscriberID")

    primary_payer_id = _payer_id_for_row(primary_row, carriers_by_num)
    secondary_row = _pick_secondary_row(insurance_rows, primary_row)
    secondary_payer_id = _payer_id_for_row(secondary_row, carriers_by_num) if secondary_row else None

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
    return req, primary_row.PatPlanNum

