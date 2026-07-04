"""Shadow pilot mode helpers — read-only OD, ROI event recording."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.pilot.shadow_store import record_shadow_event


def opendental_writeback_allowed(settings: Settings | Any) -> bool:
    """True when OD write-back is enabled and shadow mode is off."""
    shadow = bool(getattr(settings, "pilot_shadow_mode", False))
    writeback = bool(getattr(settings, "opendental_writeback_enabled", False))
    return writeback and not shadow


def record_eligibility_shadow(
    settings: Settings,
    *,
    practice_id: str,
    pat_num: int,
    primary_result: dict[str, Any],
    source: str = "opendental_poller",
) -> None:
    """Log an eligibility check during shadow pilot (no OD writes)."""
    if not getattr(settings, "pilot_shadow_mode", False):
        return
    routing = (primary_result.get("routing") or {}) if isinstance(primary_result, dict) else {}
    record_shadow_event(
        settings,
        practice_id=practice_id,
        event_type="eligibility.checked",
        source=source,
        external_ref=str(pat_num),
        patient_id=primary_result.get("patient_id"),
        agent_payload={
            "routing": routing,
            "check_id": primary_result.get("check_id"),
            "payer": primary_result.get("payer_name") or primary_result.get("payer"),
        },
        match_status="pending",
        metadata={"shadow_mode": True},
    )


def record_coding_review_shadow(
    settings: Settings,
    *,
    practice_id: str,
    decision_id: str,
    status: str,
    has_override: bool,
) -> None:
    if not getattr(settings, "pilot_shadow_mode", False):
        return
    if status == "approved" and not has_override:
        match_status = "match"
    elif status == "approved" and has_override:
        match_status = "mismatch"
    elif status == "rejected":
        match_status = "reject"
    else:
        match_status = "pending"

    record_shadow_event(
        settings,
        practice_id=practice_id,
        event_type="coding.reviewed",
        source="review_api",
        external_ref=decision_id,
        agent_payload={"decision_id": decision_id},
        human_label={"status": status, "has_override": has_override},
        match_status=match_status,
    )


def record_hitl_resolve_shadow(
    settings: Settings,
    *,
    practice_id: str,
    task_id: str,
    action: str,
    ai_codes: list[str],
    final_codes: list[str],
) -> None:
    if not getattr(settings, "pilot_shadow_mode", False):
        return
    if action == "approve":
        match_status = "match"
    elif action == "override":
        match_status = "mismatch"
    elif action == "reject":
        match_status = "reject"
    else:
        match_status = "pending"

    record_shadow_event(
        settings,
        practice_id=practice_id,
        event_type="hitl.resolved",
        source="dashboard_hitl",
        external_ref=task_id,
        agent_payload={"ai_codes": ai_codes},
        human_label={"action": action, "final_codes": final_codes},
        match_status=match_status,
    )
