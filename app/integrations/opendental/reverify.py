"""Continuous pre-appointment reverification helpers (Track F)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def effective_poll_window_days(
    connection: dict[str, Any],
    *,
    default_reverify_days: int = 3,
) -> int:
    """Return the appointment look-ahead window for a connection.

    Uses the connection's ``poll_window_days`` when explicitly set (>0).
    When the clinic left the default at 0 (today only), prefer the configured
    reverify window (48–72h ≈ 2–3 days) so pilots pick up next-day/next-few-day apts.
    """
    raw = connection.get("poll_window_days")
    try:
        configured = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        return configured
    # Only auto-expand when auto-poll is enabled — avoids surprising Poll-now "today" clinics.
    if connection.get("poll_enabled") and default_reverify_days > 0:
        return max(0, int(default_reverify_days))
    return max(0, configured)


def appointment_dates_in_reverify_horizon(
    *,
    today: date | None = None,
    window_days: int = 3,
) -> list[str]:
    """ISO dates from today through today+window_days (inclusive)."""
    base = today or date.today()
    days = max(0, int(window_days))
    return [(base + timedelta(days=i)).isoformat() for i in range(days + 1)]


def material_change_alert_items(
    *,
    benefits_grid: dict[str, Any] | None,
    insadjust: dict[str, Any] | None,
    insplan_drift: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build change-only staff alerts from a writeback (often dry-run) result."""
    items: list[dict[str, Any]] = []
    grid = benefits_grid or {}
    for action in grid.get("actions") or []:
        act = str(action.get("action") or "")
        if act in (
            "proposed_create",
            "proposed_update",
            "skipped_needs_review",
            "created",
            "updated",
        ):
            items.append(
                {
                    "kind": "benefit_change",
                    "severity": "review" if "review" in act else "info",
                    "target": action.get("target"),
                    "type": action.get("type"),
                    "action": act,
                    "previous": action.get("previous") or action.get("previous_percent"),
                    "proposed": action.get("proposed")
                    if action.get("proposed") is not None
                    else action.get("percent")
                    if action.get("percent") is not None
                    else action.get("amount"),
                }
            )
    if (
        isinstance(insadjust, dict)
        and insadjust.get("mode") in ("proposed", "set")
        and not insadjust.get("skipped")
    ):
        items.append(
            {
                "kind": "insadjust_change",
                "severity": "info",
                "ins_used": insadjust.get("ins_used"),
                "deductible_used": insadjust.get("deductible_used"),
                "mode": insadjust.get("mode"),
            }
        )
    for finding in insplan_drift or []:
        if finding.get("disposition") in ("review", "fill_if_blank_candidate"):
            items.append({**finding, "kind": "insplan_drift"})
    return items
