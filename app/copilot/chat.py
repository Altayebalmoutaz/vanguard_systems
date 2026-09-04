"""Bounded tool-calling loop for the read-only OpenDental copilot."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.config import Settings
from app.copilot.patients import stub_profile_from_opendental
from app.copilot.tools import TOOL_SPECS, ToolContext, UnknownCopilotToolError, execute_tool
from app.dashboard.store import (
    DashboardPatientNotFoundError,
    get_patient_360,
    get_patient_copilot_anchor,
    get_patient_eligibility_profile,
)
from app.eligibility.config import get_settings as get_elig_settings
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.connections_store import get_connection
from app.integrations.opendental.errors import OpenDentalConfigError
from app.llm.client import openrouter_chat_completion
from app.security.phi import PhiScrubError, scrub_for_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are SmileSuites Copilot, a friendly read-only assistant for dental office staff.
Talk like a helpful teammate: warm, clear, and in plain language. Use short paragraphs.
Answer questions about ONE anchored patient using only tool results.
You cannot write to OpenDental or Vanguard. Do not invent coverage, payments, or codes.
Decoded status fields (appointment status, claimproc status, problem status) come
straight from OpenDental — report them as returned, do not invent or remap them.
You can read OpenDental appointments, treatment plans, account ledger (balance,
aging, payments, adjustments), claim procedures, recalls, commlogs, document
metadata, referrals, statements, family members, health history (medications,
allergies, problems), perio exam headers, and clinical procedure notes, plus
Vanguard eligibility and CARC policy.
When you use a fact, name its source in everyday words (OpenDental chart,
Vanguard eligibility, CARC policy).
If a tool returns an error or empty data, say so instead of guessing.
Keep answers tight unless they ask for more. Offer a natural follow-up when it
helps, not a canned "how else can I help." """


@dataclass(frozen=True)
class CopilotReply:
    reply: str
    tool_trace: list[dict[str, Any]]
    model: str


class CopilotConfigError(RuntimeError):
    """Copilot cannot run (missing LLM key or disabled)."""


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be a JSON object")
    return parsed


def _resolve_od_client(settings: Settings, *, practice_id: str) -> OpenDentalClient | None:
    connection = get_connection(settings, practice_id=practice_id)
    if not connection:
        return None
    try:
        return OpenDentalClient.from_connection(connection, settings=get_elig_settings())
    except OpenDentalConfigError:
        logger.warning("copilot: OpenDental connection present but not usable")
        return None


def run_copilot_chat(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID,
    messages: list[dict[str, str]],
    profile: dict[str, Any] | None = None,
    od_client: OpenDentalClient | None = None,
    od_pat_num: int | None = None,
) -> CopilotReply:
    if not settings.openrouter_api_key:
        raise CopilotConfigError("OPENROUTER_API_KEY is not set")

    resolved_profile = profile
    resolved_pat = od_pat_num
    resolved_client = od_client
    if resolved_client is None:
        try:
            resolved_client = _resolve_od_client(settings, practice_id=practice_id)
        except Exception:
            logger.warning("copilot: failed to resolve OpenDental client", exc_info=True)
            resolved_client = None

    if resolved_profile is None:
        try:
            resolved_profile = get_patient_360(
                settings,
                practice_id=practice_id,
                patient_id=patient_id,
                performed_by="copilot",
            )
            if resolved_pat is None:
                anchor = get_patient_copilot_anchor(
                    settings, practice_id=practice_id, patient_id=patient_id
                )
                resolved_pat = anchor.get("od_pat_num")
        except DashboardPatientNotFoundError:
            fallback = get_patient_eligibility_profile(
                settings, practice_id=practice_id, patient_id=patient_id
            )
            if fallback is not None:
                resolved_profile = {
                    "patient": fallback["patient"],
                    "latest_eligibility_check": fallback["latest_eligibility_check"],
                    "agent_runs": fallback["agent_runs"],
                }
                if resolved_pat is None:
                    resolved_pat = fallback["od_pat_num"]
            elif resolved_pat is not None and resolved_client is not None:
                resolved_profile = stub_profile_from_opendental(
                    resolved_client,
                    patient_id=patient_id,
                    od_pat_num=int(resolved_pat),
                )
            else:
                raise

    ctx = ToolContext(
        settings=settings,
        practice_id=practice_id,
        patient_id=patient_id,
        od_pat_num=resolved_pat,
        client=resolved_client,
        profile=resolved_profile,
    )
    model = (settings.copilot_model or settings.openrouter_model).strip()
    max_iters = max(1, int(settings.copilot_max_tool_iterations))

    llm_messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in messages:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        llm_messages.append({"role": role, "content": content.strip()})
    if len(llm_messages) < 2:
        raise ValueError("copilot requires at least one user message")

    tool_trace: list[dict[str, Any]] = []
    last_text = ""

    for _ in range(max_iters):
        payload: dict[str, Any] = {
            "model": model,
            "messages": llm_messages,
            "tools": TOOL_SPECS,
            "tool_choice": "auto",
            "temperature": 0.35,
            "max_tokens": max(1, int(settings.copilot_max_tokens)),
        }
        data = openrouter_chat_completion(
            api_key=settings.openrouter_api_key,
            payload=payload,
            http_referer=settings.openrouter_http_referer or "https://localhost",
            app_name=settings.app_name,
            timeout_seconds=settings.openrouter_timeout_seconds,
            max_retries=settings.openrouter_max_retries,
        )
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            last_text = content.strip()

        if not tool_calls:
            break

        llm_messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
        )
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            raw_args = function.get("arguments") or "{}"
            try:
                args = _parse_tool_arguments(
                    raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
                )
            except (ValueError, json.JSONDecodeError):
                args = {}
            try:
                result = execute_tool(name, args, ctx=ctx)
            except UnknownCopilotToolError:
                result = {"error": "unknown_tool", "tool": name}
            if settings.copilot_scrub_phi:
                result = scrub_for_llm(result)
            tool_trace.append({"name": name, "args": args})
            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or name),
                    "content": json.dumps(result, default=str),
                }
            )
    else:
        if not last_text:
            last_text = "I reached the tool-call limit before finishing. Ask again with a narrower question."

    if not last_text:
        last_text = "I could not produce an answer from the available records."

    if settings.copilot_scrub_phi:
        last_text = scrub_for_llm(last_text)
        if not isinstance(last_text, str):
            raise PhiScrubError("PHI scrubbing failed for copilot reply")

    return CopilotReply(reply=last_text, tool_trace=tool_trace, model=model)
