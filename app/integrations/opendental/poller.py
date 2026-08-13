"""In-process OpenDental appointment poller (multi-clinic).

When enabled, this runs as a FastAPI background task. If ``rcm.opendental_connections``
has enabled rows, each clinic is polled with its own base URL / Customer key /
interval / CDT codes, and every pass writes ``last_poll_*`` + health back to the row
so the dashboard shows live poller state. Without connection rows it falls back to
the legacy single-clinic env configuration (``OPENDENTAL_*``).

When ``PILOT_SHADOW_MODE=1``, write-back is disabled and eligibility results are logged
to ``platform.pilot_shadow_events`` for ROI tracking.

Idempotency: a patient is processed at most once per successful Stedi check per day.
Same-day ``failed`` / ``needs_attention`` rows may be re-enqueued. Intra-pass PatNum
dedup uses a local set; auto-poll does not keep a process-lifetime seen set.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.config import get_settings as get_app_settings
from app.db.connection import get_neon_dsn
from app.eligibility.config import EligibilitySettings
from app.eligibility.models import TriggerEvent
from app.integrations.opendental.cdt_resolve import resolve_appointment_procedures
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.connections_store import (
    list_enabled_connections,
    record_poll_result,
)
from app.integrations.opendental.eligibility_enqueue import (
    enqueue_od_eligibility_check,
    od_request_exists_today,
    opendental_patient_uuid,
)
from app.integrations.opendental.errors import OpenDentalAPIError, OpenDentalConfigError
from app.integrations.opendental.models import ODProcedureLog
from app.integrations.opendental.reverify import effective_poll_window_days

logger = logging.getLogger(__name__)

# Client-side filter: OD Remote API may ignore AptStatus= query params.
_LIVE_APT_STATUSES = frozenset({"scheduled", "asap"})


def od_headers(developer_key: str, customer_key: str) -> dict[str, str]:
    return {"Authorization": f"ODFHIR {developer_key.strip()}/{customer_key.strip()}"}


def is_live_appointment(apt: dict[str, Any]) -> bool:
    """True for appointments that should trigger eligibility (not complete/broken)."""
    status = str(apt.get("AptStatus") or "").strip().lower()
    return status in _LIVE_APT_STATUSES


def appointment_date_from_row(apt: dict[str, Any], *, fallback: str) -> date:
    raw = apt.get("AptDateTime") or apt.get("AptDate") or fallback
    text = str(raw).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.fromisoformat(fallback)


def fetch_appointments(
    *,
    base_url: str,
    headers: dict[str, str],
    on_date: str,
    timeout: float,
) -> list[dict[str, Any]]:
    """GET /appointments for a single date. Raises on HTTP/network errors."""
    url = f"{base_url.rstrip('/')}/appointments"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers, params={"date": on_date})
    except OpenDentalAPIError:
        raise
    except Exception as exc:
        raise OpenDentalAPIError(
            f"OpenDental appointment fetch failed: {type(exc).__name__}: {exc}"
        ) from exc
    if resp.status_code >= 400:
        raise OpenDentalAPIError(
            f"OpenDental GET /appointments failed: {resp.status_code}",
            status_code=resp.status_code,
            body=(resp.text or "")[:200],
        )
    try:
        data = resp.json()
    except Exception as exc:
        raise OpenDentalAPIError("OpenDental appointments payload was not JSON") from exc
    if not isinstance(data, list):
        raise OpenDentalAPIError("OpenDental appointments payload was not a list")
    return data


def _poll_dates(window_days: int) -> list[str]:
    today = date.today()
    days = max(0, int(window_days))
    return [(today + timedelta(days=i)).isoformat() for i in range(days + 1)]


def _checked_today(pat_num: int) -> bool:
    """True when this patient already has an eligibility_checks row dated today.

    Blocks a second Stedi run after a successful check so writeback retries stay
    on the writeback pipeline, not a new 270/271.
    """
    try:
        from app.eligibility.db import get_latest_eligibility_for_patient, get_supabase

        supabase = get_supabase()
        latest = get_latest_eligibility_for_patient(supabase, opendental_patient_uuid(pat_num))
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
    except Exception as exc:
        logger.debug("poller DB dedupe check skipped: %s", exc)
        return False


def run_connection_poll(
    settings: EligibilitySettings,
    app_settings: Settings,
    connection: dict[str, Any],
    *,
    seen: set[int] | None = None,
    for_auto_poll: bool = False,
) -> dict[str, Any]:
    """One synchronous poll pass for one clinic connection row.

    Shared by the poller loop and the dashboard 'Poll now' pipeline job. Always
    records the outcome on the connection row so the dashboard reflects it.
    """
    practice_id = str(connection.get("practice_id") or "")
    seen = seen if seen is not None else set()
    try:
        client = OpenDentalClient.from_connection(connection, settings=settings)
    except OpenDentalConfigError as exc:
        record_poll_result(app_settings, practice_id=practice_id, status="error", error=str(exc))
        return {
            "practice_id": practice_id,
            "status": "error",
            "error": str(exc),
            "appointments": 0,
            "processed": 0,
            "skipped_today": 0,
            "failed": 0,
        }

    headers = od_headers(client.developer_key, client.customer_key)
    clinic_defaults = [
        c.strip() for c in str(connection.get("cdt_codes") or "").split(",") if c.strip()
    ]
    window_days = effective_poll_window_days(
        connection,
        default_reverify_days=int(getattr(settings, "opendental_reverify_window_days", 3) or 3),
        expand_when_zero=for_auto_poll,
    )

    apts_by_pat: dict[int, list[int]] = {}
    apt_date_by_pat: dict[int, date] = {}
    total_appointments = 0
    try:
        for on_date in _poll_dates(window_days):
            appointments = fetch_appointments(
                base_url=client.base_url,
                headers=headers,
                on_date=on_date,
                timeout=settings.opendental_timeout_seconds,
            )
            for apt in appointments:
                if not is_live_appointment(apt):
                    continue
                pat_raw = apt.get("PatNum")
                if not pat_raw:
                    continue
                pat_num = int(pat_raw)
                apt_raw = apt.get("AptNum")
                if apt_raw is None:
                    continue
                apt_num = int(apt_raw)
                total_appointments += 1
                bucket = apts_by_pat.setdefault(pat_num, [])
                if apt_num not in bucket:
                    bucket.append(apt_num)
                apt_date = appointment_date_from_row(apt, fallback=on_date)
                prev = apt_date_by_pat.get(pat_num)
                if prev is None or apt_date < prev:
                    apt_date_by_pat[pat_num] = apt_date
    except OpenDentalAPIError as exc:
        logger.warning("OD appointment fetch failed practice=%s: %s", practice_id, exc)
        record_poll_result(app_settings, practice_id=practice_id, status="error", error=str(exc))
        return {
            "practice_id": practice_id,
            "status": "error",
            "error": str(exc),
            "appointments": 0,
            "processed": 0,
            "skipped_today": 0,
            "failed": 0,
        }

    processed = 0
    failed = 0
    skipped_today = 0
    skip_same_day_dedupe = bool(
        getattr(settings, "opendental_poll_skip_same_day_dedupe", False)
    )
    if skip_same_day_dedupe:
        logger.warning(
            "OD same-day poll dedupe disabled (OD_POLL_SKIP_SAME_DAY_DEDUPE) practice=%s",
            practice_id,
        )
    for pat_num, apt_nums in apts_by_pat.items():
        if pat_num in seen:
            skipped_today += 1
            continue
        if not skip_same_day_dedupe and (
            _checked_today(pat_num)
            or od_request_exists_today(app_settings, practice_id=practice_id, pat_num=pat_num)
        ):
            seen.add(pat_num)
            skipped_today += 1
            continue
        try:
            proc_rows: list[ODProcedureLog] = []
            for apt_num in apt_nums:
                proc_rows.extend(client.get_procedurelogs_for_appointment(apt_num))
            resolved = resolve_appointment_procedures(proc_rows, clinic_defaults=clinic_defaults)
            row = enqueue_od_eligibility_check(
                app_settings,
                practice_id=practice_id,
                pat_num=pat_num,
                connection=connection,
                client=client,
                cdt_codes=resolved.cdt_codes,
                trigger_event=TriggerEvent.PRE_APPOINTMENT,
                resolve=resolved,
                apt_nums=apt_nums,
                appointment_date=apt_date_by_pat.get(pat_num),
                skip_same_day_dedupe=skip_same_day_dedupe,
            )
            if row:
                seen.add(pat_num)
                processed += 1
                logger.warning(
                    "poller enqueued practice=%s PatNum=%s request_id=%s cdt_source=%s cdts=%s",
                    practice_id,
                    pat_num,
                    row.get("id"),
                    resolved.cdt_source,
                    resolved.cdt_codes,
                )
            else:
                seen.add(pat_num)
                skipped_today += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "poller practice=%s PatNum=%s failed: %s: %s",
                practice_id,
                pat_num,
                type(exc).__name__,
                exc,
            )

    status = "ok" if failed == 0 else "error"
    error = None if failed == 0 else f"{failed} patient(s) failed during poll"
    record_poll_result(
        app_settings,
        practice_id=practice_id,
        status=status,
        appointments=total_appointments,
        error=error,
    )
    return {
        "practice_id": practice_id,
        "status": status,
        "appointments": total_appointments,
        "processed": processed,
        "skipped_today": skipped_today,
        "failed": failed,
        "error": error,
    }


def _connection_due(connection: dict[str, Any], *, now: datetime, default_interval: float) -> bool:
    last = connection.get("last_poll_at")
    if not isinstance(last, datetime):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    try:
        interval = float(connection.get("poll_interval_seconds") or default_interval)
    except (TypeError, ValueError):
        interval = default_interval
    return (now - last).total_seconds() >= max(1.0, interval)


async def _poll_loop(settings: EligibilitySettings) -> None:
    app_settings = get_app_settings()
    interval = max(1.0, float(settings.opendental_auto_poll_interval_seconds))
    logger.info(
        "OpenDental poller loop started (interval=%ss, shadow_mode=%s)",
        interval,
        settings.pilot_shadow_mode,
    )
    from app.db.leases import LEASE_OD_POLLER, try_lease

    def _leased_pass() -> str:
        """One full poll pass under the single-flight lease (sync, thread-run)."""
        with try_lease(app_settings, LEASE_OD_POLLER) as acquired:
            if not acquired:
                return "lease_held_elsewhere"
            connections: list[dict[str, Any]] = []
            if get_neon_dsn(app_settings):
                try:
                    connections = list_enabled_connections(app_settings)
                except Exception as exc:
                    logger.debug("opendental_connections lookup skipped: %s", exc)
            if not connections:
                return "legacy"
            now = datetime.now(UTC)
            for connection in connections:
                if not _connection_due(connection, now=now, default_interval=interval):
                    continue
                # Fresh seen each pass so a same-day failed request can be retried;
                # intra-pass PatNum dedup still happens inside run_connection_poll.
                run_connection_poll(
                    settings, app_settings, connection, for_auto_poll=True
                )
            return "multi_clinic"

    while True:
        try:
            outcome = await asyncio.to_thread(_leased_pass)
            if outcome == "legacy":
                logger.warning(
                    "OpenDental poller: no connection rows configured; skipping legacy path"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("poller pass failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


def start_appointment_poller(settings: EligibilitySettings) -> asyncio.Task[None]:
    """Launch the polling loop as a background asyncio task."""
    return asyncio.create_task(_poll_loop(settings))
