"""Claim intake snapshot reads for the RCM pipeline (Neon PHI plane)."""

from __future__ import annotations

import json
import logging
from typing import Any

from psycopg.rows import dict_row

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.integrations.supabase_client import create_supabase
from supabase import Client

logger = logging.getLogger(__name__)


def _normalize_snapshot(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def fetch_claim_intake_snapshot(
    settings: Settings,
    encounter_id: str,
    *,
    practice_id: str | None,
) -> dict[str, Any] | None:
    """Load a ready claim intake snapshot by encounter id."""
    if get_neon_dsn(settings):
        if not practice_id:
            logger.warning("claim snapshot Neon lookup skipped: practice_id required")
            return None
        return _fetch_claim_snapshot_neon(settings, encounter_id, practice_id=practice_id)

    supabase = create_supabase(settings)
    if supabase is None:
        return None
    return _fetch_claim_snapshot_supabase(supabase, encounter_id, practice_id=practice_id)


def _fetch_claim_snapshot_neon(
    settings: Settings,
    encounter_id: str,
    *,
    practice_id: str,
) -> dict[str, Any] | None:
    with neon_connection(settings, practice_id=practice_id) as conn, conn.cursor() as cur:
        cur.execute(
            "select agents.get_claim_intake_snapshot(%s, %s)",
            (practice_id, encounter_id),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _normalize_snapshot(row[0])


def _fetch_claim_snapshot_supabase(
    supabase: Client,
    encounter_id: str,
    *,
    practice_id: str | None,
) -> dict[str, Any] | None:
    try:
        rpc_resp = supabase.rpc(
            "get_claim_intake_snapshot", {"p_encounter_id": encounter_id}
        ).execute()
        if isinstance(rpc_resp.data, dict):
            if practice_id and rpc_resp.data.get("practice_id") != practice_id:
                return None
            return rpc_resp.data
        if isinstance(rpc_resp.data, list) and rpc_resp.data:
            first = rpc_resp.data[0]
            if isinstance(first, dict):
                if practice_id and first.get("practice_id") != practice_id:
                    return None
                return first
    except Exception:
        pass

    try:
        query = (
            supabase.table("claim_intake_snapshot")
            .select("*")
            .eq("encounter_id", encounter_id)
            .limit(1)
        )
        if practice_id:
            query = query.eq("practice_id", practice_id)
        table_resp = query.execute()
        if isinstance(table_resp.data, list) and table_resp.data:
            first = table_resp.data[0]
            if isinstance(first, dict):
                return first
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load claim intake snapshot for encounter_id={encounter_id}: {exc}"
        ) from exc

    return None


def fetch_claim_intake_snapshot_row(
    settings: Settings,
    encounter_id: str,
    *,
    practice_id: str | None,
) -> dict[str, Any] | None:
    """Table-shaped snapshot row (Neon direct select when on PHI plane)."""
    if get_neon_dsn(settings):
        if not practice_id:
            return None
        query = """
            select *
            from agents.claim_intake_snapshot
            where practice_id = %s
              and encounter_id = %s
            limit 1
        """
        with (
            neon_connection(settings, practice_id=practice_id) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(query, (practice_id, encounter_id))
            row = cur.fetchone()
        return dict(row) if row else None

    supabase = create_supabase(settings)
    if supabase is None:
        return None
    return _fetch_claim_snapshot_supabase(supabase, encounter_id, practice_id=practice_id)
