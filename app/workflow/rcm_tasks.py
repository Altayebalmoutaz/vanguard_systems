"""Workflow OS task writers for ``agents.rcm_tasks``."""

from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection

logger = logging.getLogger(__name__)

TASK_TYPE_FULL_RCM_PIPELINE = "Full RCM pipeline"
TASK_TYPE_CODING_REVIEW = "Coding review"
TASK_TYPE_PRIOR_AUTH_REVIEW = "Prior auth review"
TASK_TYPE_DENIAL_REVIEW = "Denial review"

HITL_STATUS_PENDING = "pending"
HITL_STATUS_APPROVED = "approved"
HITL_STATUS_REJECTED = "rejected"


def extract_coding_confidence(pipeline_result: dict[str, Any]) -> float | None:
    coding = pipeline_result.get("coding")
    if not isinstance(coding, dict):
        return None
    try:
        return float(coding.get("confidence"))
    except (TypeError, ValueError):
        return None


def should_route_to_hitl(confidence: float | None, threshold: float) -> bool:
    if confidence is None:
        return True
    return confidence < threshold


def should_route_coding_to_hitl(
    confidence: float | None,
    threshold: float,
    *,
    payer_flags: list[str] | None = None,
) -> bool:
    if should_route_to_hitl(confidence, threshold):
        return True
    flags = payer_flags or []
    return any("review" in str(flag).lower() for flag in flags)


def should_route_prior_auth_to_hitl(prior_auth: dict[str, Any]) -> bool:
    if prior_auth.get("requires_auth"):
        return True
    if prior_auth.get("required_documents"):
        return True
    return str(prior_auth.get("risk_level") or "").lower() == "high"


def should_route_denial_to_hitl(denial: dict[str, Any]) -> bool:
    if denial.get("requires_human_review"):
        return True
    status = str(denial.get("status") or "")
    next_action = str(denial.get("next_action") or "")
    if status in {"denied", "partial"} and next_action not in {"", "none"}:
        return True
    return False


def create_rcm_task(
    settings: Settings,
    *,
    practice_id: str,
    task_type: str,
    backend_record_id: str,
    patient_name: str,
    payer: str | None = None,
    clinical_note: str = "",
    ai_codes: list[str] | None = None,
    ai_summary: str | None = None,
    confidence: float | None = None,
    backend_claim_id: str = "",
    pipeline_json: dict[str, Any] | None = None,
    event_reason: str,
    actor_label: str = "system",
) -> str | None:
    """Insert one pending task plus a ``task.created`` event."""
    if not get_neon_dsn(settings):
        return None

    codes = list(ai_codes or [])
    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into agents.rcm_tasks (
                      practice_id, backend_record_id, backend_claim_id, task_type,
                      patient_name, payer, clinical_note, ai_codes, ai_summary,
                      confidence, status, pipeline_json
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        practice_id,
                        backend_record_id,
                        backend_claim_id,
                        task_type,
                        patient_name,
                        payer,
                        clinical_note,
                        codes,
                        ai_summary,
                        confidence,
                        HITL_STATUS_PENDING,
                        Jsonb(pipeline_json or {}),
                    ),
                )
                row = cur.fetchone()
                task_id = str(row[0]) if row else None
                if task_id:
                    cur.execute(
                        """
                        insert into agents.rcm_task_events (
                          practice_id, task_id, event_type, actor_label, payload
                        )
                        values (%s, %s, %s, %s, %s)
                        """,
                        (
                            practice_id,
                            task_id,
                            "task.created",
                            actor_label,
                            Jsonb({"reason": event_reason}),
                        ),
                    )
            conn.commit()
        return task_id
    except Exception as exc:
        logger.warning("rcm_tasks insert failed: %s", exc)
        return None


def create_hitl_task_from_pipeline(
    settings: Settings,
    *,
    practice_id: str,
    pipeline_run_id: str,
    pipeline_result: dict[str, Any],
    confidence: float | None,
) -> str | None:
    coding = pipeline_result.get("coding") if isinstance(pipeline_result.get("coding"), dict) else {}
    prior = pipeline_result.get("prior_auth") if isinstance(pipeline_result.get("prior_auth"), dict) else {}
    claim = pipeline_result.get("claim_draft") if isinstance(pipeline_result.get("claim_draft"), dict) else {}

    patient_name = str(
        pipeline_result.get("patient_name")
        or pipeline_result.get("patient")
        or "Unknown patient"
    )
    payer = str(pipeline_result.get("insurance") or prior.get("payer") or "Unknown payer")
    cdt_codes = coding.get("cdt_codes") if isinstance(coding.get("cdt_codes"), list) else []
    summary = str(coding.get("justification") or "Low-confidence coding — human review required")

    return create_rcm_task(
        settings,
        practice_id=practice_id,
        task_type=TASK_TYPE_FULL_RCM_PIPELINE,
        backend_record_id=pipeline_run_id,
        patient_name=patient_name,
        payer=payer,
        clinical_note=str(pipeline_result.get("clinical_note") or ""),
        ai_codes=[str(code) for code in cdt_codes],
        ai_summary=summary,
        confidence=confidence,
        pipeline_json={
            "pipeline_run_id": pipeline_run_id,
            "coding": coding,
            "prior_auth": prior,
            "claim_draft": claim,
            "gated": True,
        },
        event_reason="confidence_below_threshold",
        actor_label="pipeline_worker",
    )


def create_hitl_task_from_coding_decision(
    settings: Settings,
    *,
    practice_id: str,
    decision_id: str,
    encounter_id: str,
    encounter: dict[str, Any],
    agent_result: dict[str, Any],
    confidence: float | None,
    threshold: float,
) -> str | None:
    if not should_route_coding_to_hitl(
        confidence,
        threshold,
        payer_flags=agent_result.get("payer_flags"),
    ):
        return None

    cdt_codes = agent_result.get("cdt_codes") if isinstance(agent_result.get("cdt_codes"), list) else []
    patient_name = str(
        encounter.get("patient_name")
        or f"{encounter.get('first_name', '')} {encounter.get('last_name', '')}".strip()
        or "Unknown patient"
    )
    payer = str(encounter.get("insurance") or "Unknown payer")

    return create_rcm_task(
        settings,
        practice_id=practice_id,
        task_type=TASK_TYPE_CODING_REVIEW,
        backend_record_id=decision_id,
        patient_name=patient_name or "Unknown patient",
        payer=payer,
        clinical_note=str(encounter.get("clinical_note") or ""),
        ai_codes=[str(code) for code in cdt_codes],
        ai_summary=str(agent_result.get("justification") or "Coding review required"),
        confidence=confidence,
        pipeline_json={
            "encounter_id": encounter_id,
            "decision_id": decision_id,
            "coding": agent_result,
            "gated": True,
        },
        event_reason="coding_confidence_or_flags",
        actor_label="coding_agent",
    )


def create_hitl_task_from_prior_auth(
    settings: Settings,
    *,
    practice_id: str,
    agent_run_id: str,
    request: dict[str, Any],
    response: dict[str, Any],
) -> str | None:
    if not should_route_prior_auth_to_hitl(response):
        return None

    patient_name = str(request.get("patient_name") or "Unknown patient")
    payer = str(request.get("insurance") or "Unknown payer")
    coding = request.get("coding") if isinstance(request.get("coding"), dict) else {}
    cdt_codes = coding.get("cdt_codes") if isinstance(coding.get("cdt_codes"), list) else []

    return create_rcm_task(
        settings,
        practice_id=practice_id,
        task_type=TASK_TYPE_PRIOR_AUTH_REVIEW,
        backend_record_id=agent_run_id,
        patient_name=patient_name,
        payer=payer,
        clinical_note=str(request.get("clinical_note") or ""),
        ai_codes=[str(code) for code in cdt_codes],
        ai_summary=str(response.get("risk_reason") or "Prior authorization review required"),
        confidence=None,
        pipeline_json={
            "agent_run_id": agent_run_id,
            "prior_auth": response,
            "coding": coding,
            "gated": True,
        },
        event_reason="prior_auth_required",
        actor_label="prior_auth_agent",
    )


def create_hitl_task_from_denial(
    settings: Settings,
    *,
    practice_id: str,
    request: dict[str, Any],
    response: dict[str, Any],
) -> str | None:
    if not should_route_denial_to_hitl(response):
        return None

    patient_name = str(request.get("patient_name") or "Unknown patient")
    claim_id = str(response.get("claim_id") or request.get("claim_id") or "")
    cdt_codes = request.get("cdt_codes") if isinstance(request.get("cdt_codes"), list) else []

    return create_rcm_task(
        settings,
        practice_id=practice_id,
        task_type=TASK_TYPE_DENIAL_REVIEW,
        backend_record_id=claim_id or "unknown_claim",
        backend_claim_id=claim_id,
        patient_name=patient_name,
        payer=str(request.get("insurance_company_name") or "Unknown payer"),
        ai_codes=[str(code) for code in cdt_codes],
        ai_summary=str(response.get("reasoning_summary") or response.get("reason") or "Denial review required"),
        confidence=float(response.get("llm_confidence") or 0.0) or None,
        pipeline_json={
            "claim_id": claim_id,
            "denial": response,
            "request": request,
            "gated": True,
        },
        event_reason="denial_human_review",
        actor_label="denial_agent",
    )
