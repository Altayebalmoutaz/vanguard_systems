"""Diff + confidence gating for Open Dental benefit-grid write-back (Track C).

Compares proposed benefit values to existing OD rows and classifies each change as:
  - auto: safe to write (exact match, create, or small delta)
  - review: material delta — queue / skip unless explicitly forced
  - block: do not write (caller may still use BenefitGridGuard for human edits)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Disposition = Literal["auto", "review", "block", "unchanged"]

# Defaults aligned with the OD VOB writeback plan.
DEFAULT_MAX_COINSURANCE_DELTA_PTS = 5
DEFAULT_MAX_MONETARY_DELTA = 100.0


@dataclass(frozen=True)
class FieldChangeDecision:
    disposition: Disposition
    reason: str
    field: str
    previous: Any = None
    proposed: Any = None
    delta: float | None = None


def classify_coinsurance_change(
    *,
    previous: int | None,
    proposed: int,
    max_delta_pts: int = DEFAULT_MAX_COINSURANCE_DELTA_PTS,
) -> FieldChangeDecision:
    if previous is None:
        return FieldChangeDecision(
            disposition="auto",
            reason="create",
            field="CoInsurance.Percent",
            proposed=proposed,
        )
    if previous == proposed:
        return FieldChangeDecision(
            disposition="unchanged",
            reason="exact_match",
            field="CoInsurance.Percent",
            previous=previous,
            proposed=proposed,
            delta=0.0,
        )
    delta = float(abs(previous - proposed))
    if delta <= max_delta_pts:
        return FieldChangeDecision(
            disposition="auto",
            reason="small_coinsurance_delta",
            field="CoInsurance.Percent",
            previous=previous,
            proposed=proposed,
            delta=delta,
        )
    return FieldChangeDecision(
        disposition="review",
        reason="large_coinsurance_delta",
        field="CoInsurance.Percent",
        previous=previous,
        proposed=proposed,
        delta=delta,
    )


def classify_monetary_change(
    *,
    previous: float | None,
    proposed: float,
    field: str,
    max_delta: float = DEFAULT_MAX_MONETARY_DELTA,
) -> FieldChangeDecision:
    if previous is None:
        return FieldChangeDecision(
            disposition="auto",
            reason="create",
            field=field,
            proposed=proposed,
        )
    if float(previous) == float(proposed):
        return FieldChangeDecision(
            disposition="unchanged",
            reason="exact_match",
            field=field,
            previous=previous,
            proposed=proposed,
            delta=0.0,
        )
    delta = abs(float(previous) - float(proposed))
    if delta <= max_delta:
        return FieldChangeDecision(
            disposition="auto",
            reason="small_monetary_delta",
            field=field,
            previous=previous,
            proposed=proposed,
            delta=delta,
        )
    return FieldChangeDecision(
        disposition="review",
        reason="large_monetary_delta",
        field=field,
        previous=previous,
        proposed=proposed,
        delta=delta,
    )


def classify_quantity_change(
    *,
    previous: int | None,
    proposed: int,
    field: str,
) -> FieldChangeDecision:
    if previous is None:
        return FieldChangeDecision(
            disposition="auto",
            reason="create",
            field=field,
            proposed=proposed,
        )
    if previous == proposed:
        return FieldChangeDecision(
            disposition="unchanged",
            reason="exact_match",
            field=field,
            previous=previous,
            proposed=proposed,
            delta=0.0,
        )
    # Quantity / waiting months are discrete plan rules — any change needs review.
    return FieldChangeDecision(
        disposition="review",
        reason="quantity_changed",
        field=field,
        previous=previous,
        proposed=proposed,
        delta=float(abs(previous - proposed)),
    )


def summarize_dispositions(actions: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"auto": 0, "review": 0, "block": 0, "unchanged": 0, "other": 0}
    for action in actions:
        act = str(action.get("action") or "")
        disposition = str(action.get("disposition") or "")
        # Prefer concrete action labels (proposed_* / skipped_*) over disposition.
        key = act or disposition or "other"
        if key in ("created", "updated", "proposed_create", "proposed_update") or (
            disposition == "auto" and act in ("", "auto")
        ):
            summary["auto"] += 1
        elif key in ("skipped_needs_review", "review") or disposition == "review":
            summary["review"] += 1
        elif key in ("skipped_human_edit", "block", "skipped_blocked"):
            summary["block"] += 1
        elif key in (
            "unchanged",
            "skipped_dry_run_unchanged",
            "skipped_inactive",
            "present_inactive_flag",
        ):
            summary["unchanged"] += 1
        elif key.startswith("skipped_"):
            summary["other"] += 1
        else:
            summary["other"] += 1
    return summary
