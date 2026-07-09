"""Enqueue payer voice verification after incomplete Stedi checks."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.db import get_eligibility_agent_settings, insert_eligibility_request_event
from app.eligibility.voice.db import (
    fetch_open_session_for_check,
    fetch_payer_voice_config,
    get_supabase_client,
    insert_verification_session,
)
from app.eligibility.voice.bland import bland_configured
from app.eligibility.voice.gate import (
    canonical_voice_escalation_eligible,
    routing_status_voice_eligible,
)

logger = logging.getLogger(__name__)


def _twilio_infra_ready(settings: EligibilitySettings) -> bool:
    return bool(
        (settings.twilio_account_sid or "").strip()
        and (settings.twilio_auth_token or "").strip()
        and (settings.twilio_from_number or "").strip()
        and (settings.twilio_webhook_base_url or "").strip()
    )


def voice_infra_ready(settings: EligibilitySettings) -> bool:
    """True when the configured call provider has credentials + webhook URL."""
    provider = (settings.voice_call_provider or "bland").strip().lower()
    if provider == "bland":
        return bland_configured(settings)
    if provider == "twilio":
        return _twilio_infra_ready(settings)
    return False


def _voice_enabled(settings: EligibilitySettings, agent_settings: dict[str, Any] | None) -> bool:
    """Runtime on/off is the dashboard toggle; env supplies deploy-time stack + credentials."""
    if not getattr(settings, "voice_verification_enabled", False):
        return False
    if not voice_infra_ready(settings):
        return False
    if agent_settings is None:
        return False
    return agent_settings.get("voice_verification_enabled") is not False


def _auto_queue_enabled(agent_settings: dict[str, Any] | None) -> bool:
    if agent_settings is None:
        return True
    return agent_settings.get("voice_verification_auto_queue") is not False


def queue_voice_verification(
    *,
    eligibility_check_id: UUID | str,
    patient_id: UUID | str,
    payer_id: str,
    canonical: dict[str, Any],
    routing: dict[str, Any],
    cdt_codes: list[str] | None = None,
    practice_id: str | None = None,
    request_id: UUID | str | None = None,
    settings: EligibilitySettings | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Idempotently create a payer_verification_sessions row.
    Returns { queued: bool, session_id?, skip_reason? }.
    """
    s = settings or get_settings()
    supabase = get_supabase_client(s)
    agent_settings = get_eligibility_agent_settings(
        supabase,
        practice_id=practice_id,
        settings=s,
    )

    if not force and not _voice_enabled(s, agent_settings):
        return {"queued": False, "skip_reason": "voice_verification_disabled"}

    routing_status = routing.get("status") or canonical.get("routing_status")
    if not routing_status_voice_eligible(str(routing_status)):
        return {"queued": False, "skip_reason": f"routing_status_{routing_status}"}

    eligible, targets = canonical_voice_escalation_eligible(canonical)
    if not eligible and not force:
        return {"queued": False, "skip_reason": "canonical_not_eligible"}

    if not targets:
        targets = list(routing.get("detail", {}).get("missing_fields_target") or [])
    if not targets:
        targets = list(canonical.get("missing_fields") or [])

    payer_cfg = fetch_payer_voice_config(supabase, payer_id)
    if not payer_cfg or not payer_cfg.get("eligibility_phone"):
        return {"queued": False, "skip_reason": "payer_phone_missing"}
    if not payer_cfg.get("voice_escalation_enabled") and not force:
        return {"queued": False, "skip_reason": "payer_voice_escalation_disabled"}

    existing = fetch_open_session_for_check(
        supabase, eligibility_check_id, practice_id=practice_id, settings=s
    )
    if existing:
        return {
            "queued": False,
            "skip_reason": "session_already_open",
            "session_id": existing.get("id"),
        }

    provider = (s.voice_call_provider or "bland").strip().lower()
    if provider not in ("twilio", "bland"):
        provider = "bland"

    session_id = insert_verification_session(
        supabase,
        {
            "practice_id": practice_id,
            "patient_id": str(patient_id),
            "payer_id": payer_id,
            "eligibility_check_id": str(eligibility_check_id),
            "request_id": str(request_id) if request_id else None,
            "status": "queued",
            "missing_fields_target": targets,
            "cdt_codes": list(cdt_codes or []),
            "call_provider": provider,
        },
        settings=s,
    )

    if request_id:
        insert_eligibility_request_event(
            supabase,
            request_id,
            "voice_verification_queued",
            {
                "session_id": str(session_id),
                "missing_fields_target": targets,
                "payer_id": payer_id,
            },
            practice_id=practice_id,
            settings=s,
        )

    logger.info(
        "voice verification queued session_id=%s check_id=%s payer=%s",
        session_id,
        eligibility_check_id,
        payer_id,
    )
    return {"queued": True, "session_id": str(session_id)}


def maybe_auto_queue_voice_verification(
    *,
    request: Any,
    primary_result: dict[str, Any],
    request_id: UUID | str | None = None,
    settings: EligibilitySettings | None = None,
) -> dict[str, Any]:
    """Called after primary Stedi pipeline when auto-queue is enabled."""
    s = settings or get_settings()
    supabase = get_supabase_client(s)
    practice = getattr(request, "practice_id", None)
    agent_settings = get_eligibility_agent_settings(
        supabase,
        practice_id=str(practice) if practice else None,
        settings=s,
    )

    if not _voice_enabled(s, agent_settings):
        return {"queued": False, "skip_reason": "voice_verification_disabled"}
    if not _auto_queue_enabled(agent_settings):
        return {"queued": False, "skip_reason": "auto_queue_disabled"}

    routing = primary_result.get("routing") or {}
    canonical = primary_result.get("canonical") or {}
    check_id = primary_result.get("check_id")
    if not check_id:
        return {"queued": False, "skip_reason": "missing_check_id"}

    detail = routing.get("detail") or {}
    if not detail.get("voice_escalation_eligible"):
        if request_id:
            insert_eligibility_request_event(
                supabase,
                request_id,
                "voice_verification_skipped",
                {"reason": detail.get("voice_skip_reason") or "not_voice_eligible"},
                practice_id=getattr(request, "practice_id", None),
                settings=s,
            )
        return {"queued": False, "skip_reason": detail.get("voice_skip_reason") or "not_voice_eligible"}

    return queue_voice_verification(
        eligibility_check_id=check_id,
        patient_id=request.patient_id,
        payer_id=request.primary_payer_id,
        canonical=canonical,
        routing=routing,
        cdt_codes=list(request.cdt_codes or []),
        practice_id=getattr(request, "practice_id", None),
        request_id=request_id,
        settings=s,
    )
