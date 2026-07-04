"""
LLM layer for prior authorization — structured JSON only.

Separate from tools and agent merge logic. On failure, the agent uses rule-based fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings
from app.llm.client import openrouter_chat_completion
from app.security.phi import scrub_for_llm

SYSTEM_PROMPT = """You are a US dental revenue-cycle prior authorization analyst.
Given CDT codes, ICD-10 codes, payer name, and optional clinical context, return ONLY valid JSON (no markdown) with exactly these keys:
- "requires_auth": boolean — true if prior authorization is likely required before treatment
- "required_documents": array of strings — documents the office should gather (e.g. X-rays, narratives)
- "payer_rules": array of strings — short payer-specific considerations
- "risk_level": one of "low", "medium", "high" — estimated denial / administrative risk if submitted without proper auth/docs
- "risk_reason": one short sentence explaining the risk_level

Be conservative: when major restorative, surgical, or endodontic codes appear, bias toward requires_auth true and higher risk unless clearly preventive only.
Do not include any keys other than the five above."""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _normalize_risk(value: str) -> str:
    v = str(value).lower().strip()
    if v in ("low", "medium", "high"):
        return v
    return "medium"


def llm_prior_auth_decision(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    """
    Call OpenRouter; return parsed dict with requires_auth, required_documents, payer_rules, risk_level, risk_reason.

    Raises on missing API key, HTTP errors, or malformed JSON / missing keys.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    user_content = json.dumps(scrub_for_llm(data), indent=2, default=str)

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    data_out = openrouter_chat_completion(
        api_key=settings.openrouter_api_key,
        payload=payload,
        http_referer=settings.openrouter_http_referer or "https://localhost",
        app_name=settings.app_name,
        timeout_seconds=settings.openrouter_timeout_seconds,
        max_retries=settings.openrouter_max_retries,
    )

    content = data_out["choices"][0]["message"]["content"]
    raw = _strip_json_fence(content)
    parsed = json.loads(raw)

    for key in ("requires_auth", "required_documents", "payer_rules", "risk_level", "risk_reason"):
        if key not in parsed:
            raise RuntimeError(f"Prior auth LLM JSON missing key: {key}")

    parsed["requires_auth"] = bool(parsed["requires_auth"])
    parsed["required_documents"] = [
        str(x).strip() for x in parsed["required_documents"] if str(x).strip()
    ]
    parsed["payer_rules"] = [str(x).strip() for x in parsed["payer_rules"] if str(x).strip()]
    parsed["risk_level"] = _normalize_risk(parsed["risk_level"])
    parsed["risk_reason"] = str(parsed["risk_reason"]).strip()
    return parsed
