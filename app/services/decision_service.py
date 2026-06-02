"""Synchronous DB + orchestration helpers for coding decisions."""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import HTTPException
from postgrest.types import ReturnMethod

from app.agents.coding_agent import run_coding_agent
from app.config import get_settings
from app.schemas.coding import CodingAgentRequest
from supabase import Client


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _id_from_insert_response(response: Any) -> Any:
    raw = getattr(response, "data", None)
    if isinstance(raw, list) and raw:
        return raw[0].get("id")
    if isinstance(raw, dict):
        return raw.get("id")
    return None


def _latest_decision_id_for_encounter(db: Any, encounter_id: str) -> Any:
    """Fallback when PostgREST returns minimal body or empty data on insert."""
    try:
        r = (
            db.table("agent_decisions")
            .select("id")
            .eq("encounter_id", encounter_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0].get("id") if rows else None
    except Exception:
        return None


def _call_coding_agent(
    note: str,
    encounter: dict[str, Any],
    supabase: Client,
) -> dict[str, Any]:
    """
    Call the existing coding agent without modifying agent implementation.

    Supports both shapes:
    - run_coding_agent(note: str) -> dict
    - run_coding_agent(settings, supabase, request) -> CodingAgentResponse
    """
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
        # Keep current agent flow untouched; only adapt call site.
        # Pass the active Supabase client so ICD/CDT validation tools can query reference tables.
        result = run_coding_agent(settings, supabase, request)

    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


def run_agent_for_encounter(supabase: Client, encounter_id: str) -> dict[str, Any]:
    """Fetch encounter, run coding agent, and persist a pending decision."""
    db = supabase
    # Step 1: fetch encounter row.
    encounter_resp = db.table("encounters").select("*").eq("id", encounter_id).limit(1).execute()
    encounter_rows = encounter_resp.data or []
    if not encounter_rows:
        raise HTTPException(status_code=404, detail="Encounter not found")

    encounter = encounter_rows[0]
    clinical_note = encounter.get("clinical_note")
    if not clinical_note:
        raise HTTPException(status_code=400, detail="Encounter missing clinical_note")

    # Step 2: run the coding agent.
    agent_result = _call_coding_agent(str(clinical_note), encounter, supabase)
    cdt_codes = [str(c).strip() for c in (agent_result.get("cdt_codes") or []) if str(c).strip()]

    # Step 3: store decision for dashboard review (payer rules already evaluated in the agent).
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
    }
    # postgrest-py 2.x: .insert() returns SyncQueryRequestBuilder — only .execute() is valid;
    # never chain .select() after .insert(). Ask for representation so `data` includes the row.
    insert_res = (
        db.table("agent_decisions")
        .insert(decision_payload, returning=ReturnMethod.representation)
        .execute()
    )
    new_id = _id_from_insert_response(insert_res)
    if new_id is None:
        new_id = _latest_decision_id_for_encounter(db, encounter_id)

    out = dict(agent_result)
    out["decision_id"] = new_id
    out["encounter_id"] = encounter_id
    return out


def review_decision(
    supabase: Client,
    decision_id: str,
    status: str,
    override: dict[str, Any] | None,
) -> dict[str, str]:
    """Apply human review status and optional override feedback."""
    db = supabase
    # Step 1: update decision status.
    decision_update = (
        db.table("agent_decisions").update({"status": status}).eq("id", decision_id).execute()
    )
    decision_rows = decision_update.data or []
    if not decision_rows:
        raise HTTPException(status_code=404, detail="Decision not found")

    # Step 2: persist manual correction feedback if provided.
    if override is not None:
        db.table("decision_feedback").insert(
            {
                "decision_id": decision_id,
                "human_override": override,
                "reason": "manual correction",
            }
        ).execute()

    # Bonus: mark encounter as coded when approved.
    if status == "approved":
        encounter_id = decision_rows[0].get("encounter_id")
        if encounter_id:
            db.table("encounters").update({"status": "coded"}).eq("id", encounter_id).execute()

    return {"message": "Decision reviewed successfully"}
