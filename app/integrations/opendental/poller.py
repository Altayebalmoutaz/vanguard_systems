"""In-process OpenDental appointment poller.

Replaces the manual ``scripts/watch_od_appointments.py`` loop: when enabled, this runs
as a FastAPI background task, polls OpenDental for appointments across a configurable
date window, and fires the shared ``run_from_opendental`` flow (with write-back) for
each new patient.

Idempotency: a patient is processed at most once per day, enforced by an in-memory set
(fast path) plus a Supabase timestamp check (survives process restarts). All OD/Stedi
failures are logged and never stop the loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid5, NAMESPACE_DNS

import httpx

from app.eligibility.config import EligibilitySettings
from app.eligibility.models import TriggerEvent

logger = logging.getLogger(__name__)


def od_headers(developer_key: str, customer_key: str) -> dict[str, str]:
    return {"Authorization": f"ODFHIR {developer_key.strip()}/{customer_key.strip()}"}


def fetch_appointments(
    *,
    base_url: str,
    headers: dict[str, str],
    on_date: str,
    timeout: float,
) -> list[dict[str, Any]]:
    """GET /appointments for a single date. Returns [] on any error (poller-friendly)."""
    url = f"{base_url.rstrip('/')}/appointments"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers, params={"date": on_date})
        if resp.status_code >= 400:
            logger.warning("OD GET /appointments %s: %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001 - poller resilience
        logger.warning("OD appointment fetch failed: %s: %s", type(exc).__name__, exc)
        return []


def _opendental_patient_uuid(pat_num: int):  # type: ignore[no-untyped-def]
    """Must match mapping.od_to_eligibility_request patient_id derivation."""
    return uuid5(NAMESPACE_DNS, f"opendental:{pat_num}")


def _poll_dates(window_days: int) -> list[str]:
    today = date.today()
    days = max(0, int(window_days))
    return [(today + timedelta(days=i)).isoformat() for i in range(days + 1)]


def _checked_today(pat_num: int) -> bool:
    """True when this patient already has an eligibility_checks row dated today."""
    try:
        from app.eligibility.db import get_latest_eligibility_for_patient, get_supabase

        supabase = get_supabase()
        latest = get_latest_eligibility_for_patient(supabase, _opendental_patient_uuid(pat_num))
        if not latest:
            return False
        checked_at = latest.get("checked_at")
        if not checked_at:
            return False
        try:
            checked_date = datetime.fromisoformat(str(checked_at)[:10]).date()
        except ValueError:
            return False
        return checked_date == date.today()
    except Exception as exc:  # noqa: BLE001 - DB optional; fall back to in-memory dedupe
        logger.debug("poller DB dedupe check skipped: %s", exc)
        return False


async def _poll_once(
    runner: Callable[..., dict[str, Any]],
    settings: EligibilitySettings,
    *,
    seen: set[int],
    cdt_codes: list[str],
) -> None:
    headers = od_headers(settings.opendental_developer_key, settings.opendental_customer_key)
    for on_date in _poll_dates(settings.opendental_auto_poll_date_window_days):
        appointments = await asyncio.to_thread(
            fetch_appointments,
            base_url=settings.opendental_base_url,
            headers=headers,
            on_date=on_date,
            timeout=settings.opendental_timeout_seconds,
        )
        for apt in appointments:
            pat_num = apt.get("PatNum")
            if not pat_num:
                continue
            pat_num = int(pat_num)
            if pat_num in seen:
                continue
            if await asyncio.to_thread(_checked_today, pat_num):
                seen.add(pat_num)
                continue
            seen.add(pat_num)
            try:
                out = await asyncio.to_thread(
                    runner,
                    pat_num=pat_num,
                    trigger_event=TriggerEvent.PRE_APPOINTMENT,
                    cdt_codes=cdt_codes,
                    write_back=True,
                    settings=settings,
                )
                routing = ((out.get("primary") or {}).get("routing")) or {}
                logger.warning(
                    "poller processed PatNum=%s status=%s", pat_num, routing.get("status", "-")
                )
            except Exception as exc:  # noqa: BLE001 - one patient failing must not stop polling
                logger.warning("poller PatNum=%s failed: %s: %s", pat_num, type(exc).__name__, exc)


async def _poll_loop(runner: Callable[..., dict[str, Any]], settings: EligibilitySettings) -> None:
    seen: set[int] = set()
    cdt_codes = [c.strip() for c in (settings.opendental_auto_poll_cdt_codes or "").split(",") if c.strip()]
    interval = max(1.0, float(settings.opendental_auto_poll_interval_seconds))
    logger.info("OpenDental poller loop started (interval=%ss, cdt=%s)", interval, cdt_codes)
    while True:
        try:
            await _poll_once(runner, settings, seen=seen, cdt_codes=cdt_codes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let one pass kill the loop
            logger.warning("poller pass failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


def start_appointment_poller(
    runner: Callable[..., dict[str, Any]], settings: EligibilitySettings
) -> asyncio.Task[None]:
    """Launch the polling loop as a background asyncio task."""
    return asyncio.create_task(_poll_loop(runner, settings))
