"""Enqueue OpenDental patients onto the unified eligibility request queue."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from psycopg.rows import dict_row

from app.config import Settings
from app.dashboard.store import create_eligibility_request
from app.db.connection import neon_connection
from app.eligibility.models import TriggerEvent
from app.integrations.opendental.cdt_resolve import ResolveResult
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.mapping import MappedEligibility, od_to_eligibility_request

logger = logging.getLogger(__name__)


def opendental_patient_uuid(pat_num: int):
    return uuid5(NAMESPACE_DNS, f"opendental:{pat_num}")


def _od_input_json(
    *,
    pat_num: int,
    mapped: MappedEligibility,
    connection: dict[str, Any],
    resolve: ResolveResult | None = None,
    apt_nums: list[int] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "opendental",
        "pat_num": pat_num,
        "primary_pat_plan_num": mapped.primary_pat_plan_num,
        "primary_plan_num": mapped.primary_plan_num,
        "primary_ins_sub_num": mapped.primary_ins_sub_num,
        "primary_carrier_name": mapped.primary_carrier_name,
        "writeback_enabled": bool(connection.get("writeback_enabled")),
        "writeback_full": bool(connection.get("writeback_full")),
    }
    if resolve is not None:
        payload.update(resolve.to_input_json(apt_nums=apt_nums))
    elif apt_nums is not None:
        payload["apt_nums"] = list(apt_nums)
    return payload


def build_od_eligibility_payload(
    client: OpenDentalClient,
    *,
    pat_num: int,
    practice_id: str,
    connection: dict[str, Any],
    cdt_codes: list[str] | None = None,
    trigger_event: TriggerEvent = TriggerEvent.PRE_APPOINTMENT,
    resolve: ResolveResult | None = None,
    apt_nums: list[int] | None = None,
) -> dict[str, Any]:
    patient = client.get_patient(pat_num)
    insurance_rows = client.get_patient_insurance(pat_num)
    carriers_by_num: dict[int, Any] = {}
    for row in insurance_rows:
        if row.CarrierNum not in carriers_by_num:
            carriers_by_num[row.CarrierNum] = client.get_carrier(row.CarrierNum)

    mapped = od_to_eligibility_request(
        patient,
        insurance_rows,
        carriers_by_num,
        trigger_event=trigger_event,
        cdt_codes=cdt_codes,
        practice_id=practice_id,
        rendering_provider_npi=None,
    )
    req = mapped.request
    return {
        "patient_id": str(req.patient_id),
        "first_name": req.first_name,
        "last_name": req.last_name,
        "dob": req.dob.isoformat() if hasattr(req.dob, "isoformat") else str(req.dob),
        "subscriber_id": req.subscriber_id,
        "primary_payer_id": req.primary_payer_id,
        "secondary_payer_id": req.secondary_payer_id,
        "cdt_codes": list(req.cdt_codes or []),
        "trigger_event": trigger_event.value,
        "priority": "medium",
        "idempotency_key": f"od:{practice_id}:{pat_num}:{date.today().isoformat()}",
        "input_json": {
            **_od_input_json(
                pat_num=pat_num,
                mapped=mapped,
                connection=connection,
                resolve=resolve,
                apt_nums=apt_nums,
            ),
            "submitted_from": "opendental_poll",
        },
    }


def od_request_exists_today(
    settings: Settings,
    *,
    practice_id: str,
    pat_num: int,
) -> bool:
    try:
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with (
            neon_connection(settings, practice_id=practice_id) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                """
                select 1
                from rcm.eligibility_requests
                where practice_id = %s
                  and (input_json->>'pat_num') = %s
                  and created_at >= %s
                limit 1
                """,
                (practice_id, str(pat_num), today_start),
            )
            return cur.fetchone() is not None
    except Exception as exc:
        logger.debug("od_request_exists_today skipped: %s", exc)
        return False


def enqueue_od_eligibility_check(
    app_settings: Settings,
    *,
    practice_id: str,
    pat_num: int,
    connection: dict[str, Any],
    client: OpenDentalClient,
    cdt_codes: list[str] | None = None,
    trigger_event: TriggerEvent = TriggerEvent.PRE_APPOINTMENT,
    resolve: ResolveResult | None = None,
    apt_nums: list[int] | None = None,
) -> dict[str, Any] | None:
    if od_request_exists_today(app_settings, practice_id=practice_id, pat_num=pat_num):
        return None
    payload = build_od_eligibility_payload(
        client,
        pat_num=pat_num,
        practice_id=practice_id,
        connection=connection,
        cdt_codes=cdt_codes,
        trigger_event=trigger_event,
        resolve=resolve,
        apt_nums=apt_nums,
    )
    try:
        return create_eligibility_request(app_settings, practice_id=practice_id, payload=payload)
    except ValueError as exc:
        if str(exc) == "idempotency_conflict":
            return None
        raise
