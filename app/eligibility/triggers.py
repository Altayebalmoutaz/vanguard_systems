"""Layer 1 — Trigger logic + staleness / cache policy."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.eligibility.config import EligibilitySettings, get_settings
from app.eligibility.db import get_latest_eligibility_check, get_supabase
from app.eligibility.models import (
    EligibilityRequest,
    Layer1ErrorCode,
    Layer1ValidationError,
    TriggerEvent,
)
from app.eligibility.sanitize import scrub_for_log

logger = logging.getLogger(__name__)


def _normalize_token(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip().upper()
    return s or None


def _normalize_cdt_codes(codes: list[str] | None) -> list[str]:
    out: list[str] = []
    for c in codes or []:
        n = _normalize_token(c)
        if n:
            out.append(n)
    return out


def should_run_realtime(
    patient_id: UUID,
    payer_id: str,
    trigger_event: TriggerEvent,
    *,
    last_checked_at: datetime | None,
    settings: EligibilitySettings | None = None,
) -> bool:
    """
    Whether to call Stedi real-time (vs cache-only path for this request).

    NEW_PATIENT → always True
    PRE_APPOINTMENT → always True (ignore cache freshness)
    APPOINTMENT_BOOKED → True only if never checked OR last check > TTL days ago
    BATCH_SWEEP → False (batch endpoint only; never real-time loop)
    """
    _ = settings or get_settings()
    if trigger_event is TriggerEvent.BATCH_SWEEP:
        return False
    if trigger_event in (TriggerEvent.NEW_PATIENT, TriggerEvent.PRE_APPOINTMENT):
        return True
    if trigger_event is TriggerEvent.APPOINTMENT_BOOKED:
        if last_checked_at is None:
            return True
        ttl_days = get_settings().cache_ttl_days
        cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
        if last_checked_at.tzinfo is None:
            last_checked_at = last_checked_at.replace(tzinfo=UTC)
        return last_checked_at < cutoff
    return True


def is_cache_fresh(last_checked_at: datetime | None, ttl_days: int) -> bool:
    if last_checked_at is None:
        return False
    if last_checked_at.tzinfo is None:
        last_checked_at = last_checked_at.replace(tzinfo=UTC)
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
    return last_checked_at >= cutoff


def resolve_cached_vs_api(
    request: EligibilityRequest,
    *,
    settings: EligibilitySettings | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """
    Returns ("api", None) or ("cache", cached_row).

    PRE_APPOINTMENT always forces API.
    BATCH_SWEEP should not use this for single check — caller routes to batch.
    """
    s = settings or get_settings()
    supabase = get_supabase(s)

    if request.trigger_event is TriggerEvent.BATCH_SWEEP:
        return "batch", None

    if request.trigger_event is TriggerEvent.PRE_APPOINTMENT:
        return "api", None

    latest = get_latest_eligibility_check(supabase, request.patient_id, request.primary_payer_id)
    last_at: datetime | None = None
    if latest and latest.get("checked_at"):
        raw = latest["checked_at"]
        if isinstance(raw, str):
            last_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        elif isinstance(raw, datetime):
            last_at = raw

    if request.trigger_event is TriggerEvent.NEW_PATIENT:
        return "api", None

    if request.trigger_event is TriggerEvent.APPOINTMENT_BOOKED:
        if not is_cache_fresh(last_at, s.cache_ttl_days):
            return "api", None
        if should_run_realtime(
            request.patient_id,
            request.primary_payer_id,
            request.trigger_event,
            last_checked_at=last_at,
            settings=s,
        ):
            return "api", None
        return "cache", latest

    return "api", None


def layer0_supabase_validation(
    request: EligibilityRequest,
    *,
    settings: EligibilitySettings | None = None,
) -> tuple[EligibilityRequest, list[str]]:
    """
    DB-backed Layer 0: dental payer + CDT filter.
    Returns (possibly mutated request with filtered cdt_codes, warnings).
    """
    from app.eligibility.db import fetch_existing_cdt_codes, validate_dental_payer

    s = settings or get_settings()
    supabase = get_supabase(s)
    warnings: list[str] = []

    primary_payer_id = _normalize_token(request.primary_payer_id) or ""
    secondary_payer_id = _normalize_token(request.secondary_payer_id)
    codes = _normalize_cdt_codes(request.cdt_codes)

    if not validate_dental_payer(supabase, primary_payer_id):
        raise Layer1ValidationError(
            Layer1ErrorCode.INVALID_PRIMARY_PAYER,
            f"primary_payer_id not in dental payer_network: {scrub_for_log(primary_payer_id)}",
            detail={"field": "primary_payer_id", "payer_id": primary_payer_id},
        )

    if secondary_payer_id and not validate_dental_payer(supabase, secondary_payer_id):
        raise Layer1ValidationError(
            Layer1ErrorCode.INVALID_SECONDARY_PAYER,
            f"secondary_payer_id not in dental payer_network: {scrub_for_log(secondary_payer_id)}",
            detail={"field": "secondary_payer_id", "payer_id": secondary_payer_id},
        )

    if not codes:
        updated = request.model_copy(
            update={
                "primary_payer_id": primary_payer_id,
                "secondary_payer_id": secondary_payer_id,
                "cdt_codes": None,
            }
        )
        return updated, warnings

    valid = fetch_existing_cdt_codes(supabase, codes)
    filtered = [c for c in codes if c in valid]
    invalid = [c for c in codes if c not in valid]
    for inv in invalid:
        msg = f"L1_INVALID_CDT_REMOVED|code={inv}|Removed invalid CDT code not in cdt_codes table"
        warnings.append(msg)
        logger.warning(scrub_for_log(msg))

    updated = request.model_copy(
        update={
            "primary_payer_id": primary_payer_id,
            "secondary_payer_id": secondary_payer_id,
            "cdt_codes": filtered or None,
        }
    )
    return updated, warnings
