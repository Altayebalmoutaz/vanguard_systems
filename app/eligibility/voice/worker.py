"""Outbound call worker for payer voice verification (Bland primary, Twilio fallback)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from app.eligibility.config import EligibilitySettings
from app.eligibility.db import get_eligibility_agent_settings, insert_eligibility_request_event
from app.eligibility.voice.bland import bland_configured, initiate_bland_call
from app.eligibility.voice.db import (
    fetch_payer_voice_config,
    fetch_queued_sessions,
    fetch_session_by_id,
    get_supabase_client,
    update_verification_session,
)
from app.eligibility.voice.extract import extract_fields_from_transcript
from app.eligibility.voice.queue import voice_infra_ready
from app.eligibility.voice.reconcile import complete_voice_session_reconciliation
from app.security.phi import scrub_for_log

logger = logging.getLogger(__name__)


def voice_webhook_url(settings: EligibilitySettings, path: str) -> str:
    """Build public webhook URL under the mounted eligibility-agent prefix."""
    base = (settings.twilio_webhook_base_url or "").rstrip("/")
    if not base:
        return f"/eligibility/voice/{path}"
    return urljoin(base + "/", f"eligibility/voice/{path}")


def _twilio_configured(settings: EligibilitySettings) -> bool:
    return bool(
        (settings.twilio_account_sid or "").strip()
        and (settings.twilio_auth_token or "").strip()
        and (settings.twilio_from_number or "").strip()
        and (settings.twilio_webhook_base_url or "").strip()
    )


def _twilio_urls(settings: EligibilitySettings, session_id: str) -> dict[str, str]:
    return {
        "twiml": voice_webhook_url(settings, f"twiml/{session_id}"),
        "status": voice_webhook_url(settings, f"status/{session_id}"),
    }


def initiate_twilio_call(session: dict[str, Any], settings: EligibilitySettings) -> str:
    """Place outbound call; returns CallSid."""
    supabase = get_supabase_client(settings)
    payer_cfg = fetch_payer_voice_config(supabase, str(session.get("payer_id") or ""))
    if not payer_cfg or not payer_cfg.get("eligibility_phone"):
        raise RuntimeError("payer_phone_missing")

    session_id = str(session["id"])
    urls = _twilio_urls(settings, session_id)
    to_number = str(payer_cfg["eligibility_phone"])

    data = {
        "To": to_number,
        "From": settings.twilio_from_number,
        "Url": urls["twiml"],
        "Method": "POST",
        "StatusCallback": urls["status"],
        "StatusCallbackMethod": "POST",
        "StatusCallbackEvent": "completed",
        "Record": "false",
    }
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    api_url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Calls.json"

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(api_url, data=data, auth=auth)
        resp.raise_for_status()
        body = resp.json()
        call_sid = body.get("sid")
        if not call_sid:
            raise RuntimeError("twilio_call_missing_sid")
        return str(call_sid)


def run_voice_sweep(settings: EligibilitySettings) -> dict[str, Any]:
    """Pick queued sessions and initiate Bland (or Twilio) calls."""
    worker_on = settings.voice_verification_worker_enabled or (
        settings.voice_verification_enabled and bland_configured(settings)
    )
    if not worker_on:
        return {"skipped": "worker_disabled", "started": 0}
    if not getattr(settings, "voice_verification_enabled", False):
        return {"skipped": "voice_stack_disabled", "started": 0}
    if not voice_infra_ready(settings):
        return {"skipped": "voice_provider_not_configured", "started": 0}

    supabase = get_supabase_client(settings)
    batch = min(int(settings.voice_verification_batch_size), 20)
    sessions = fetch_queued_sessions(supabase, limit=batch)

    provider = (settings.voice_call_provider or "bland").strip().lower()
    use_bland = provider == "bland" and bland_configured(settings)

    if not use_bland and not _twilio_configured(settings):
        if settings.voice_demo_auto_complete and settings.voice_demo_transcript:
            demo_done = 0
            for session in sessions:
                session_id = session.get("id")
                if not session_id:
                    continue
                try:
                    process_call_completion(
                        str(session_id),
                        call_duration=60,
                        speech_result=settings.voice_demo_transcript,
                        settings=settings,
                    )
                    demo_done += 1
                except Exception as exc:
                    logger.warning("voice demo complete failed session=%s: %s", session_id, exc)
            return {"demo_completed": demo_done, "considered": len(sessions)}
        return {"skipped": "twilio_not_configured", "started": 0}
    started = 0
    errors = 0
    skipped_disabled = 0

    for session in sessions:
        session_id = session.get("id")
        if not session_id:
            continue
        practice_id = session.get("practice_id")
        agent_settings = get_eligibility_agent_settings(
            supabase,
            practice_id=str(practice_id) if practice_id else None,
            settings=settings,
        )
        if agent_settings is None or agent_settings.get("voice_verification_enabled") is False:
            skipped_disabled += 1
            continue
        try:
            provider_name = "bland" if use_bland else "twilio"
            update_verification_session(
                supabase,
                session_id,
                {"status": "calling", "call_provider": provider_name},
            )
            if use_bland:
                webhook_url = voice_webhook_url(settings, f"bland/{session_id}")
                call_sid = initiate_bland_call(session, settings, webhook_url=webhook_url)
            else:
                call_sid = initiate_twilio_call(session, settings)
            update_verification_session(
                supabase,
                session_id,
                {"call_sid": call_sid},
            )
            request_id = session.get("request_id")
            if request_id:
                insert_eligibility_request_event(
                    supabase,
                    request_id,
                    "voice_verification_calling",
                    {
                        "session_id": str(session_id),
                        "call_sid": call_sid,
                        "provider": provider_name,
                    },
                )
            started += 1
        except Exception as exc:
            errors += 1
            logger.warning(
                "voice call failed session=%s err=%s",
                session_id,
                scrub_for_log(str(exc)),
            )
            update_verification_session(
                supabase,
                session_id,
                {
                    "status": "failed",
                    "failure_code": "call_initiation_failed",
                    "failure_message": str(exc)[:500],
                },
            )
            request_id = session.get("request_id")
            if request_id:
                insert_eligibility_request_event(
                    supabase,
                    request_id,
                    "voice_verification_failed",
                    {"session_id": str(session_id), "error": str(exc)[:200]},
                )

    return {
        "started": started,
        "errors": errors,
        "considered": len(sessions),
        "skipped_disabled": skipped_disabled,
    }


def process_call_completion(
    session_id: str,
    *,
    call_duration: int | None,
    speech_result: str | None,
    settings: EligibilitySettings,
) -> dict[str, Any]:
    """Handle completed call: extract, reconcile, pending_review."""
    supabase = get_supabase_client(settings)
    session = fetch_session_by_id(supabase, session_id)
    if not session:
        raise ValueError("session_not_found")

    practice_id = str(session.get("practice_id") or "").strip() or None

    transcript = (speech_result or "").strip()
    if not transcript and settings.voice_demo_transcript:
        transcript = settings.voice_demo_transcript

    extracted = extract_fields_from_transcript(
        transcript,
        missing_fields_target=list(session.get("missing_fields_target") or []),
        cdt_codes=list(session.get("cdt_codes") or []),
        settings=settings,
    )

    if call_duration is not None:
        update_verification_session(
            supabase,
            session_id,
            {"call_duration_seconds": call_duration},
            practice_id=practice_id,
        )

    if not transcript:
        update_verification_session(
            supabase,
            session_id,
            {
                "status": "failed",
                "failure_code": "empty_transcript",
                "failure_message": "No speech captured on payer call",
            },
            practice_id=practice_id,
        )
        return {"status": "failed", "reason": "empty_transcript"}

    return complete_voice_session_reconciliation(
        session_id,
        transcript=transcript,
        extracted=extracted,
        settings=settings,
        practice_id=practice_id,
    )


def _leased_voice_sweep(settings: EligibilitySettings) -> dict[str, Any]:
    from app.config import get_settings as get_app_settings
    from app.db.leases import LEASE_VOICE_WORKER, try_lease

    with try_lease(get_app_settings(), LEASE_VOICE_WORKER) as acquired:
        if not acquired:
            return {"skipped": "lease_held_elsewhere", "started": 0}
        return run_voice_sweep(settings)


async def _voice_loop(settings: EligibilitySettings) -> None:
    interval = max(10.0, float(settings.voice_verification_worker_interval_seconds))
    logger.info("voice verification worker started (interval=%ss)", interval)
    while True:
        try:
            summary = await asyncio.to_thread(_leased_voice_sweep, settings)
            if summary.get("started") or summary.get("errors") or summary.get("demo_completed"):
                logger.warning("voice verification sweep: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("voice verification sweep failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


def start_voice_worker(settings: EligibilitySettings) -> asyncio.Task[None]:
    return asyncio.create_task(_voice_loop(settings))
