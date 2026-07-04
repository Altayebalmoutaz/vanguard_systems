"""Approve or reject voice verification sessions (HITL)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.cost_calculator import calculate_responsibility
from app.eligibility.db import (
    complete_eligibility_request_after_voice,
    fetch_payer_fee_schedule_as_dict,
    get_eligibility_check_by_id,
    get_supabase,
    insert_eligibility_request_event,
    insert_procedure_estimates,
    update_eligibility_check_fields,
)
from app.eligibility.fee_schedule import merge_ucr_fallback_into_fee_schedule
from app.eligibility.voice.db import fetch_session_by_id, update_verification_session
from app.eligibility.voice.reconcile import _check_row_to_canonical

logger = logging.getLogger(__name__)


def approve_voice_verification_session(
    session_id: str | UUID,
    *,
    approved_by: str,
    settings: EligibilitySettings | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    supabase = get_supabase(s)
    session = fetch_session_by_id(supabase, session_id, settings=s)
    if not session:
        raise ValueError("session_not_found")
    if session.get("status") != "pending_review":
        raise ValueError(f"invalid_session_status:{session.get('status')}")

    merged_id = session.get("merged_check_id")
    if not merged_id:
        raise ValueError("missing_merged_check_id")

    practice_id = str(session.get("practice_id") or "").strip() or None
    merged_check = get_eligibility_check_by_id(
        supabase, UUID(str(merged_id)), practice_id=practice_id, settings=s
    )
    if not merged_check:
        raise ValueError("merged_check_not_found")

    now_iso = datetime.now(UTC).isoformat()
    update_eligibility_check_fields(
        supabase,
        merged_id,
        routing_status="CLEARED",
        response_complete=True,
        missing_fields=[],
        practice_id=practice_id,
        settings=s,
    )

    proc_rows: list[dict[str, Any]] = []
    if merged_check.get("is_active"):
        try:
            canonical = _check_row_to_canonical(merged_check)
            canonical["routing_status"] = "CLEARED"
            canonical["response_complete"] = True
            canonical["missing_fields"] = []
            payer_id = str(merged_check.get("payer_id") or "")
            fee = fetch_payer_fee_schedule_as_dict(supabase, payer_id)
            cdt_codes = list(session.get("cdt_codes") or [])
            merge_ucr_fallback_into_fee_schedule(fee, payer_id, cdt_codes, s)
            for code in cdt_codes:
                if code and code not in [p.get("cdt_code") for p in canonical.get("procedure_details") or []]:
                    canonical.setdefault("procedure_details", []).append(
                        {"cdt_code": code, "procedure_covered": True}
                    )
            est = calculate_responsibility(canonical, fee)
            for e in est:
                proc_rows.append(
                    {
                        "cdt_code": e["cdt_code"],
                        "procedure_covered": True,
                        "allowed_amount": e["allowed_amount"],
                        "insurance_pays": e["insurance_pays"],
                        "patient_responsibility": e["patient_responsibility"],
                    }
                )
            if proc_rows:
                insert_procedure_estimates(
                    supabase, UUID(str(merged_id)), proc_rows, practice_id=practice_id, settings=s
                )
        except Exception:
            logger.exception("voice approve cost calculation failed session=%s", session_id)

    update_verification_session(
        supabase,
        session_id,
        {
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": now_iso,
        },
        practice_id=practice_id,
        settings=s,
    )

    request_id = session.get("request_id")
    if request_id:
        complete_eligibility_request_after_voice(
            supabase,
            request_id,
            primary_check_id=merged_id,
            completed_at=now_iso,
            practice_id=practice_id,
            settings=s,
        )
        insert_eligibility_request_event(
            supabase,
            request_id,
            "voice_verification_approved",
            {
                "session_id": str(session_id),
                "merged_check_id": str(merged_id),
                "approved_by": approved_by,
            },
            practice_id=practice_id,
            settings=s,
        )

    return {
        "session_id": str(session_id),
        "merged_check_id": str(merged_id),
        "procedure_estimates": proc_rows,
        "status": "approved",
    }


def reject_voice_verification_session(
    session_id: str | UUID,
    *,
    rejected_by: str,
    reason: str | None = None,
    settings: EligibilitySettings | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    supabase = get_supabase(s)
    session = fetch_session_by_id(supabase, session_id, settings=s)
    if not session:
        raise ValueError("session_not_found")
    if session.get("status") != "pending_review":
        raise ValueError(f"invalid_session_status:{session.get('status')}")

    practice_id = str(session.get("practice_id") or "").strip() or None
    update_verification_session(
        supabase,
        session_id,
        {
            "status": "rejected",
            "approved_by": rejected_by,
            "approved_at": datetime.now(UTC).isoformat(),
            "failure_message": reason or "Rejected by staff review",
        },
        practice_id=practice_id,
        settings=s,
    )

    request_id = session.get("request_id")
    if request_id:
        insert_eligibility_request_event(
            supabase,
            request_id,
            "voice_verification_rejected",
            {"session_id": str(session_id), "reason": reason},
            practice_id=practice_id,
            settings=s,
        )

    return {"session_id": str(session_id), "status": "rejected"}
