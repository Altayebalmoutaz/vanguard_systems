"""Exception / human-review queue for OD writeback (Track C).

Stores review items as eligibility audit events and exposes a lightweight
aggregator for the dashboard API.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

REVIEW_EVENT_TYPE = "opendental_writeback_review"
SNAPSHOT_EVENT_TYPE = "opendental_benefits_snapshot"
FEE_ALERT_EVENT_TYPE = "opendental_fee_schedule_alert"
INSPLAN_DRIFT_EVENT_TYPE = "opendental_insplan_drift"
REVERIFY_ALERT_EVENT_TYPE = "opendental_reverify_change"


def persist_review_items(
    *,
    patient_id: Any,
    check_id: str | None,
    plan_num: int | None,
    items: list[dict[str, Any]],
    event_type: str = REVIEW_EVENT_TYPE,
) -> None:
    if not patient_id or not items:
        return
    try:
        from app.eligibility.audit import write_audit_event

        write_audit_event(
            patient_id=patient_id,
            event_type=event_type,
            detail={
                "check_id": check_id,
                "plan_num": plan_num,
                "items": items,
                "count": len(items),
            },
        )
    except Exception as exc:
        logger.warning("OD review queue persist failed: %s", exc)


def persist_benefits_snapshot(
    *,
    patient_id: Any,
    check_id: str | None,
    plan_num: int,
    snapshot: list[dict[str, Any]],
) -> None:
    if not patient_id:
        return
    try:
        from app.eligibility.audit import write_audit_event

        write_audit_event(
            patient_id=patient_id,
            event_type=SNAPSHOT_EVENT_TYPE,
            detail={
                "check_id": check_id,
                "plan_num": plan_num,
                "benefit_count": len(snapshot),
                "benefits": snapshot,
            },
        )
    except Exception as exc:
        logger.warning("OD benefits snapshot persist failed: %s", exc)


def extract_review_items_from_grid_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull actions that need staff attention from a benefits-grid result."""
    items: list[dict[str, Any]] = []
    for action in actions:
        disposition = str(action.get("disposition") or "")
        act = str(action.get("action") or "")
        if disposition == "review" or act in ("skipped_needs_review", "skipped_blocked"):
            items.append(
                {
                    "kind": "benefit_change",
                    "severity": "review",
                    "target": action.get("target"),
                    "type": action.get("type"),
                    "reason": action.get("reason") or act,
                    "previous": action.get("previous")
                    or action.get("previous_percent")
                    or action.get("previous_amount")
                    or action.get("previous_months"),
                    "proposed": action.get("percent")
                    if action.get("percent") is not None
                    else action.get("amount")
                    if action.get("amount") is not None
                    else action.get("months")
                    if action.get("months") is not None
                    else action.get("proposed"),
                    "benefit_num": action.get("benefit_num"),
                }
            )
        elif action.get("error"):
            items.append(
                {
                    "kind": "benefit_error",
                    "severity": "error",
                    "target": action.get("target"),
                    "type": action.get("type"),
                    "reason": action.get("error"),
                }
            )
    return items


def summarize_review_queue(audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate recent review/drift/fee alerts from patient audit rows."""
    by_type: dict[str, list[dict[str, Any]]] = {
        REVIEW_EVENT_TYPE: [],
        FEE_ALERT_EVENT_TYPE: [],
        INSPLAN_DRIFT_EVENT_TYPE: [],
        REVERIFY_ALERT_EVENT_TYPE: [],
    }
    for row in audit_rows:
        event_type = str(row.get("event_type") or "")
        if event_type not in by_type:
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        by_type[event_type].append(
            {
                "created_at": row.get("created_at"),
                "check_id": detail.get("check_id"),
                "plan_num": detail.get("plan_num"),
                "items": detail.get("items")
                or detail.get("alerts")
                or detail.get("findings")
                or [],
            }
        )
    return {
        "review": by_type[REVIEW_EVENT_TYPE],
        "fee_alerts": by_type[FEE_ALERT_EVENT_TYPE],
        "insplan_drift": by_type[INSPLAN_DRIFT_EVENT_TYPE],
        "reverify_alerts": by_type[REVERIFY_ALERT_EVENT_TYPE],
        "total_items": sum(len(v) for v in by_type.values()),
    }
