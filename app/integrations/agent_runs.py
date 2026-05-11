"""
Persisted agent runs — minimal audit + gating metadata (`rcm.agent_runs`).

Use for prior auth and future agents; eligibility history remains in `eligibility_checks`.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from supabase import Client

logger = logging.getLogger(__name__)

AGENT_PRIOR_AUTH = "prior_auth"


def insert_agent_run(
    supabase: Client,
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
    try:
        res = supabase.table("agent_runs").insert(row).select("id").execute()
        data = getattr(res, "data", None) or []
        if data and data[0].get("id"):
            return UUID(str(data[0]["id"]))
    except Exception as e:
        logger.warning("agent_runs insert failed: %s", e)
    return None


def list_agent_runs_for_patient(
    supabase: Client,
    patient_id: UUID,
    *,
    agent: str | None = AGENT_PRIOR_AUTH,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent runs for a patient (optional filter by agent)."""
    try:
        q = (
            supabase.table("agent_runs")
            .select("*")
            .eq("patient_id", str(patient_id))
            .order("created_at", desc=True)
            .limit(limit)
        )
        if agent:
            q = q.eq("agent", agent)
        res = q.execute()
        return list(getattr(res, "data", None) or [])
    except Exception as e:
        logger.warning("agent_runs list failed: %s", e)
        return []
