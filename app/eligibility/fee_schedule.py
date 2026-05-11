"""Merge DB fee schedules with optional UCR fallbacks for Layer 5."""

from __future__ import annotations

from typing import Any

from app.eligibility.config import EligibilitySettings
from app.eligibility.default_ucr import merged_ucr_defaults


def merge_ucr_fallback_into_fee_schedule(
    fee: dict[str, Any],
    payer_id: str,
    cdt_codes: list[str],
    settings: EligibilitySettings,
) -> None:
    """
    When enabled, fill ``billed`` / ``contracted[payer_id]`` for requested CDTs that are
    missing or zero so :func:`~app.eligibility.cost_calculator.calculate_responsibility`
    can produce non-zero illustrative estimates without manual DB seeding.
    """
    if not bool(getattr(settings, "eligibility_ucr_fallback_enabled", False)):
        return
    pid = str(payer_id or "").strip()
    defaults = merged_ucr_defaults(settings)
    if not defaults:
        return

    billed = fee.setdefault("billed", {})
    contracted_root = fee.setdefault("contracted", {})
    payer_map: dict[str, Any] = {}
    if pid:
        payer_map = contracted_root.setdefault(pid, {})

    for raw in cdt_codes:
        c = str(raw or "").strip().upper()
        if not c:
            continue
        fallback = defaults.get(c)
        if fallback is None:
            continue
        cur_billed = float(billed.get(c) or 0)
        if cur_billed > 0:
            continue
        billed[c] = fallback
        if pid:
            cur_con = float(payer_map.get(c) or 0)
            if cur_con <= 0:
                payer_map[c] = fallback
