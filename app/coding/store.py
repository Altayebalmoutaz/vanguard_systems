"""Persist coding_runs (system of record for scribe-driven coding)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.db.json_safe import json_safe
from app.integrations.supabase_client import create_supabase
from app.security.phi import scrub_for_log

logger = logging.getLogger(__name__)


def fetch_run_by_request_id(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
) -> dict[str, Any] | None:
    """Return an existing coding_runs row for idempotent replay, or None."""
    if get_neon_dsn(settings):
        try:
            with (
                neon_connection(settings, practice_id=practice_id) as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                cur.execute(
                    """
                    select id, practice_id, request_id, status, overall_confidence,
                           response_payload, created_at
                    from agents.coding_runs
                    where practice_id = %s and request_id = %s
                    limit 1
                    """,
                    (practice_id, str(request_id)),
                )
                row = cur.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            logger.warning(
                "coding_runs idempotency lookup failed: %s",
                scrub_for_log(str(exc)),
            )
            return None

    supabase = create_supabase(settings)
    if supabase is None:
        return None
    try:
        result = (
            supabase.table("coding_runs")
            .select("id,practice_id,request_id,status,overall_confidence,response_payload,created_at")
            .eq("practice_id", practice_id)
            .eq("request_id", str(request_id))
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        return dict(rows[0]) if rows else None
    except Exception as exc:
        logger.warning(
            "coding_runs supabase idempotency lookup failed: %s",
            scrub_for_log(str(exc)),
        )
        return None


def insert_coding_run(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
    patient_id: str,
    provider_id: str,
    encounter_datetime: datetime,
    payer_id: str | None,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    status: str,
    overall_confidence: float,
) -> UUID | None:
    """Insert one coding_runs row; returns id (or existing id on unique conflict)."""
    if get_neon_dsn(settings):
        return _insert_neon(
            settings,
            practice_id=practice_id,
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            encounter_datetime=encounter_datetime,
            payer_id=payer_id,
            request_payload=request_payload,
            response_payload=response_payload,
            status=status,
            overall_confidence=overall_confidence,
        )

    supabase = create_supabase(settings)
    if supabase is None:
        logger.warning("coding_runs insert skipped: no Neon/Supabase configured")
        return None
    try:
        result = (
            supabase.table("coding_runs")
            .insert(
                {
                    "practice_id": practice_id,
                    "request_id": str(request_id),
                    "patient_id": patient_id,
                    "provider_id": provider_id,
                    "encounter_datetime": encounter_datetime.isoformat(),
                    "payer_id": payer_id,
                    "request_payload": json_safe(request_payload),
                    "response_payload": json_safe(response_payload),
                    "status": status,
                    "overall_confidence": overall_confidence,
                }
            )
            .execute()
        )
        rows = getattr(result, "data", None) or []
        if rows and rows[0].get("id"):
            return UUID(str(rows[0]["id"]))
    except Exception as exc:
        # Unique conflict → fetch existing.
        logger.info(
            "coding_runs supabase insert failed (may be duplicate): %s",
            scrub_for_log(str(exc)),
        )
        existing = fetch_run_by_request_id(
            settings, practice_id=practice_id, request_id=request_id
        )
        if existing and existing.get("id"):
            return UUID(str(existing["id"]))
    return None


def _insert_neon(
    settings: Settings,
    *,
    practice_id: str,
    request_id: UUID,
    patient_id: str,
    provider_id: str,
    encounter_datetime: datetime,
    payer_id: str | None,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    status: str,
    overall_confidence: float,
) -> UUID | None:
    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    insert into agents.coding_runs (
                      practice_id, request_id, patient_id, provider_id,
                      encounter_datetime, payer_id, request_payload, response_payload,
                      status, overall_confidence
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (practice_id, request_id) do update
                      set practice_id = excluded.practice_id
                    returning id
                    """,
                    (
                        practice_id,
                        str(request_id),
                        patient_id,
                        provider_id,
                        encounter_datetime,
                        payer_id,
                        Jsonb(json_safe(request_payload)),
                        Jsonb(json_safe(response_payload)),
                        status,
                        overall_confidence,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if row and row.get("id"):
            return UUID(str(row["id"]))
    except Exception as exc:
        logger.warning(
            "coding_runs neon insert failed: %s",
            scrub_for_log(str(exc)),
        )
        existing = fetch_run_by_request_id(
            settings, practice_id=practice_id, request_id=request_id
        )
        if existing and existing.get("id"):
            return UUID(str(existing["id"]))
    return None
