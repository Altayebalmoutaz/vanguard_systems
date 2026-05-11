"""Audit log writer (SSN fallback, re-checks, routing)."""

from __future__ import annotations

import logging
from typing import Any

from app.eligibility.config import EligibilitySettings, get_settings
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
) -> None:
    """Persist audit row; detail must not contain raw SSN/MBI."""
    s = settings or get_settings()
    supabase = get_supabase(s)
    from uuid import UUID

    pid = UUID(str(patient_id)) if patient_id else None
    insert_audit_log(supabase, patient_id=pid, event_type=event_type, detail=detail)
