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
from app.eligibility.voice.db import (
    fetch_eligibility_request,
    fetch_payer_voice_config,
    get_supabase_client,
)
from app.eligibility.voice.gate import format_missing_fields_for_voice

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
        (settings.bland_api_key or "").strip() and (settings.twilio_webhook_base_url or "").strip()
    )


def _build_task_prompt(ctx: dict[str, Any]) -> str:
    member_tail = ctx["member_id"][-4:] if ctx["member_id"] else "unknown"
    dob = ctx["dob"] or "available on request"
    payer_name = ctx["payer_name"]
    scope = (ctx.get("requested_benefits") or "").strip()
    cdt_codes = [str(c).strip().upper() for c in (ctx.get("cdt_codes") or []) if str(c).strip()]
    procedures = ", ".join(cdt_codes) if cdt_codes else "general dental services"

    # `requested_benefits` (when present) is already scoped to the missing fields and
    # already names the CDT codes, so avoid repeating the procedures here. Only the
    # generic fallback needs to spell out procedures explicitly.
    if scope:
        scope_block = f"{scope}\n\n"
    else:
        scope_block = (
            "Confirm whether the member and coverage are active, the remaining deductible, "
            "the remaining annual maximum, the coverage percentage, and any copay or "
            "coinsurance. "
            f"Also confirm coverage for these procedures: {procedures}. "
            "Ask for exact dollar amounts and percentages where they apply.\n\n"
        )

    return (
        "# Who you are\n"
        "You are Sam, a warm, easygoing benefits coordinator at a dental office. You are "
        "calling a dental insurance payer to check a patient's benefits. You are a real "
        "person having a normal phone conversation, not a script reader.\n\n"
        "# How you talk\n"
        "- Sound relaxed and human. Use natural, everyday language and light filler words "
        "like 'sure', 'okay', 'gotcha', 'perfect', and 'thanks so much'.\n"
        "- Ask ONE question at a time, then stop and listen. Never stack multiple questions "
        "in one breath.\n"
        "- Give a short, natural acknowledgement of each answer before moving on "
        "(for example: 'okay, got it' or 'perfect, thank you').\n"
        "- Keep your turns short. Do not lecture or repeat the whole context.\n"
        "- If you get interrupted, stop talking and let them speak. Do not restart your "
        "sentence from the beginning; just pick up naturally.\n"
        "- Be patient and friendly if they need a moment or put you on hold.\n\n"
        "# Getting to a representative\n"
        "If you reach an automated menu, listen and press or say the options to reach a live "
        "benefits or eligibility representative. When a person answers, greet them warmly and "
        "let them know you're a dental office calling to verify a patient's benefits.\n\n"
        "# Patient and provider details (share when asked, one item at a time)\n"
        f"- Payer: {payer_name}\n"
        f"- Member ID ending in {member_tail} (read the digits slowly if they need the full ID)\n"
        f"- Patient date of birth: {dob}\n"
        f"- Calling on behalf of a dental provider, NPI {ctx['npi']}\n\n"
        "# What you need to find out\n"
        f"{scope_block}"
        "# Wrapping up\n"
        "- When you hear an important number, gently read it back once to confirm you have "
        "it right (for example: 'so that's one thousand five hundred remaining, correct?').\n"
        "- Before you hang up, warmly ask for a call reference number and the "
        "representative's first name.\n"
        "- Thank them genuinely and end the call politely once you have what you need."
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
        practice_id = str(session.get("practice_id") or "").strip() or None
        request_row = fetch_eligibility_request(
            supabase,
            req_id,
            practice_id=practice_id,
            settings=settings,
        )
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
        "targets": targets,
        "cdt_codes": cdt_codes,
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
    use_pathway = bool(getattr(settings, "bland_use_pathway", False)) and bool(pathway_id)

    payload: dict[str, Any] = {
        "phone_number": to_number,
        "webhook": webhook_url,
        "metadata": {"session_id": session_id},
        "analysis_schema": BLAND_ANALYSIS_SCHEMA,
        "wait_for_greeting": True,
        "record": bool(getattr(settings, "bland_record", False)),
    }

    if use_pathway:
        # Pathway mode: Bland runs the visual flow; we feed patient data as variables.
        # A pathway overrides task/model/first_sentence, so we do not send those.
        payload["pathway_id"] = pathway_id
        payload["request_data"] = _pathway_request_data(ctx)
        version = (getattr(settings, "bland_pathway_version", "") or "").strip()
        if version:
            payload["pathway_version"] = version
    else:
        # Prompt mode (pilot default): the humanized persona and real patient data live in
        # our task prompt, so the call sounds natural and uses the correct member details.
        payload["task"] = _build_task_prompt(ctx)
        model = (settings.bland_model or "").strip()
        if model:
            payload["model"] = model
        temperature = getattr(settings, "bland_temperature", None)
        if temperature is not None:
            payload["temperature"] = float(temperature)
        interruption_threshold = getattr(settings, "bland_interruption_threshold", None)
        if interruption_threshold is not None:
            payload["interruption_threshold"] = int(interruption_threshold)

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
