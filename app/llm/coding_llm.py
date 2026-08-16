"""
LLM layer: one job — propose CDT and ICD-10 codes from the note.

Uses OpenRouter (OpenAI-compatible). Kept separate from tools and agent orchestration.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings
from app.llm.client import openrouter_chat_completion
from app.security.phi import scrub_for_llm

SYSTEM_PROMPT = """You are a dental coding assistant for US practices.
Return ONLY valid JSON (no markdown fences) with exactly these keys:
- "cdt_codes": array of CDT strings (e.g. "D0120") you recommend
- "icd10_codes": array of ICD-10-CM codes (e.g. "K02.9") linked to diagnoses in the note
- "confidence": number 0.0-1.0 for your overall confidence
- "justification": short clinical summary tying note to codes

Rules:
- Use current CDT and ICD-10-CM conventions; codes must be strings.
- If the finding does not support a code, return no codes. Do not guess.
- Do not include any key besides the four above."""

LINE_SYSTEM_PROMPT = """You are a dental coding assistant for US practices.
You receive a structured scribe encounter with one or more procedure lines.
Return ONLY valid JSON (no markdown fences) with exactly these keys:
- "recommendations": array of objects, one per input line_id, each with:
  - "line_id": string (must match an input line_id)
  - "cdt_code": string CDT (e.g. "D2392") or null if cannot code
  - "confidence": number 0.0-1.0 for that line
  - "explanation": short clinical rationale for that line
  - "icd10_codes": array of ICD-10-CM strings for that line
- "overall_confidence": number 0.0-1.0
- "justification": short summary across all lines

Rules:
- Use current CDT and ICD-10-CM conventions; codes must be strings.
- Emit exactly one recommendation object per input line_id.
- If the finding does not support a code, return null. Do not invent a procedure.
- Do not return null just because tooth numbers, surfaces, or per-line quadrant were not spoken.
- Do not invent line_ids that were not provided.
- D0150 and D0180 are mutually exclusive on the same date of service; pick one.
- D4346 is gingivitis-only scaling. Do not use it for periodontitis, SRP, or laser therapy.
- Gingival irrigation is D4921 (per quadrant), not D4999. If the line covers all quadrants, still return D4921.
- D4341 is scaling and root planing, four or more teeth per quadrant; D4342 is one to three. When a quadrant is given but no tooth list, suggest D4341.
- When a crown is documented without planned material, suggest D2740 (porcelain/ceramic) at lower confidence and note that material must be confirmed. Do not return null.
- Oral hygiene instructions are D1330; code them when the line describes OHI / oral hygiene instruction.
- Do not code PPE, pre-procedural rinse, or infection-control measures."""


VERIFY_SYSTEM_PROMPT = """You are a senior dental coding auditor for US practices.
You are given ONE procedure line, a candidate CDT code, its official description,
and any payer notes. Confirm or correct the single best CDT code for this line.
Return ONLY valid JSON (no markdown fences) with exactly these keys:
- "cdt_code": string CDT you endorse (may equal or differ from the candidate)
- "confidence": number 0.0-1.0 in the endorsed code
- "explanation": one-sentence rationale
Rules:
- Prefer the candidate unless it is clearly wrong for the documented finding.
- Respect the tooth/surface/material detail actually documented.
- If planned crown material is not documented, keep the candidate crown code rather than returning null.
- Do not invent unsupported procedures. Output only the three keys above."""


def llm_verify_line(
    settings: Settings,
    *,
    line_summary: str,
    candidate_cdt: str,
    candidate_description: str = "",
    payer_notes: str = "",
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Second-opinion pass for one high-stakes/low-confidence/conflict line.

    Returns {"cdt_code", "confidence", "explanation"}. Raises on transport/JSON
    errors so callers can degrade to the original recommendation.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    safe_line = scrub_for_llm(line_summary or "")
    safe_payer = scrub_for_llm((payer_notes or "").strip())
    user_parts = [
        f"Candidate CDT: {candidate_cdt}",
        f"Candidate description: {candidate_description or '(none)'}",
        "",
        "Procedure line:",
        safe_line,
    ]
    if safe_payer:
        user_parts += ["", "Payer notes:", safe_payer]
    user_content = "\n".join(user_parts) + "\n"

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }
    data = openrouter_chat_completion(
        api_key=settings.openrouter_api_key,
        payload=payload,
        http_referer=settings.openrouter_http_referer or "https://localhost",
        app_name=settings.app_name,
        timeout_seconds=(
            timeout_seconds if timeout_seconds is not None else settings.openrouter_timeout_seconds
        ),
        max_retries=(max_retries if max_retries is not None else settings.openrouter_max_retries),
    )
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(_strip_json_fence(content))
    code = parsed.get("cdt_code")
    try:
        conf = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "cdt_code": str(code).upper().strip() if code else None,
        "confidence": max(0.0, min(1.0, conf)),
        "explanation": str(parsed.get("explanation") or ""),
    }


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
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
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

    data = openrouter_chat_completion(
        api_key=settings.openrouter_api_key,
        payload=payload,
        http_referer=settings.openrouter_http_referer or "https://localhost",
        app_name=settings.app_name,
        timeout_seconds=(
            timeout_seconds if timeout_seconds is not None else settings.openrouter_timeout_seconds
        ),
        max_retries=(max_retries if max_retries is not None else settings.openrouter_max_retries),
    )

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


def llm_generate_line_recommendations(
    settings: Settings,
    *,
    structured_block: str,
    clinical_note: str,
    patient_age: int,
    insurance: str,
    line_ids: list[str],
    retrieval_context: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Propose one CDT recommendation per scribe procedure line_id."""
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    safe_block = scrub_for_llm(structured_block or "")
    safe_note = scrub_for_llm(clinical_note or "")
    safe_context = scrub_for_llm((retrieval_context or "").strip())
    user_parts = [
        f"Patient age: {patient_age}",
        f"Insurance: {insurance}",
        f"Required line_ids: {', '.join(line_ids)}",
        "",
    ]
    if safe_context:
        user_parts.append(safe_context)
        user_parts.append("")
    user_parts.append(safe_block)
    if safe_note:
        user_parts.append("")
        user_parts.append("Flattened clinical note:")
        user_parts.append(safe_note)
    user_content = "\n".join(user_parts) + "\n"

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": LINE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    data = openrouter_chat_completion(
        api_key=settings.openrouter_api_key,
        payload=payload,
        http_referer=settings.openrouter_http_referer or "https://localhost",
        app_name=settings.app_name,
        timeout_seconds=(
            timeout_seconds if timeout_seconds is not None else settings.openrouter_timeout_seconds
        ),
        max_retries=(max_retries if max_retries is not None else settings.openrouter_max_retries),
    )

    content = data["choices"][0]["message"]["content"]
    raw = _strip_json_fence(content)
    parsed = json.loads(raw)
    if "recommendations" not in parsed:
        raise RuntimeError("LLM JSON missing key: recommendations")

    recs_raw = parsed.get("recommendations") or []
    if not isinstance(recs_raw, list):
        raise RuntimeError("LLM recommendations must be an array")

    wanted = set(line_ids)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in recs_raw:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("line_id") or "").strip()
        if not line_id or line_id not in wanted or line_id in seen:
            continue
        seen.add(line_id)
        cdt = item.get("cdt_code")
        cdt_norm = str(cdt).upper().strip() if cdt not in (None, "") else None
        icd_raw = item.get("icd10_codes") or []
        if not isinstance(icd_raw, list):
            icd_raw = []
        try:
            conf = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        normalized.append(
            {
                "line_id": line_id,
                "cdt_code": cdt_norm,
                "confidence": conf,
                "explanation": str(item.get("explanation") or ""),
                "icd10_codes": [str(x).upper().strip() for x in icd_raw if str(x).strip()],
            }
        )

    have = {r["line_id"] for r in normalized}
    for lid in line_ids:
        if lid not in have:
            normalized.append(
                {
                    "line_id": lid,
                    "cdt_code": None,
                    "confidence": 0.0,
                    "explanation": "No recommendation returned for this line",
                    "icd10_codes": [],
                }
            )

    try:
        overall = float(parsed.get("overall_confidence") or 0.0)
    except (TypeError, ValueError):
        overall = 0.0
    if not overall and normalized:
        overall = sum(float(r["confidence"]) for r in normalized) / len(normalized)

    return {
        "recommendations": normalized,
        "overall_confidence": max(0.0, min(1.0, overall)),
        "justification": str(parsed.get("justification") or ""),
    }
