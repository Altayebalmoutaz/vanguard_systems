"""
PHI scrubbing utilities (Presidio + regex fallback).

This is the canonical PHI redaction module for the whole backend. Three entry points:

- :func:`scrub_for_log` — redact a single string for safe ``logger.info``/``logger.error`` use.
  Degrades gracefully to regex-only if Presidio fails at runtime.
- :func:`scrub_for_llm` — redact arbitrary ``str | dict | list`` payloads before sending them to a
  third-party LLM (OpenRouter, etc.). **Fail-closed**: any scrubbing error raises
  :class:`PhiScrubError` and the LLM request must abort.
- :func:`scrub_detail_for_storage` — redact ``dict`` payloads bound for JSONB columns or in-memory
  stores. Removes a denylist of obvious identifier keys outright before falling back to text scrubs.

Presidio is loaded lazily at import time; if the analyzer stack is unavailable we use regex-only
for :func:`scrub_for_log` and :func:`scrub_for_llm` string paths.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class PhiScrubError(RuntimeError):
    """PHI scrubbing failed; the payload must not be sent to an external LLM."""


try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    _analyzer = AnalyzerEngine()
    _anonymizer = AnonymizerEngine()
    _PRESIDIO_AVAILABLE = True
except Exception:
    _analyzer = None
    _anonymizer = None
    _PRESIDIO_AVAILABLE = False
    logger.info("Presidio unavailable; PHI scrubbing falls back to regex-only.")

# Medicare Beneficiary Identifier (HHS spec); intentionally tighter than the SSN regex.
_MBI_PATTERN = re.compile(
    r"\b[1-9][AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9][0-9]-?"
    r"[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9][0-9]-?"
    r"[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9][0-9]\b",
    re.I,
)
_SSN_LIKE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")

# Keys we always strip from any storage / memory dict regardless of the value.
_BANNED_KEYS = frozenset(
    {
        "ssn",
        "mbi",
        "social_security_number",
        "subscriber_id",
        "member_id",
        "dob",
        "date_of_birth",
        "address",
        "street",
        "phone",
        "email",
    }
)


def _apply_regex_scrub(text: str) -> str:
    out = _SSN_LIKE.sub("<REDACTED_SSN>", text)
    return _MBI_PATTERN.sub("<REDACTED_MBI>", out)


def _apply_presidio_scrub(text: str) -> str:
    if not (_PRESIDIO_AVAILABLE and _analyzer and _anonymizer):
        return text
    results = _analyzer.analyze(text=text, language="en")
    if not results:
        return text
    anon = _anonymizer.anonymize(
        text=text,
        analyzer_results=results,
        operators={"DEFAULT": OperatorConfig("replace", {"new_value": "<PHI>"})},
    )
    return anon.text


def scrub_for_log(text: str) -> str:
    """Redact SSN/MBI patterns and any Presidio entities in ``text`` for logging."""
    if not text:
        return text
    out = _apply_regex_scrub(text)
    if _PRESIDIO_AVAILABLE and _analyzer and _anonymizer:
        try:
            return _apply_presidio_scrub(out)
        except Exception:
            return out
    return out


def _scrub_text_for_llm(text: str) -> str:
    if not text:
        return text
    out = _apply_regex_scrub(text)
    return _apply_presidio_scrub(out)


def scrub_for_llm(payload: Any) -> Any:
    """
    Recursively scrub a payload destined for an external LLM.

    Strings are run through regex + Presidio. Dicts and lists are walked recursively.
    Raises :class:`PhiScrubError` on any scrubbing failure — the LLM call must not proceed.
    """
    try:
        if isinstance(payload, str):
            return _scrub_text_for_llm(payload)
        if isinstance(payload, dict):
            return {k: scrub_for_llm(v) for k, v in payload.items()}
        if isinstance(payload, list):
            return [scrub_for_llm(v) for v in payload]
        if isinstance(payload, tuple):
            return tuple(scrub_for_llm(v) for v in payload)
        return payload
    except PhiScrubError:
        raise
    except Exception as exc:
        raise PhiScrubError("PHI scrubbing failed for LLM payload") from exc


def scrub_detail_for_storage(detail: dict[str, Any]) -> dict[str, Any]:
    """Remove identifier keys and scrub free-text values before persisting to storage/memory."""
    redacted: dict[str, Any] = {}
    for k, v in detail.items():
        lk = k.lower()
        if lk in _BANNED_KEYS:
            redacted[k] = "[REDACTED]"
        elif isinstance(v, dict):
            redacted[k] = scrub_detail_for_storage(v)
        elif isinstance(v, list):
            redacted[k] = [
                scrub_detail_for_storage(item)
                if isinstance(item, dict)
                else scrub_for_log(item)
                if isinstance(item, str)
                else item
                for item in v
            ]
        elif isinstance(v, str):
            redacted[k] = scrub_for_log(v)
        else:
            redacted[k] = v
    return redacted
