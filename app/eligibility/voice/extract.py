"""Extract structured eligibility fields from voice call transcripts."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.eligibility.config import EligibilitySettings

logger = logging.getLogger(__name__)

EXTRACTION_SCHEMA_HINT = """
Return a single JSON object with optional keys:
is_active (bool), is_covered (bool), deductible_remaining (number), annual_max_remaining (number),
coverage_percent (number), copay (number), coinsurance (number),
call_reference (string), rep_name (string),
procedure_details (array of {cdt_code, procedure_covered (bool)}).
Use null for unknown fields. Do not invent values not stated on the call.
"""


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("extraction must be a JSON object")
    return data


def extract_fields_from_transcript(
    transcript: str,
    *,
    missing_fields_target: list[str],
    cdt_codes: list[str],
    settings: EligibilitySettings,
) -> dict[str, Any]:
    """
    Post-call structured extraction. Uses OpenAI chat when configured; otherwise
    returns a deterministic stub for demo/dev (empty extraction).
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return {}

    api_key = (getattr(settings, "voice_openai_api_key", "") or "").strip()
    if not api_key:
        logger.warning("voice extraction skipped: VOICE_OPENAI_API_KEY not set")
        return _demo_extraction_stub(transcript, missing_fields_target, cdt_codes)

    model = getattr(settings, "voice_openai_model", "gpt-4o-mini")
    prompt = (
        "Extract dental eligibility benefit facts from this payer phone call transcript.\n"
        f"Target missing fields: {missing_fields_target}\n"
        f"Procedure codes of interest: {cdt_codes}\n"
        f"{EXTRACTION_SCHEMA_HINT}\n\nTranscript:\n{transcript}"
    )
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You extract structured eligibility data."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            return _parse_json_object(content)
    except Exception as exc:
        logger.warning("voice LLM extraction failed: %s", exc)
        return _demo_extraction_stub(transcript, missing_fields_target, cdt_codes)


def _demo_extraction_stub(
    transcript: str,
    missing_fields_target: list[str],
    cdt_codes: list[str],
) -> dict[str, Any]:
    """Heuristic stub when no LLM key — supports demo transcripts with explicit values."""
    lower = transcript.lower()
    out: dict[str, Any] = {}
    ref_match = re.search(r"reference(?:\s+number)?[:\s]+([A-Z0-9-]+)", transcript, re.I)
    if ref_match:
        out["call_reference"] = ref_match.group(1)

    if "active" in lower and "inactive" not in lower:
        out["is_active"] = True
    if "deductible_remaining" in missing_fields_target or "deductible" in lower:
        m = re.search(r"deductible.*?(\d+(?:\.\d+)?)", lower)
        if m:
            out["deductible_remaining"] = float(m.group(1))
    if "annual_max_remaining" in missing_fields_target or "annual max" in lower:
        m = re.search(r"annual max.*?(\d+(?:\.\d+)?)", lower)
        if m:
            out["annual_max_remaining"] = float(m.group(1))
    if "is_covered" in missing_fields_target or "covered" in lower:
        out["is_covered"] = "not covered" not in lower

    proc_details: list[dict[str, Any]] = []
    for code in cdt_codes:
        if code.lower() in lower:
            proc_details.append({"cdt_code": code, "procedure_covered": "not covered" not in lower})
    if proc_details:
        out["procedure_details"] = proc_details
    return out
