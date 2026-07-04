"""Bland.ai conversational outbound call provider for payer voice verification.

This replaces the scripted Twilio TwiML call layer with a fully conversational
agent (Bland handles LLM + voice + telephony + interruption/turn-taking). The
surrounding pipeline (gate -> queue -> reconcile -> pending_review HITL) is
unchanged; Bland posts the transcript and structured ``analysis`` to our webhook
when the call ends.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.eligibility.config import EligibilitySettings
from app.eligibility.voice.gate import format_missing_fields_for_voice
from app.eligibility.voice.db import (
    fetch_eligibility_request,
    fetch_payer_voice_config,
    get_supabase_client,
)

logger = logging.getLogger(__name__)

# Structured fields Bland extracts post-call and returns under ``analysis``.
BLAND_ANALYSIS_SCHEMA: dict[str, str] = {
    "member_active": "boolean",
    "coverage_active": "boolean",
    "deductible_remaining": "number",
    "annual_max_remaining": "number",
    "coverage_percent": "number",
    "copay": "number",
    "coinsurance": "number",
    "call_reference": "string",
    "representative_name": "string",
    "notes": "string",
}


def bland_configured(settings: EligibilitySettings) -> bool:
    return bool(
        (settings.bland_api_key or "").strip()
        and (settings.twilio_webhook_base_url or "").strip()
    )


def _build_task_prompt(ctx: dict[str, Any]) -> str:
    member_tail = ctx["member_id"][-4:] if ctx["member_id"] else "unknown"
    targets = ctx["targets"] or "remaining deductible, annual maximum, and coverage status"
    procedures = ctx["cdt_codes"] or "general dental services"
    return (
        "You are a friendly, professional dental office benefits coordinator making an "
        "outbound phone call to a dental insurance payer to verify a patient's eligibility "
        "and benefits. Speak naturally and conversationally, ask one question at a time, and "
        "wait for the representative to finish answering before moving on.\n\n"
        "If you reach an automated phone menu (IVR), listen and navigate it to reach a live "
        "benefits or eligibility representative. If asked who is calling, you are calling on "
        f"behalf of a dental provider with N P I {ctx['npi']}.\n\n"
        "Patient and member details to provide when asked:\n"
        f"- Member I D ending in {member_tail}\n"
        f"- Date of birth: {ctx['dob'] or 'available on request'}\n"
        f"- Payer: {ctx['payer_name']}\n\n"
        "Your goal is to confirm the following, getting exact dollar amounts where applicable:\n"
        f"- {targets}\n"
        f"- Whether these procedures are covered: {procedures}\n\n"
        "Specifically find out: is the member active, is coverage active, the remaining "
        "deductible, the remaining annual maximum, the coverage percentage for the procedures, "
        "and any copay or coinsurance. Before ending the call, always ask for and confirm the "
        "call reference number and the representative's name.\n\n"
        "Be polite and concise. Confirm important numbers by repeating them back. Once you have "
        "the information, thank the representative and end the call."
    )


# Demo fallbacks used only when a session has no linked eligibility request
# (e.g. synthetic test sessions) so the call still has coherent values to read out.
_DEMO_PATIENT_NAME = "Jane Sample"
_DEMO_MEMBER_ID = "W123456789"
_DEMO_DOB = "01/01/1985"


def _bland_context(
    session: dict[str, Any],
    settings: EligibilitySettings,
    payer_cfg: dict[str, Any],
) -> dict[str, Any]:
    supabase = get_supabase_client(settings)
    member_id = ""
    dob = ""
    patient_name = ""
    group_number = ""
    provider_name = settings.provider_name
    req_id = session.get("request_id")
    if req_id:
        request_row = fetch_eligibility_request(supabase, req_id)
        if request_row:
            member_id = str(request_row.get("subscriber_id") or "")
            dob = str(request_row.get("dob") or "")
            first = str(request_row.get("first_name") or "").strip()
            last = str(request_row.get("last_name") or "").strip()
            patient_name = (first + " " + last).strip()
            group_number = str(request_row.get("plan_id") or "")
            provider_name = str(request_row.get("provider_name") or provider_name)

    targets = list(session.get("missing_fields_target") or [])
    cdt_codes = list(session.get("cdt_codes") or [])
    requested_benefits = format_missing_fields_for_voice(targets, cdt_codes=cdt_codes)

    return {
        "member_id": member_id,
        "dob": dob,
        "patient_name": patient_name,
        "group_number": group_number,
        "provider_name": provider_name,
        "requested_benefits": requested_benefits,
        "npi": settings.provider_npi,
        "tin": settings.provider_tax_id,
        "payer_name": str(
            payer_cfg.get("display_name") or payer_cfg.get("payer_id") or "the payer"
        ),
    }


def _pathway_request_data(ctx: dict[str, Any]) -> dict[str, Any]:
    """Variables exposed to the Bland Pathway (referenced as {{member_id}} etc.).

    Keys match the pathway's variable names exactly.
    """
    return {
        "provider_npi": ctx["npi"],
        "provider_tin": ctx["tin"],
        "provider_name": ctx["provider_name"],
        "office_name": ctx["provider_name"],
        "patient_name": ctx["patient_name"] or _DEMO_PATIENT_NAME,
        "patient_dob": ctx["dob"] or _DEMO_DOB,
        "member_id": ctx["member_id"] or _DEMO_MEMBER_ID,
        "group_number": ctx["group_number"],
        "payer_name": ctx["payer_name"],
        "requested_benefits": ctx["requested_benefits"]
        or format_missing_fields_for_voice(
            ["annual_max_remaining", "deductible_remaining"],
        ),
    }


def initiate_bland_call(
    session: dict[str, Any],
    settings: EligibilitySettings,
    *,
    webhook_url: str,
) -> str:
    """Place a conversational Bland.ai outbound call; returns the Bland call_id."""
    supabase = get_supabase_client(settings)
    payer_cfg = fetch_payer_voice_config(supabase, str(session.get("payer_id") or ""))
    if not payer_cfg or not payer_cfg.get("eligibility_phone"):
        raise RuntimeError("payer_phone_missing")

    session_id = str(session["id"])
    ctx = _bland_context(session, settings, payer_cfg)
    to_number = str(payer_cfg["eligibility_phone"])
    pathway_id = (getattr(settings, "bland_pathway_id", "") or "").strip()

    payload: dict[str, Any] = {
        "phone_number": to_number,
        "webhook": webhook_url,
        "metadata": {"session_id": session_id},
        "analysis_schema": BLAND_ANALYSIS_SCHEMA,
        "wait_for_greeting": True,
        "record": bool(getattr(settings, "bland_record", False)),
    }

    if pathway_id:
        # Pathway mode: Bland runs the visual flow; we feed patient data as variables.
        # A pathway overrides task/model/first_sentence, so we do not send those.
        payload["pathway_id"] = pathway_id
        payload["request_data"] = _pathway_request_data(ctx)
        version = (getattr(settings, "bland_pathway_version", "") or "").strip()
        if version:
            payload["pathway_version"] = version
    else:
        # Prompt mode: inline conversational task prompt.
        payload["task"] = _build_task_prompt(ctx)
        model = (settings.bland_model or "").strip()
        if model:
            payload["model"] = model

    voice = (settings.bland_voice or "").strip()
    if voice:
        payload["voice"] = voice

    headers = {
        "authorization": settings.bland_api_key.strip(),
        "Content-Type": "application/json",
    }
    api_url = f"{settings.bland_base_url.rstrip('/')}/v1/calls"

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(api_url, json=payload, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    if isinstance(body, dict) and str(body.get("status")).lower() == "error":
        raise RuntimeError(f"bland_call_error: {body.get('message') or body}")
    call_id = None
    if isinstance(body, dict):
        call_id = body.get("call_id") or body.get("c_id")
    if not call_id:
        raise RuntimeError(f"bland_call_missing_id: {body}")
    return str(call_id)


def _coerce_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "active", "covered", "in network", "in-network"):
            return True
        if lowered in ("false", "no", "inactive", "not covered", "out of network"):
            return False
    return None


def map_bland_analysis_to_extracted(analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Translate Bland ``analysis_schema`` output into the canonical extracted shape."""
    out: dict[str, Any] = {}
    if not isinstance(analysis, dict):
        return out

    active = _coerce_bool(analysis.get("member_active"))
    if active is not None:
        out["is_active"] = active
    covered = _coerce_bool(analysis.get("coverage_active"))
    if covered is not None:
        out["is_covered"] = covered

    for key in (
        "deductible_remaining",
        "annual_max_remaining",
        "coverage_percent",
        "copay",
        "coinsurance",
    ):
        num = _coerce_number(analysis.get(key))
        if num is not None:
            out[key] = num

    ref = analysis.get("call_reference")
    if ref:
        out["call_reference"] = str(ref)
    rep = analysis.get("representative_name")
    if rep:
        out["rep_name"] = str(rep)
    return out


def _parse_money(value: Any) -> float | None:
    if value is None:
        return None
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def map_bland_variables_to_extracted(variables: dict[str, Any] | None) -> dict[str, Any]:
    """Map the Pathway's own extractVars (string dollar amounts) to canonical fields.

    Used as a fallback when the call-level analysis_schema did not populate values.
    """
    out: dict[str, Any] = {}
    if not isinstance(variables, dict):
        return out

    active = _coerce_bool(variables.get("coverage_active"))
    if active is not None:
        out["is_active"] = active
        out["is_covered"] = active

    annual_remaining = _parse_money(variables.get("annual_maximum_remaining"))
    if annual_remaining is not None:
        out["annual_max_remaining"] = annual_remaining

    ded_individual = _parse_money(variables.get("deductible_individual"))
    ded_met = _parse_money(variables.get("deductible_met"))
    if ded_individual is not None and ded_met is not None:
        out["deductible_remaining"] = max(ded_individual - ded_met, 0.0)
    elif ded_individual is not None:
        out["deductible_remaining"] = ded_individual

    return out
