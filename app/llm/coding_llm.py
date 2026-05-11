"""
LLM layer: one job — propose CDT and ICD-10 codes from the note.

Uses OpenRouter (OpenAI-compatible). Kept separate from tools and agent orchestration.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.security.phi import scrub_for_llm

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a dental coding assistant for US practices.
Return ONLY valid JSON (no markdown fences) with exactly these keys:
- "cdt_codes": array of CDT strings (e.g. "D0120") you recommend
- "icd10_codes": array of ICD-10-CM codes (e.g. "K02.9") linked to diagnoses in the note
- "confidence": number 0.0-1.0 for your overall confidence
- "justification": short clinical summary tying note to codes

Rules:
- Use current CDT and ICD-10-CM conventions; codes must be strings.
- If uncertain, lower confidence and still suggest best-effort codes.
- Do not include any key besides the four above."""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def llm_generate_codes(
    settings: Settings,
    clinical_note: str,
    patient_age: int,
    insurance: str,
    *,
    retrieval_context: str | None = None,
) -> dict[str, Any]:
    """
    Call the model via OpenRouter; parse JSON object.

    Raises RuntimeError on missing key, HTTP errors, or invalid JSON.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    # Scrub the clinical note before it leaves the process — third-party LLMs are out-of-scope BAA.
    safe_note = scrub_for_llm(clinical_note or "")
    safe_context = scrub_for_llm((retrieval_context or "").strip())
    user_parts = [
        f"Patient age: {patient_age}",
        f"Insurance: {insurance}",
        "",
    ]
    if safe_context:
        user_parts.append(safe_context)
        user_parts.append("")
    user_parts.append("Clinical note:")
    user_parts.append(safe_note)
    user_content = "\n".join(user_parts) + "\n"

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
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
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    raw = _strip_json_fence(content)
    parsed = json.loads(raw)

    for key in ("cdt_codes", "icd10_codes", "confidence", "justification"):
        if key not in parsed:
            raise RuntimeError(f"LLM JSON missing key: {key}")

    # Normalize types
    parsed["cdt_codes"] = [str(x).upper().strip() for x in parsed["cdt_codes"]]
    parsed["icd10_codes"] = [str(x).upper().strip() for x in parsed["icd10_codes"]]
    parsed["confidence"] = float(parsed["confidence"])
    parsed["justification"] = str(parsed["justification"])
    return parsed
