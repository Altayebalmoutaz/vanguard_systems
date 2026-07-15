"""
Persisted agent runs — minimal audit + gating metadata (``rcm.agent_runs``).

Neon is the PHI-plane writer when ``NEON_DATABASE_URL`` is configured; otherwise
falls back to Supabase for local dev without a Neon branch.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection
from app.db.phi_store import PhiStoreError
from app.integrations.supabase_client import create_supabase

logger = logging.getLogger(__name__)

AGENT_PRIOR_AUTH = "prior_auth"

AGENT_RUN_STATUSES = frozenset({"pending_review", "approved", "denied", "expired", "superseded"})
AGENT_RUN_RESOLVE_STATUSES = frozenset({"approved", "denied", "expired", "superseded"})
AGENT_RUN_TERMINAL_STATUSES = AGENT_RUN_RESOLVE_STATUSES
VALID_AGENT_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending_review": AGENT_RUN_RESOLVE_STATUSES,
}


class AgentRunNotFoundError(LookupError):
    """No agent run matched the given id and practice."""


class AgentRunTransitionError(ValueError):
    """Status transition is not allowed for the current run state."""


def validate_agent_run_transition(current_status: str, new_status: str) -> None:
    """Raise ``AgentRunTransitionError`` when ``new_status`` is not allowed."""
    allowed = VALID_AGENT_RUN_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        raise AgentRunTransitionError(
            f"Invalid agent run transition: {current_status!r} -> {new_status!r}"
        )


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _insert_agent_run_neon(
    settings: Settings,
    *,
    practice_id: str,
    agent: str,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
    meta: dict[str, Any] | None,
    payer_id: str | None,
    patient_id: UUID | None,
    status: str,
) -> UUID | None:
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into rcm.agent_runs (
                  practice_id, patient_id, agent, payer_id, status,
                  input_json, output_json, meta
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    practice_id,
                    patient_id,
                    agent,
                    payer_id,
                    status,
                    Jsonb(input_json),
                    Jsonb(output_json),
                    Jsonb(meta or {}),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    if row and row.get("id"):
        return UUID(str(row["id"]))
    return None


def _insert_agent_run_supabase(
    supabase: Any,
    *,
    practice_id: str | None,
    agent: str,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
    meta: dict[str, Any] | None,
    payer_id: str | None,
    patient_id: UUID | None,
    status: str,
) -> UUID | None:
    row: dict[str, Any] = {
        "agent": agent,
        "input_json": input_json,
        "output_json": output_json,
        "meta": meta or {},
        "status": status,
    }
    if payer_id:
        row["payer_id"] = payer_id
    if patient_id is not None:
        row["patient_id"] = str(patient_id)
    if practice_id:
        row["practice_id"] = practice_id
    res = supabase.table("agent_runs").insert(row).select("id").execute()
    data = getattr(res, "data", None) or []
    if data and data[0].get("id"):
        return UUID(str(data[0]["id"]))
    return None


def insert_agent_run(
    settings: Settings,
    *,
    agent: str,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
    meta: dict[str, Any] | None = None,
    payer_id: str | None = None,
    patient_id: UUID | None = None,
    practice_id: str | None = None,
    status: str = "pending_review",
) -> UUID | None:
    """Insert one run; returns new id or None on failure."""
    if get_neon_dsn(settings):
        if not practice_id:
            raise PhiStoreError("practice_id required for Neon PHI store")
        return _insert_agent_run_neon(
            settings,
            practice_id=practice_id,
            agent=agent,
            input_json=input_json,
            output_json=output_json,
            meta=meta,
            payer_id=payer_id,
            patient_id=patient_id,
            status=status,
        )
    if not practice_id:
        logger.warning("agent_runs insert skipped: practice_id is required")
        return None
    try:
        supabase = create_supabase(settings)
        if supabase is None:
            logger.warning("agent_runs insert skipped: no Neon or Supabase configured")
            return None
        return _insert_agent_run_supabase(
            supabase,
            practice_id=practice_id,
            agent=agent,
            input_json=input_json,
            output_json=output_json,
            meta=meta,
            payer_id=payer_id,
            patient_id=patient_id,
            status=status,
        )
    except Exception as e:
        logger.warning("agent_runs insert failed: %s", e)
        return None


def _list_agent_runs_neon(
    settings: Settings,
    patient_id: UUID,
    *,
    practice_id: str,
    agent: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    query = """
        select *
        from rcm.agent_runs
        where practice_id = %s
          and patient_id = %s
    """
    params: list[Any] = [practice_id, patient_id]
    if agent:
        query += " and agent = %s"
        params.append(agent)
    query += " order by created_at desc limit %s"
    params.append(limit)

    with (
        neon_connection(settings, practice_id=practice_id) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        cur.execute(query, params)
        rows = cur.fetchall()
    return [_serialize_row(dict(row)) for row in rows]


def _list_agent_runs_supabase(
    supabase: Any,
    patient_id: UUID,
    *,
    practice_id: str | None,
    agent: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    q = (
        supabase.table("agent_runs")
        .select("*")
        .eq("patient_id", str(patient_id))
        .order("created_at", desc=True)
        .limit(limit)
    )
    if agent:
        q = q.eq("agent", agent)
    if practice_id:
        q = q.eq("practice_id", practice_id)
    res = q.execute()
    return list(getattr(res, "data", None) or [])


def list_agent_runs_for_patient(
    settings: Settings,
    patient_id: UUID,
    *,
    practice_id: str,
    agent: str | None = AGENT_PRIOR_AUTH,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent runs for a patient (optional filter by agent)."""
    try:
        if get_neon_dsn(settings):
            return _list_agent_runs_neon(
                settings,
                patient_id,
                practice_id=practice_id,
                agent=agent,
                limit=limit,
            )
        supabase = create_supabase(settings)
        if supabase is None:
            return []
        return _list_agent_runs_supabase(
            supabase,
            patient_id,
            practice_id=practice_id,
            agent=agent,
            limit=limit,
        )
    except Exception as e:
        logger.warning("agent_runs list failed: %s", e)
        return []


def _update_agent_run_status_neon(
    settings: Settings,
    run_id: UUID,
    status: str,
    *,
    practice_id: str,
    meta_patch: dict[str, Any] | None,
) -> dict[str, Any]:
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                select status, meta
                from rcm.agent_runs
                where id = %s
                  and practice_id = %s
                  and agent = %s
                for update
                """,
                (run_id, practice_id, AGENT_PRIOR_AUTH),
            )
            row = cur.fetchone()
            if not row:
                raise AgentRunNotFoundError(f"Agent run not found: {run_id}")

            current_status = str(row["status"])
            validate_agent_run_transition(current_status, status)

            if meta_patch:
                cur.execute(
                    """
                    update rcm.agent_runs
                    set status = %s,
                        meta = coalesce(meta, '{}'::jsonb) || %s
                    where id = %s
                      and practice_id = %s
                      and agent = %s
                    returning *
                    """,
                    (status, Jsonb(meta_patch), run_id, practice_id, AGENT_PRIOR_AUTH),
                )
            else:
                cur.execute(
                    """
                    update rcm.agent_runs
                    set status = %s
                    where id = %s
                      and practice_id = %s
                      and agent = %s
                    returning *
                    """,
                    (status, run_id, practice_id, AGENT_PRIOR_AUTH),
                )
            updated = cur.fetchone()
        conn.commit()
    if not updated:
        raise AgentRunNotFoundError(f"Agent run not found: {run_id}")
    return _serialize_row(dict(updated))


def _update_agent_run_status_supabase(
    supabase: Any,
    run_id: UUID,
    status: str,
    *,
    practice_id: str,
    meta_patch: dict[str, Any] | None,
) -> dict[str, Any]:
    res = (
        supabase.table("agent_runs")
        .select("status, meta")
        .eq("id", str(run_id))
        .eq("practice_id", practice_id)
        .eq("agent", AGENT_PRIOR_AUTH)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        raise AgentRunNotFoundError(f"Agent run not found: {run_id}")

    current_status = str(rows[0].get("status") or "")
    validate_agent_run_transition(current_status, status)

    patch: dict[str, Any] = {"status": status}
    if meta_patch:
        existing_meta = rows[0].get("meta") or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        patch["meta"] = {**existing_meta, **meta_patch}

    update_res = (
        supabase.table("agent_runs")
        .update(patch)
        .eq("id", str(run_id))
        .eq("practice_id", practice_id)
        .eq("agent", AGENT_PRIOR_AUTH)
        .select("*")
        .execute()
    )
    updated_rows = getattr(update_res, "data", None) or []
    if not updated_rows:
        raise AgentRunNotFoundError(f"Agent run not found: {run_id}")
    return updated_rows[0]


def update_agent_run_status(
    settings: Settings,
    run_id: UUID,
    status: str,
    *,
    practice_id: str,
    meta_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a prior-auth run from ``pending_review`` to a terminal status."""
    if status not in AGENT_RUN_RESOLVE_STATUSES:
        raise AgentRunTransitionError(f"Invalid resolve status: {status!r}")

    try:
        if get_neon_dsn(settings):
            return _update_agent_run_status_neon(
                settings,
                run_id,
                status,
                practice_id=practice_id,
                meta_patch=meta_patch,
            )
        supabase = create_supabase(settings)
        if supabase is None:
            raise RuntimeError("No Neon or Supabase configured for agent_runs update")
        return _update_agent_run_status_supabase(
            supabase,
            run_id,
            status,
            practice_id=practice_id,
            meta_patch=meta_patch,
        )
    except (AgentRunNotFoundError, AgentRunTransitionError):
        raise
    except Exception as e:
        logger.warning("agent_runs status update failed: %s", e)
        raise RuntimeError("agent_runs status update failed") from e


def agent_run_row_to_json(row: dict[str, Any]) -> str:
    """Test helper: stable JSON for row snapshots."""
    return json.dumps(row, sort_keys=True, default=str)
