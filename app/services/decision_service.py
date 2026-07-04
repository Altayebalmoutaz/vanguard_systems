"""Synchronous DB + orchestration helpers for coding decisions."""

from __future__ import annotations

import inspect
import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from postgrest.types import ReturnMethod
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from supabase import Client

from app.agents.coding_agent import run_coding_agent
from app.audit.writer import write_audit_log
from app.pilot.shadow import record_coding_review_shadow
from app.config import Settings, get_settings
from app.db.connection import get_neon_dsn, neon_connection
from app.integrations.supabase_client import create_supabase
from app.schemas.coding import CodingAgentRequest
from app.security.phi import scrub_for_log
from app.workflow.rcm_tasks import create_hitl_task_from_coding_decision

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _patient_age_from_dob(dob: date | datetime | None) -> int:
    if dob is None:
        return 0
    if isinstance(dob, datetime):
        dob = dob.date()
    today = date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(years, 0)


def _id_from_insert_response(response: Any) -> Any:
    raw = getattr(response, "data", None)
    if isinstance(raw, list) and raw:
        return raw[0].get("id")
    if isinstance(raw, dict):
        return raw.get("id")
    return None


def _get_supabase_for_reference(settings: Settings) -> Client:
    supabase = create_supabase(settings)
    if supabase is None:
        raise RuntimeError("Supabase reference plane is not configured")
    return supabase


def _fetch_encounter_neon(
    settings: Settings,
    encounter_id: str,
    *,
    practice_id: str,
) -> dict[str, Any] | None:
    query = """
        select
          e.id,
          e.practice_id,
          e.patient_id,
          e.provider_id,
          e.clinical_note,
          e.procedures_json,
          e.attachments,
          e.status,
          e.created_at,
          p.payer,
          p.dob
        from patient.encounters e
        left join patient.patients p
          on p.id = e.patient_id and p.practice_id = e.practice_id
        where e.id = %s
          and e.practice_id = %s
        limit 1
    """
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (encounter_id, practice_id))
            row = cur.fetchone()
    if not row:
        return None
    encounter = dict(row)
    encounter["insurance"] = str(encounter.pop("payer", None) or "Unknown")
    encounter["patient_age"] = _patient_age_from_dob(encounter.pop("dob", None))
    return encounter


def _fetch_encounter_supabase(
    supabase: Client,
    encounter_id: str,
    *,
    practice_id: str | None,
) -> dict[str, Any] | None:
    encounter_query = (
        supabase.table("encounters").select("*").eq("id", encounter_id).limit(1)
    )
    if practice_id:
        encounter_query = encounter_query.eq("practice_id", practice_id)
    encounter_resp = encounter_query.execute()
    encounter_rows = encounter_resp.data or []
    if not encounter_rows:
        return None
    encounter = encounter_rows[0]
    if "patient_age" not in encounter:
        encounter["patient_age"] = 0
    if "insurance" not in encounter:
        encounter["insurance"] = "Unknown"
    return encounter


def _latest_decision_id_neon(
    settings: Settings,
    encounter_id: str,
    *,
    practice_id: str,
) -> Any:
    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    select id
                    from agents.agent_decisions
                    where encounter_id = %s
                      and practice_id = %s
                    order by created_at desc
                    limit 1
                    """,
                    (encounter_id, practice_id),
                )
                row = cur.fetchone()
        return row["id"] if row else None
    except Exception as exc:
        logger.warning(
            "fallback lookup for latest decision id failed (encounter_id=%s): %s",
            encounter_id,
            scrub_for_log(str(exc)),
        )
        return None


def _latest_decision_id_supabase(
    db: Client,
    encounter_id: str,
    practice_id: str | None,
) -> Any:
    try:
        q = (
            db.table("agent_decisions")
            .select("id")
            .eq("encounter_id", encounter_id)
            .order("created_at", desc=True)
            .limit(1)
        )
        if practice_id:
            q = q.eq("practice_id", practice_id)
        r = q.execute()
        rows = r.data or []
        return rows[0].get("id") if rows else None
    except Exception as exc:
        logger.warning(
            "fallback lookup for latest decision id failed (encounter_id=%s): %s",
            encounter_id,
            scrub_for_log(str(exc)),
        )
        return None


def _insert_decision_neon(
    settings: Settings,
    *,
    practice_id: str,
    decision_payload: dict[str, Any],
) -> Any:
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into agents.agent_decisions (
                  practice_id, encounter_id, agent_name, input_snapshot,
                  reasoning, output, confidence, status
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    practice_id,
                    decision_payload["encounter_id"],
                    decision_payload["agent_name"],
                    Jsonb(decision_payload["input_snapshot"]),
                    decision_payload["reasoning"],
                    Jsonb(decision_payload["output"]),
                    decision_payload["confidence"],
                    decision_payload["status"],
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"] if row else None


def _call_coding_agent(
    note: str,
    encounter: dict[str, Any],
    supabase: Client,
) -> dict[str, Any]:
    signature = inspect.signature(run_coding_agent)
    if len(signature.parameters) == 1:
        result = run_coding_agent(note)
    else:
        settings = get_settings()
        request = CodingAgentRequest(
            clinical_note=note,
            patient_age=int(encounter.get("patient_age") or 0),
            insurance=str(encounter.get("insurance") or "Unknown"),
        )
        result = run_coding_agent(settings, supabase, request)

    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


def run_agent_for_encounter(
    settings: Settings,
    encounter_id: str,
    *,
    practice_id: str,
) -> dict[str, Any]:
    """Fetch encounter, run coding agent, and persist a pending decision."""
    if get_neon_dsn(settings):
        encounter = _fetch_encounter_neon(settings, encounter_id, practice_id=practice_id)
    else:
        supabase = _get_supabase_for_reference(settings)
        encounter = _fetch_encounter_supabase(
            supabase, encounter_id, practice_id=practice_id
        )

    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found")

    clinical_note = encounter.get("clinical_note")
    if not clinical_note:
        raise HTTPException(status_code=400, detail="Encounter missing clinical_note")

    supabase = _get_supabase_for_reference(settings)
    agent_result = _call_coding_agent(str(clinical_note), encounter, supabase)
    cdt_codes = [str(c).strip() for c in (agent_result.get("cdt_codes") or []) if str(c).strip()]

    decision_payload = {
        "encounter_id": encounter_id,
        "agent_name": "coding_agent_v1",
        "input_snapshot": encounter,
        "reasoning": agent_result.get("justification", ""),
        "output": {
            "cdt_codes": cdt_codes,
            "icd10_codes": agent_result.get("icd10_codes", []),
            "payer_flags": agent_result.get("payer_flags", []),
            "payer_rules_matched": agent_result.get("payer_rules_matched", []),
        },
        "confidence": _safe_float(agent_result.get("confidence", 0.0)),
        "status": "pending_review",
        "practice_id": practice_id,
    }

    if get_neon_dsn(settings):
        new_id = _insert_decision_neon(settings, practice_id=practice_id, decision_payload=decision_payload)
        if new_id is None:
            new_id = _latest_decision_id_neon(
                settings, encounter_id, practice_id=practice_id
            )
    else:
        db = supabase
        insert_res = (
            db.table("agent_decisions")
            .insert(decision_payload, returning=ReturnMethod.representation)
            .execute()
        )
        new_id = _id_from_insert_response(insert_res)
        if new_id is None:
            new_id = _latest_decision_id_supabase(db, encounter_id, practice_id)

    out = dict(agent_result)
    out["decision_id"] = str(new_id) if new_id is not None else None
    out["encounter_id"] = encounter_id

    if new_id is not None and get_neon_dsn(settings):
        hitl_task_id = create_hitl_task_from_coding_decision(
            settings,
            practice_id=practice_id,
            decision_id=str(new_id),
            encounter_id=encounter_id,
            encounter=encounter,
            agent_result=agent_result,
            confidence=_safe_float(agent_result.get("confidence")),
            threshold=settings.confidence_hitl_threshold,
        )
        if hitl_task_id:
            out["hitl_task_id"] = hitl_task_id

    return out


def _review_decision_neon(
    settings: Settings,
    decision_id: str,
    status: str,
    override: dict[str, Any] | None,
    *,
    practice_id: str,
) -> dict[str, str]:
    with neon_connection(settings, practice_id=practice_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                update agents.agent_decisions
                set status = %s
                where id = %s
                  and practice_id = %s
                returning encounter_id
                """,
                (status, decision_id, practice_id),
            )
            decision_rows = cur.fetchall()
            if not decision_rows:
                raise HTTPException(status_code=404, detail="Decision not found")

            if override is not None:
                cur.execute(
                    """
                    insert into feedback.decision_feedback (
                      practice_id, decision_id, human_override, reason
                    )
                    values (%s, %s, %s, %s)
                    """,
                    (practice_id, decision_id, Jsonb(override), "manual correction"),
                )

            encounter_id = decision_rows[0].get("encounter_id")
            if status == "approved" and encounter_id:
                cur.execute(
                    """
                    update patient.encounters
                    set status = 'coded'
                    where id = %s
                      and practice_id = %s
                    """,
                    (encounter_id, practice_id),
                )
        conn.commit()
    write_audit_log(
        settings,
        practice_id=practice_id,
        action="decision.reviewed",
        entity_type="agent_decision",
        entity_id=UUID(decision_id),
        performed_by="review_api",
        metadata={"status": status, "has_override": override is not None},
    )
    record_coding_review_shadow(
        settings,
        practice_id=practice_id,
        decision_id=decision_id,
        status=status,
        has_override=override is not None,
    )
    return {"message": "Decision reviewed successfully"}


def review_decision(
    settings: Settings,
    decision_id: str,
    status: str,
    override: dict[str, Any] | None,
    *,
    practice_id: str,
) -> dict[str, str]:
    """Apply human review status and optional override feedback."""
    if get_neon_dsn(settings):
        return _review_decision_neon(
            settings,
            decision_id,
            status,
            override,
            practice_id=practice_id,
        )

    supabase = _get_supabase_for_reference(settings)
    db = supabase
    decision_query = db.table("agent_decisions").update({"status": status}).eq("id", decision_id)
    if practice_id:
        decision_query = decision_query.eq("practice_id", practice_id)
    decision_update = decision_query.execute()
    decision_rows = decision_update.data or []
    if not decision_rows:
        raise HTTPException(status_code=404, detail="Decision not found")

    if override is not None:
        db.table("decision_feedback").insert(
            {
                "decision_id": decision_id,
                "human_override": override,
                "reason": "manual correction",
            }
        ).execute()

    if status == "approved":
        encounter_id = decision_rows[0].get("encounter_id")
        if encounter_id:
            encounter_update = db.table("encounters").update({"status": "coded"}).eq("id", encounter_id)
            if practice_id:
                encounter_update = encounter_update.eq("practice_id", practice_id)
            encounter_update.execute()

    return {"message": "Decision reviewed successfully"}
