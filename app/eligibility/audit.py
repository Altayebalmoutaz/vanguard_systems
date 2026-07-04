"""Persist audit events — delegates to unified Neon writer when configured."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.audit.writer import write_audit_log
from app.config import get_settings
from app.db.connection import get_neon_dsn
from app.eligibility.config import EligibilitySettings, get_settings as get_eligibility_settings
from app.eligibility.db import get_supabase, insert_audit_log
from app.eligibility.sanitize import scrub_for_log

logger = logging.getLogger(__name__)


def safe_log(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        formatted = message % args if args else message
    except TypeError:
        formatted = message
    logger.info(scrub_for_log(formatted), **kwargs)


def write_audit_event(
    *,
    patient_id: Any,
    event_type: str,
    detail: dict[str, Any],
    settings: EligibilitySettings | None = None,
    practice_id: str | None = None,
) -> None:
    """Persist audit row; detail must not contain raw SSN/MBI."""
    elig_settings = settings or get_eligibility_settings()
    app_settings = get_settings()
    pid = UUID(str(patient_id)) if patient_id else None
    effective_practice = (practice_id or detail.get("practice_id") or "").strip() or None

    if get_neon_dsn(app_settings) and effective_practice:
        write_audit_log(
            app_settings,
            practice_id=effective_practice,
            action=f"eligibility.{event_type}",
            entity_type="patient",
            entity_id=pid,
            performed_by="eligibility_agent",
            metadata={"detail": detail},
        )
        return

    supabase = get_supabase(elig_settings)
    insert_audit_log(
        supabase,
        patient_id=pid,
        event_type=event_type,
        detail=detail,
        practice_id=effective_practice,
        settings=app_settings,
    )
