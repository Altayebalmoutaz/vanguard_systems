"""Process queued eligibility requests in-process (replaces Supabase Edge Function)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from app.audit.writer import write_audit_log
from app.config import Settings
from app.config import get_settings as get_app_settings
from app.eligibility.config import get_settings as get_eligibility_settings
from app.eligibility.db import (
    complete_eligibility_request_processing,
    fail_eligibility_request_processing,
    fetch_eligibility_request_row,
    insert_eligibility_request_event,
    lock_eligibility_request_for_processing,
    touch_eligibility_agent_settings_sync,
)
from app.eligibility.db_reference import get_supabase
from app.eligibility.models import EligibilityRequest, TriggerEvent
from app.eligibility.services import run_eligibility_check_endpoint

logger = logging.getLogger(__name__)

FailureCategory = Literal[
    "config_error", "agent_error", "payer_error", "timeout", "validation_error", "unknown"
]


class EligibilityRequestSkipped(Exception):
    """Request is not in queued state — no work to do."""


def build_eligibility_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "patient_id": row.get("patient_id"),
        "first_name": row.get("first_name"),
        "last_name": row.get("last_name"),
        "dob": row.get("dob"),
        "subscriber_id": row.get("subscriber_id"),
        "primary_payer_id": row.get("primary_payer_id"),
        "secondary_payer_id": row.get("secondary_payer_id"),
        "plan_id": row.get("plan_id"),
        "cdt_codes": row.get("cdt_codes") or [],
        "trigger_event": row.get("trigger_event") or TriggerEvent.APPOINTMENT_BOOKED.value,
        "eligibility_request_id": row.get("id"),
        "practice_id": row.get("practice_id"),
    }


def extract_check_id(result: dict[str, Any], key: Literal["primary", "secondary"]) -> str | None:
    section = result.get(key)
    if isinstance(section, dict):
        check_id = section.get("check_id")
        if check_id:
            return str(check_id)
    if key == "primary" and result.get("cached") is True:
        record = result.get("record")
        if isinstance(record, dict) and record.get("id"):
            return str(record["id"])
    return None


def classify_failure(message: str, http_status: int | None = None) -> FailureCategory:
    lower = message.lower()
    if "missing required" in lower or "configuration" in lower:
        return "config_error"
    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    if http_status in {400, 422} or "validation" in lower:
        return "validation_error"
    if http_status == 404:
        return "agent_error"
    if http_status and http_status >= 500:
        return "agent_error"
    if http_status and http_status >= 400:
        return "payer_error"
    return "unknown"


def actionable_error(message: str, http_status: int | None = None) -> dict[str, str]:
    lower = message.lower()
    if "member" in lower or "subscriber" in lower:
        return {
            "error_code": "INVALID_MEMBER_ID",
            "suggested_action": "Request updated insurance information from the patient.",
            "terminal_status": "needs_attention",
        }
    if "dob" in lower or "birth" in lower:
        return {
            "error_code": "DOB_MISMATCH",
            "suggested_action": "Verify patient demographics before retrying.",
            "terminal_status": "needs_attention",
        }
    if "inactive" in lower or "not active" in lower:
        return {
            "error_code": "INACTIVE_COVERAGE",
            "suggested_action": "Notify the patient that coverage appears inactive.",
            "terminal_status": "needs_attention",
        }
    if (
        "timeout" in lower
        or "timed out" in lower
        or http_status in {408, 429}
        or (http_status and http_status >= 500)
    ):
        return {
            "error_code": "PAYER_TIMEOUT",
            "suggested_action": "Retry automatically when the next retry window opens.",
            "terminal_status": "retrying",
        }
    if http_status == 404:
        return {
            "error_code": "AGENT_ENDPOINT_NOT_FOUND",
            "suggested_action": "Verify eligibility agent routing configuration.",
            "terminal_status": "failed",
        }
    return {
        "error_code": "AGENT_ERROR",
        "suggested_action": "Review the request details and retry if payer information is correct.",
        "terminal_status": "failed",
    }


def process_eligibility_request(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
    locked_by: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one eligibility request end-to-end and update queue state."""
    app_settings = settings if isinstance(settings, Settings) else get_app_settings()
    elig_settings = get_eligibility_settings()
    supabase = get_supabase(elig_settings)

    row = fetch_eligibility_request_row(
        app_settings,
        practice_id=practice_id,
        request_id=request_id,
    )
    if not row:
        raise LookupError(f"Eligibility request not found: {request_id}")

    if str(row.get("status") or "") != "queued":
        raise EligibilityRequestSkipped(str(row.get("status") or "unknown"))

    attempt_count = int(row.get("attempt_count") or 0) + 1
    lock_eligibility_request_for_processing(
        app_settings,
        practice_id=practice_id,
        request_id=request_id,
        attempt_count=attempt_count,
        locked_by=locked_by,
    )
    touch_eligibility_agent_settings_sync(app_settings, practice_id=practice_id)
    insert_eligibility_request_event(
        supabase,
        request_id,
        "started",
        {"attempt_count": attempt_count, "source": "pipeline_worker"},
        practice_id=practice_id,
        settings=app_settings,
    )
    write_audit_log(
        app_settings,
        practice_id=practice_id,
        action="eligibility.request.started",
        entity_type="eligibility_request",
        entity_id=request_id,
        performed_by=locked_by,
        metadata={"attempt_count": attempt_count},
    )

    agent_started = time.monotonic()
    agent_http_status: int | None = 200
    try:
        merged = {**build_eligibility_payload_from_row(row), **(payload or {})}
        request = EligibilityRequest.model_validate(merged)
        insert_eligibility_request_event(
            supabase,
            request_id,
            "agent_call_started",
            {"payload_keys": sorted(merged.keys())},
            practice_id=practice_id,
            settings=app_settings,
        )
        result = run_eligibility_check_endpoint(
            request,
            settings=elig_settings,
            eligibility_request_id=request_id,
        )
        agent_duration_ms = int((time.monotonic() - agent_started) * 1000)
        insert_eligibility_request_event(
            supabase,
            request_id,
            "agent_call_completed",
            {"duration_ms": agent_duration_ms},
            practice_id=practice_id,
            settings=app_settings,
        )

        primary_check_id = extract_check_id(result, "primary")
        secondary_check_id = extract_check_id(result, "secondary")
        complete_eligibility_request_processing(
            app_settings,
            practice_id=practice_id,
            request_id=request_id,
            primary_check_id=primary_check_id,
            secondary_check_id=secondary_check_id,
            output_json=result,
            agent_http_status=agent_http_status,
            agent_duration_ms=agent_duration_ms,
        )
        insert_eligibility_request_event(
            supabase,
            request_id,
            "result_linked",
            {
                "primary_check_id": primary_check_id,
                "secondary_check_id": secondary_check_id,
            },
            practice_id=practice_id,
            settings=app_settings,
        )
        write_audit_log(
            app_settings,
            practice_id=practice_id,
            action="eligibility.request.completed",
            entity_type="eligibility_request",
            entity_id=request_id,
            performed_by=locked_by,
            metadata={
                "primary_check_id": primary_check_id,
                "secondary_check_id": secondary_check_id,
            },
        )
        # Writeback is best-effort after a successful check: never let enqueue
        # failures (e.g. Jsonb datetime) overwrite a completed eligibility result.
        writeback: dict[str, Any] | None = None
        try:
            from app.integrations.opendental.post_check import maybe_enqueue_od_writeback

            writeback = maybe_enqueue_od_writeback(
                app_settings,
                practice_id=practice_id,
                request_id=request_id,
                row=row,
                result=result,
            )
        except Exception as wb_exc:
            from app.security.phi import scrub_for_log

            logger.exception(
                "OD writeback enqueue failed after successful eligibility request_id=%s",
                request_id,
            )
            insert_eligibility_request_event(
                supabase,
                request_id,
                "opendental_writeback_enqueue_failed",
                {"error": scrub_for_log(str(wb_exc))[:300]},
                practice_id=practice_id,
                settings=app_settings,
            )
        out: dict[str, Any] = {
            **result,
            "request_id": str(request_id),
            "primary_check_id": primary_check_id,
            "secondary_check_id": secondary_check_id,
            "terminal_status": "completed",
        }
        if writeback:
            out["opendental_writeback"] = writeback
        return out
    except Exception as exc:
        agent_duration_ms = int((time.monotonic() - agent_started) * 1000)
        # Exception text can echo request PHI (names, DOBs, member ids); scrub
        # before it is persisted to error_message / request events / audit logs.
        from app.security.phi import scrub_for_log

        message = scrub_for_log(str(exc))
        category = classify_failure(message, agent_http_status)
        action = actionable_error(message, agent_http_status)
        max_attempts = int(row.get("max_attempts") or 0) or 3
        next_retry_at = None
        if action["terminal_status"] == "retrying" and attempt_count < max_attempts:
            next_retry_at = datetime.now(UTC) + timedelta(minutes=5)

        fail_eligibility_request_processing(
            app_settings,
            practice_id=practice_id,
            request_id=request_id,
            terminal_status=action["terminal_status"],
            failure_category=category,
            error_message=message,
            error_code=action["error_code"],
            suggested_action=action["suggested_action"],
            agent_http_status=agent_http_status,
            agent_duration_ms=agent_duration_ms,
            next_retry_at=next_retry_at,
        )
        if next_retry_at:
            touch_eligibility_agent_settings_sync(
                app_settings,
                practice_id=practice_id,
                next_retry_at=next_retry_at,
            )
        insert_eligibility_request_event(
            supabase,
            request_id,
            "failed",
            {
                "failure_category": category,
                "error_code": action["error_code"],
                "message": message,
            },
            practice_id=practice_id,
            settings=app_settings,
        )
        write_audit_log(
            app_settings,
            practice_id=practice_id,
            action="eligibility.request.failed",
            entity_type="eligibility_request",
            entity_id=request_id,
            performed_by=locked_by,
            metadata={
                "failure_category": category,
                "error_code": action["error_code"],
                "terminal_status": action["terminal_status"],
            },
        )
        return {
            "request_id": str(request_id),
            "terminal_status": action["terminal_status"],
            "error": message,
            "handled": True,
        }
