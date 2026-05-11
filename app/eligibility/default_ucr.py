"""Illustrative CDT billed amounts for UCR fallback when DB fee schedules are empty."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Round-number placeholders — not a fee schedule guarantee; replace via DB or ELIGIBILITY_UCR_FALLBACK_JSON.
BUILTIN_UCR_FALLBACK: dict[str, float] = {
    "D0120": 95.0,
    "D0140": 85.0,
    "D0150": 120.0,
    "D0210": 45.0,
    "D0220": 35.0,
    "D0270": 30.0,
    "D1110": 165.0,
    "D1120": 130.0,
    "D1206": 95.0,
    "D1208": 95.0,
    "D2330": 210.0,
    "D2740": 1100.0,
    "D2750": 1150.0,
    "D2790": 1050.0,
    "D4341": 220.0,
    "D4910": 185.0,
}


def parse_ucr_fallback_json(raw: str) -> dict[str, float]:
    """Merge JSON object of CDT -> number into overrides; invalid input ignored."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("ELIGIBILITY_UCR_FALLBACK_JSON parse failed: %s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        ks = str(k).strip().upper()
        if not ks:
            continue
        try:
            out[ks] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def merged_ucr_defaults(settings: Any) -> dict[str, float]:
    """Builtin map overridden by settings eligibility_ucr_fallback_json."""
    base = dict(BUILTIN_UCR_FALLBACK)
    extra = getattr(settings, "eligibility_ucr_fallback_json", None) or ""
    base.update(parse_ucr_fallback_json(str(extra)))
    return base
