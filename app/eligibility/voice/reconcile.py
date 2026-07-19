"""Merge voice-extracted fields into canonical and persist supplemental checks."""

from __future__ import annotations

import copy
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.db import (
    get_supabase,
    insert_eligibility_check,
    insert_eligibility_request_event,
)
from app.eligibility.integrity import validate_completeness
from app.eligibility.router import route
from app.eligibility.services import canonical_to_row
from app.eligibility.voice.db import fetch_session_by_id, update_verification_session

logger = logging.getLogger(__name__)

NUMERIC_CANONICAL_FIELDS = (
    "deductible_remaining",
    "annual_max_remaining",
    "deductible_total",
    "deductible_met",
    "annual_max_total",
    "annual_max_used",
    "coverage_percent",
    "copay",
    "coinsurance",
)


def merge_voice_extraction(
    base_canonical: dict[str, Any],
    extracted: dict[str, Any],
    *,
    session_id: str | UUID,
    call_reference: str | None = None,
) -> dict[str, Any]:
    """Patch canonical with voice-sourced values; attach provenance."""
    patched = copy.deepcopy(base_canonical)
    for field in NUMERIC_CANONICAL_FIELDS:
        if field in extracted and extracted[field] is not None:
            patched[field] = extracted[field]
    for field in ("is_active", "is_covered", "in_network"):
        if field in extracted and extracted[field] is not None:
            patched[field] = extracted[field]

    proc_details = list(patched.get("procedure_details") or [])
    extracted_procs = extracted.get("procedure_details")
    if isinstance(extracted_procs, list):
        by_cdt = {str(p.get("cdt_code")): p for p in proc_details if p.get("cdt_code")}
        for ep in extracted_procs:
            if not isinstance(ep, dict):
                continue
            code = str(ep.get("cdt_code") or "").strip().upper()
            if not code:
                continue
            row = by_cdt.get(code, {"cdt_code": code})
            if ep.get("procedure_covered") is not None:
                row["procedure_covered"] = ep.get("procedure_covered")
            by_cdt[code] = row
        patched["procedure_details"] = list(by_cdt.values())

    patched["voice_verification"] = {
        "session_id": str(session_id),
        "call_reference": call_reference or extracted.get("call_reference"),
        "extracted_at": datetime.now(UTC).isoformat(),
        "source": "voice_verification",
    }
    validate_completeness(patched)
    return patched


def persist_voice_supplemental_check(
    *,
    session: dict[str, Any],
    base_check: dict[str, Any],
    patched_canonical: dict[str, Any],
    settings: EligibilitySettings | None = None,
) -> UUID:
    """Insert supplemental eligibility_checks row; cap routing for human review."""
    s = settings or get_settings()
    supabase = get_supabase(s)
    routing = route(patched_canonical, supabase)
    # Human review required — do not treat as production CLEARED until approved.
    routing_status = routing.get("status")
    if routing_status == "CLEARED":
        routing_status = "PENDING_VOICE_REVIEW"

    raw_for_db = {
        "source": "voice_verification",
        "session_id": session.get("id"),
        "extracted_fields": session.get("extracted_fields"),
        "base_check_id": base_check.get("id"),
    }
    row = canonical_to_row(
        UUID(str(base_check["patient_id"])),
        patched_canonical,
        routing_status=routing_status,
        has_secondary_flag=bool(base_check.get("has_secondary")),
        secondary_payer_id=base_check.get("secondary_payer_id"),
        raw_for_db=raw_for_db,
    )
    row["source_check_id"] = str(base_check["id"])
    row["verification_source"] = "voice_verification"
    practice_id = (
        str(session.get("practice_id") or "").strip()
        or str(base_check.get("practice_id") or "").strip()
        or None
    )
    merged_id = insert_eligibility_check(supabase, row, practice_id=practice_id, settings=s)
    return merged_id


def voice_recovery_complete(patched: dict[str, Any]) -> bool:
    """True when Stedi + voice together satisfy Layer-4 completeness."""
    return bool(
        patched.get("response_complete")
        and patched.get("is_active") is not False
        and not (patched.get("missing_fields") or [])
    )


def complete_voice_session_reconciliation(
    session_id: str | UUID,
    *,
    transcript: str,
    extracted: dict[str, Any],
    settings: EligibilitySettings | None = None,
) -> dict[str, Any]:
    """Full post-call flow: merge, supplemental check, pending_review session."""
    s = settings or get_settings()
    supabase = get_supabase(s)
    session = fetch_session_by_id(supabase, session_id, settings=s)
    if not session:
        raise ValueError("session_not_found")

    check_id = session.get("eligibility_check_id")
    practice_id = str(session.get("practice_id") or "").strip() or None
    from app.eligibility.db import get_eligibility_check_by_id

    base_check = get_eligibility_check_by_id(
        supabase, UUID(str(check_id)), practice_id=practice_id, settings=s
    )
    if not base_check:
        raise ValueError("base_check_not_found")

    base_canonical = _check_row_to_canonical(base_check)
    call_ref = extracted.get("call_reference")
    patched = merge_voice_extraction(
        base_canonical,
        extracted,
        session_id=session_id,
        call_reference=str(call_ref) if call_ref else None,
    )
    merged_id = persist_voice_supplemental_check(
        session=session,
        base_check=base_check,
        patched_canonical=patched,
        settings=s,
    )

    update_verification_session(
        supabase,
        session_id,
        {
            "status": "pending_review",
            "transcript_redacted": transcript,
            "extracted_fields": extracted,
            "merged_check_id": str(merged_id),
            "call_reference": call_ref,
        },
        practice_id=practice_id,
        settings=s,
    )

    result: dict[str, Any] = {
        "session_id": str(session_id),
        "merged_check_id": str(merged_id),
        "routing_status": patched.get("routing_status"),
        "missing_fields": patched.get("missing_fields"),
        "response_complete": patched.get("response_complete"),
        "status": "pending_review",
    }

    if getattr(s, "voice_auto_approve_when_complete", True) and voice_recovery_complete(patched):
        from app.eligibility.voice.approve import approve_voice_verification_session

        approved = approve_voice_verification_session(
            session_id,
            approved_by="system:voice_auto_complete",
            settings=s,
        )
        result.update(approved)
        result["status"] = "approved"
        result["auto_approved"] = True
        request_id = session.get("request_id")
        if request_id:
            insert_eligibility_request_event(
                supabase,
                request_id,
                "voice_verification_auto_approved",
                {
                    "session_id": str(session_id),
                    "merged_check_id": str(merged_id),
                    "missing_fields": patched.get("missing_fields") or [],
                },
            )
        return result

    request_id = session.get("request_id")
    if request_id:
        insert_eligibility_request_event(
            supabase,
            request_id,
            "voice_verification_completed",
            {
                "session_id": str(session_id),
                "merged_check_id": str(merged_id),
                "extracted_fields": extracted,
            },
        )

    return result


def _check_row_to_canonical(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild minimal canonical dict from stored eligibility_checks row."""
    checked_at = row.get("checked_at")
    if isinstance(checked_at, str):
        checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    elif checked_at is None:
        checked_at = datetime.now(UTC)

    return {
        "payer_id": row.get("payer_id"),
        "checked_at": checked_at,
        "coverage_order": row.get("coverage_order"),
        "is_active": row.get("is_active"),
        "inactive_reason": row.get("inactive_reason"),
        "is_covered": row.get("is_covered"),
        "in_network": row.get("in_network"),
        "coverage_percent": row.get("coverage_percent"),
        "copay": row.get("copay"),
        "coinsurance": row.get("coinsurance"),
        "deductible_total": row.get("deductible_total"),
        "deductible_met": row.get("deductible_met"),
        "deductible_remaining": row.get("deductible_remaining"),
        "annual_max_total": row.get("annual_max_total"),
        "annual_max_used": row.get("annual_max_used"),
        "annual_max_remaining": row.get("annual_max_remaining"),
        "response_complete": row.get("response_complete"),
        "missing_fields": list(row.get("missing_fields") or []),
        "integrity_warnings": list(row.get("integrity_warnings") or []),
        "normalization_version": row.get("normalization_version") or "1.0",
        "procedure_details": [],
        "raw_response": row.get("raw_response")
        if isinstance(row.get("raw_response"), dict)
        else {},
    }
