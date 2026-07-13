"""Neon-backed claim draft and accepted-claim persistence."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import get_neon_dsn, neon_connection

logger = logging.getLogger(__name__)

CLAIM_STATUS_DRAFT = "draft"
CLAIM_STATUS_PENDING_AUTH = "pending_auth"
CLAIM_STATUS_SUBMITTED = "submitted"


def persist_claim_draft(
    settings: Settings,
    *,
    practice_id: str,
    patient_id: UUID | None,
    clinical_note: str,
    provider: str | None,
    coding: dict[str, Any],
    prior_auth: dict[str, Any],
    claim_draft: dict[str, Any],
) -> str | None:
    """Insert a draft row into ``rcm.claims`` after pipeline claim assembly."""
    if not get_neon_dsn(settings):
        return None

    draft_status = str(claim_draft.get("status") or CLAIM_STATUS_DRAFT)
    blockers = claim_draft.get("blockers") if isinstance(claim_draft.get("blockers"), list) else []
    details = claim_draft.get("details") if isinstance(claim_draft.get("details"), dict) else {}
    claim_payload = (
        claim_draft.get("claim_payload")
        if isinstance(claim_draft.get("claim_payload"), dict)
        else {}
    )

    cdt_lines = {
        "cdt_codes": details.get("cdt_codes") or coding.get("cdt_codes") or [],
        "service_lines": claim_payload.get("service_lines") or [],
        "claim_payload": claim_payload,
    }
    icd10_codes = details.get("icd10_codes") or coding.get("icd10_codes") or []

    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    insert into rcm.claims (
                      practice_id, patient_id, provider, raw_note, status,
                      cdt_lines, icd10_codes, compliance_status, compliance_flags,
                      compliance_note, coded_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        practice_id,
                        patient_id,
                        provider,
                        clinical_note,
                        draft_status,
                        Jsonb(cdt_lines),
                        Jsonb(icd10_codes),
                        str(prior_auth.get("status") or "pending_review"),
                        Jsonb(blockers),
                        str(prior_auth.get("risk_reason") or ""),
                        datetime.now(UTC),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if row and row.get("id"):
            return str(row["id"])
    except Exception as exc:
        logger.warning("claim draft persist failed: %s", exc)
    return None


def update_claim_status(
    settings: Settings,
    *,
    practice_id: str,
    claim_id: UUID,
    status: str,
) -> None:
    if not get_neon_dsn(settings):
        return
    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update rcm.claims
                    set status = %s
                    where id = %s and practice_id = %s
                    """,
                    (status, claim_id, practice_id),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("claim status update failed: %s", exc)


def insert_accepted_claim(
    settings: Settings,
    *,
    practice_id: str,
    task_id: UUID,
    backend_record_id: str,
    backend_claim_id: str,
    patient_name: str,
    payer: str | None,
    final_codes: list[str],
    final_summary: str | None,
    confidence: float | None,
    source_pipeline_json: dict[str, Any] | None,
) -> str | None:
    """Record human acceptance of a task into ``rcm.accepted_claims``."""
    if not get_neon_dsn(settings):
        return None

    try:
        with neon_connection(settings, practice_id=practice_id) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    insert into rcm.accepted_claims (
                      practice_id, task_id, backend_record_id, backend_claim_id,
                      patient_name, payer, final_codes, final_summary, confidence,
                      source_pipeline_json
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        practice_id,
                        task_id,
                        backend_record_id,
                        backend_claim_id,
                        patient_name,
                        payer,
                        final_codes,
                        final_summary,
                        confidence,
                        Jsonb(source_pipeline_json or {}),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if row and row.get("id"):
            return str(row["id"])
    except Exception as exc:
        logger.warning("accepted_claim insert failed: %s", exc)
    return None
