"""
LLM intelligence layer for denial triage.

This module interprets denial context and proposes structured signals.
Deterministic rules remain authoritative in the agent.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.security.phi import scrub_for_llm

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a US dental RCM denial triage analyst.
Given ERA status/reason and claim context, return ONLY valid JSON with exactly these keys:
- "reason_token": short snake_case token (e.g. missing_xray, invalid_code, not_covered, frequency_limit, unspecified_denial)
- "suggested_next_action": short snake_case action string
- "required_evidence": array of strings listing documents/evidence to gather
- "confidence": number from 0.0 to 1.0
- "reasoning_summary": one short sentence explaining your interpretation

Rules:
- Keep tokens concise and machine-friendly.
- Do not include any keys besides the five above.
- Prefer conservative interpretation when evidence is ambiguous."""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def llm_denial_intelligence(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """
    Call OpenRouter; parse structured denial intelligence output.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    safe_data = scrub_for_llm(data)
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(safe_data, indent=2, default=str)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_http_referer or "https://localhost",
        "X-Title": settings.app_name,
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post(OPENROUTER_URL, headers=headers, json=payload)
        response.raise_for_status()
        data_out = response.json()

    content = data_out["choices"][0]["message"]["content"]
    raw = _strip_json_fence(content)
    parsed = json.loads(raw)

    for key in (
        "reason_token",
        "suggested_next_action",
        "required_evidence",
        "confidence",
        "reasoning_summary",
    ):
        if key not in parsed:
            raise RuntimeError(f"Denial LLM JSON missing key: {key}")

    parsed["reason_token"] = str(parsed["reason_token"]).strip().lower()
    parsed["suggested_next_action"] = str(parsed["suggested_next_action"]).strip().lower()
    parsed["required_evidence"] = [str(x).strip() for x in parsed["required_evidence"] if str(x).strip()]
    parsed["confidence"] = max(0.0, min(1.0, float(parsed["confidence"])))
    parsed["reasoning_summary"] = str(parsed["reasoning_summary"]).strip()
    return parsed
