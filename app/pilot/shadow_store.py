"""Neon persistence for shadow pilot events and ROI summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import NeonNotConfiguredError, get_neon_dsn, neon_connection

HUMAN_REVIEW_EVENT_TYPES = frozenset(
    {
        "coding.reviewed",
        "hitl.resolved",
    }
)


def _require_neon(settings: Settings) -> None:
    if not get_neon_dsn(settings):
        raise NeonNotConfiguredError("NEON_DATABASE_URL is not configured")


def record_shadow_event(
    settings: Settings,
    *,
    practice_id: str,
    event_type: str,
    source: str = "system",
    patient_id: UUID | str | None = None,
    external_ref: str | None = None,
    agent_payload: dict[str, Any] | None = None,
    human_label: dict[str, Any] | None = None,
    match_status: str = "pending",
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Insert a shadow pilot event. Returns event id or None when Neon is unavailable."""
    if not get_neon_dsn(settings):
        return None

    patient_uuid: UUID | None = None
    if patient_id is not None:
        patient_uuid = UUID(str(patient_id))

    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into platform.pilot_shadow_events (
                  practice_id, event_type, source, patient_id, external_ref,
                  agent_payload, human_label, match_status, metadata
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    practice_id,
                    event_type,
                    source,
                    patient_uuid,
                    external_ref,
                    Jsonb(agent_payload or {}),
                    Jsonb(human_label) if human_label is not None else None,
                    match_status,
                    Jsonb(metadata or {}),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return str(row["id"]) if row else None


def get_shadow_summary(
    settings: Settings,
    *,
    practice_id: str,
    days: int = 7,
) -> dict[str, Any]:
    """Aggregate shadow pilot metrics for the last N days."""
    _require_neon(settings)
    window_days = max(1, min(int(days), 90))

    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select event_type, match_status, agent_payload, human_label, metadata
                from platform.pilot_shadow_events
                where practice_id = %s
                  and created_at >= now() - make_interval(days => %s)
                order by created_at desc
                """,
                (practice_id, window_days),
            )
            rows = [dict(row) for row in cur.fetchall()]

    eligibility_total = 0
    routing_status: dict[str, int] = {}
    human_reviews = 0
    matches = 0
    mismatches = 0
    rejections = 0
    hitl_resolved = 0
    hitl_overrides = 0

    for row in rows:
        event_type = str(row.get("event_type") or "")
        match_status = str(row.get("match_status") or "")

        if event_type == "eligibility.checked":
            eligibility_total += 1
            agent_payload = row.get("agent_payload") if isinstance(row.get("agent_payload"), dict) else {}
            routing = agent_payload.get("routing") if isinstance(agent_payload.get("routing"), dict) else {}
            status_key = str(routing.get("status") or "unknown")
            routing_status[status_key] = routing_status.get(status_key, 0) + 1
            continue

        if event_type not in HUMAN_REVIEW_EVENT_TYPES:
            continue

        human_reviews += 1
        if event_type == "hitl.resolved":
            hitl_resolved += 1
            human_label = row.get("human_label") if isinstance(row.get("human_label"), dict) else {}
            if human_label.get("action") == "override":
                hitl_overrides += 1

        if match_status == "match":
            matches += 1
        elif match_status == "mismatch":
            mismatches += 1
        elif match_status == "reject":
            rejections += 1

    override_rate = round(mismatches / human_reviews, 4) if human_reviews else 0.0
    match_rate = round(matches / human_reviews, 4) if human_reviews else 0.0
    hitl_override_rate = round(hitl_overrides / hitl_resolved, 4) if hitl_resolved else 0.0

    return {
        "practice_id": practice_id,
        "days": window_days,
        "eligibility": {
            "total_checks": eligibility_total,
            "by_routing_status": routing_status,
        },
        "agent_accuracy": {
            "total_human_reviews": human_reviews,
            "matches": matches,
            "mismatches": mismatches,
            "rejections": rejections,
            "override_rate": override_rate,
            "match_rate": match_rate,
        },
        "hitl": {
            "tasks_resolved": hitl_resolved,
            "override_count": hitl_overrides,
            "override_rate": hitl_override_rate,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
