"""Enqueue OpenDental patients onto the unified eligibility request queue."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

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
        "writeback_shadow_compare": bool(connection.get("writeback_shadow_compare")),
    }
    secondary_pat_plan = getattr(mapped, "secondary_pat_plan_num", None)
    if secondary_pat_plan is not None:
        payload["secondary_pat_plan_num"] = secondary_pat_plan
        payload["secondary_plan_num"] = getattr(mapped, "secondary_plan_num", None)
        payload["secondary_ins_sub_num"] = getattr(mapped, "secondary_ins_sub_num", None)
        payload["secondary_carrier_name"] = getattr(mapped, "secondary_carrier_name", None)
    primary_od_snapshot = getattr(mapped, "primary_od_snapshot", None)
    if primary_od_snapshot:
        payload["primary_od_snapshot"] = primary_od_snapshot
    secondary_od_snapshot = getattr(mapped, "secondary_od_snapshot", None)
    if secondary_od_snapshot:
        payload["secondary_od_snapshot"] = secondary_od_snapshot
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
    appointment_date: date | None = None,
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
        "appointment_date": appointment_date.isoformat() if appointment_date else None,
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


_BLOCKING_REQUEST_STATUSES = ("queued", "processing", "completed")


def od_request_exists_today(
    settings: Settings,
    *,
    practice_id: str,
    pat_num: int,
) -> bool:
    """True when a same-day request is still in-flight or already succeeded.

    ``failed`` / ``needs_attention`` do not block a new enqueue (Stedi retry).
    A completed Stedi check is blocked here and by ``_checked_today`` so
    writeback retries go through the writeback pipeline, not a second 270/271.
    """
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
                  and status = any(%s)
                limit 1
                """,
                (practice_id, str(pat_num), today_start, list(_BLOCKING_REQUEST_STATUSES)),
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
    appointment_date: date | None = None,
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
        appointment_date=appointment_date,
    )
    try:
        return create_eligibility_request(app_settings, practice_id=practice_id, payload=payload)
    except ValueError as exc:
        if str(exc) != "idempotency_conflict":
            raise
        # Same-day failed/needs_attention row still holds the base idempotency key.
        payload["idempotency_key"] = f"{payload['idempotency_key']}:r{uuid4().hex[:8]}"
        try:
            return create_eligibility_request(app_settings, practice_id=practice_id, payload=payload)
        except ValueError as retry_exc:
            if str(retry_exc) == "idempotency_conflict":
                return None
            raise
