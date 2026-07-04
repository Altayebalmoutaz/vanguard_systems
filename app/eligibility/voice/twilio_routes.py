"""Twilio TwiML and webhook handlers for payer voice verification."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID
from xml.sax.saxutils import escape

from fastapi import APIRouter, Form, HTTPException, Request, Response

from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.db import get_eligibility_check_by_id
from app.eligibility.voice.bland import (
    map_bland_analysis_to_extracted,
    map_bland_variables_to_extracted,
)
from app.eligibility.voice.db import (
    fetch_eligibility_request,
    fetch_session_by_id,
    get_supabase_client,
    update_verification_session,
)
from app.eligibility.voice.extract import extract_fields_from_transcript
from app.eligibility.voice.reconcile import complete_voice_session_reconciliation
from app.eligibility.voice.worker import process_call_completion, voice_webhook_url
from app.security.phi import scrub_for_log

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/eligibility/voice", tags=["voice-verification"])


async def _validate_twilio_signature(request: Request, form: dict[str, str], settings: EligibilitySettings) -> bool:
    token = (settings.twilio_auth_token or "").strip()
    if not token:
        return True
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        return True

    signature = request.headers.get("X-Twilio-Signature") or ""
    validator = RequestValidator(token)
    return validator.validate(str(request.url), form, signature)


def _session_context(session: dict[str, Any], settings: EligibilitySettings) -> dict[str, Any]:
    supabase = get_supabase_client(settings)
    check = get_eligibility_check_by_id(
        supabase, UUID(str(session["eligibility_check_id"]))
    )
    request_row = None
    if session.get("request_id"):
        request_row = fetch_eligibility_request(supabase, session["request_id"])

    member_id = ""
    dob = ""
    if request_row:
        member_id = str(request_row.get("subscriber_id") or "")
        dob = str(request_row.get("dob") or "")

    session_id = str(session["id"])
    return {
        "member_id": member_id,
        "dob": dob,
        "targets": ", ".join(session.get("missing_fields_target") or []),
        "cdt_codes": ", ".join(session.get("cdt_codes") or []),
        "npi": settings.provider_npi,
        "gather_url": voice_webhook_url(settings, f"twiml/{session_id}/gather"),
        "is_active": check.get("is_active") if check else None,
    }


def _build_gather_twiml(ctx: dict[str, Any]) -> str:
    member_tail = ctx["member_id"][-4:] if ctx["member_id"] else "unknown"
    prompt = (
        f"Hello, this is an automated eligibility verification call from a dental office. "
        f"Provider N P I {ctx['npi']}. "
        f"I need to verify benefits for member I D ending in {member_tail}, "
        f"date of birth {ctx['dob']}. "
        f"Please confirm: {ctx['targets']}. "
        f"For procedures {ctx['cdt_codes'] or 'general dental'}. "
        f"Please state remaining deductible, annual maximum, coverage status, and your call reference number."
    )
    prompt_xml = escape(prompt)
    gather_url = escape(ctx["gather_url"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">{prompt_xml}</Say>
  <Gather input="speech" timeout="120" speechTimeout="auto" action="{gather_url}" method="POST">
    <Say voice="Polly.Joanna">Please provide the requested benefit information after the tone.</Say>
  </Gather>
  <Say voice="Polly.Joanna">We did not receive a response. Goodbye.</Say>
</Response>"""


@router.post("/twiml/{session_id}")
async def voice_twiml(session_id: UUID) -> Response:
    settings = get_settings()
    supabase = get_supabase_client(settings)
    session = fetch_session_by_id(supabase, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")

    ctx = _session_context(session, settings)
    twiml = _build_gather_twiml(ctx)
    return Response(content=twiml, media_type="application/xml")


@router.post("/twiml/{session_id}/gather")
async def voice_twiml_gather(
    session_id: UUID,
    request: Request,
    SpeechResult: str | None = Form(default=None),
    CallDuration: str | None = Form(default=None),
) -> Response:
    settings = get_settings()
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not await _validate_twilio_signature(request, form, settings):
        raise HTTPException(status_code=403, detail="invalid_twilio_signature")

    duration: int | None = None
    if CallDuration:
        try:
            duration = int(CallDuration)
        except ValueError:
            duration = None

    try:
        process_call_completion(
            str(session_id),
            call_duration=duration,
            speech_result=SpeechResult or form.get("SpeechResult"),
            settings=settings,
        )
    except Exception as exc:
        logger.exception("voice gather processing failed session=%s", session_id)
        msg = escape(scrub_for_log(str(exc))[:120])
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response><Say voice="Polly.Joanna">Verification recorded with errors. {msg}</Say></Response>"""
        return Response(content=twiml, media_type="application/xml")

    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say voice="Polly.Joanna">Thank you. Your responses have been recorded. Goodbye.</Say></Response>"""
    return Response(content=twiml, media_type="application/xml")


def _bland_duration_seconds(body: dict[str, Any]) -> int | None:
    corrected = body.get("corrected_duration")
    if corrected is not None:
        try:
            return int(float(corrected))
        except (TypeError, ValueError):
            pass
    call_length = body.get("call_length")
    if call_length is not None:
        try:
            return int(float(call_length) * 60)
        except (TypeError, ValueError):
            pass
    return None


@router.post("/bland/{session_id}")
async def voice_bland_webhook(session_id: UUID, request: Request) -> dict[str, str]:
    """Post-call webhook from Bland.ai: ingest transcript + analysis, reconcile, pending_review."""
    settings = get_settings()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    # Ignore mid-call streaming events (they carry a 'category' and no transcript).
    if body.get("category") and not body.get("concatenated_transcript"):
        return {"ok": "true", "ignored": "event"}

    supabase = get_supabase_client(settings)
    session = fetch_session_by_id(supabase, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session_not_found")

    duration = _bland_duration_seconds(body)
    if duration is not None:
        update_verification_session(
            supabase, session_id, {"call_duration_seconds": duration}
        )

    transcript = str(body.get("concatenated_transcript") or "").strip()
    # Pathway extractVars (best-effort) as a base; call-level analysis_schema overrides.
    pathway_vars = body.get("variables") if isinstance(body.get("variables"), dict) else None
    extracted = {
        **map_bland_variables_to_extracted(pathway_vars),
        **map_bland_analysis_to_extracted(body.get("analysis")),
    }
    if not extracted and transcript:
        extracted = extract_fields_from_transcript(
            transcript,
            missing_fields_target=list(session.get("missing_fields_target") or []),
            cdt_codes=list(session.get("cdt_codes") or []),
            settings=settings,
        )

    if not transcript and not extracted:
        update_verification_session(
            supabase,
            session_id,
            {
                "status": "failed",
                "failure_code": "empty_transcript",
                "failure_message": "Bland call returned no transcript or analysis",
            },
        )
        return {"ok": "true", "status": "failed"}

    if not transcript:
        transcript = str(body.get("summary") or "(structured analysis only; no transcript)")

    try:
        result = complete_voice_session_reconciliation(
            session_id,
            transcript=transcript,
            extracted=extracted,
            settings=settings,
        )
    except Exception as exc:
        logger.exception("bland webhook reconciliation failed session=%s", session_id)
        update_verification_session(
            supabase,
            session_id,
            {
                "status": "failed",
                "failure_code": "reconcile_failed",
                "failure_message": scrub_for_log(str(exc))[:500],
            },
        )
        return {"ok": "false", "status": "failed"}

    return {"ok": "true", "status": str(result.get("status", "pending_review"))}


@router.post("/status/{session_id}")
async def voice_status(
    session_id: UUID,
    request: Request,
    CallStatus: str | None = Form(default=None),
) -> dict[str, str]:
    settings = get_settings()
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not await _validate_twilio_signature(request, form, settings):
        raise HTTPException(status_code=403, detail="invalid_twilio_signature")

    if CallStatus in ("failed", "busy", "no-answer", "canceled"):
        supabase = get_supabase_client(settings)
        update_verification_session(
            supabase,
            session_id,
            {
                "status": "failed",
                "failure_code": CallStatus or "call_failed",
                "failure_message": f"Twilio call status: {CallStatus}",
            },
        )
    return {"ok": "true"}
