"""Copilot patient directory: OpenDental chart plus eligibility-queue overlap."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from uuid import UUID

from app.config import Settings
from app.dashboard.store import list_eligibility_queue
from app.db.connection import NeonNotConfiguredError
from app.eligibility.config import get_settings as get_elig_settings
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.connections_store import get_connection
from app.integrations.opendental.errors import OpenDentalAPIError, OpenDentalConfigError
from app.integrations.opendental.onboarding_errors import friendly_opendental_test_error

logger = logging.getLogger(__name__)

_SEARCH_KEEP = re.compile(r"[^A-Za-z0-9 \-]")
_DIRECTORY_LIMIT = 50


def copilot_patient_uuid(practice_id: str, pat_num: int) -> UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"opendental:{practice_id}:{int(pat_num)}")


def safe_search_fragment(raw: str | None) -> str:
    cleaned = _SEARCH_KEEP.sub("", raw or "").strip()
    return cleaned[:40]


def stub_profile_from_opendental(
    client: OpenDentalClient,
    *,
    patient_id: UUID,
    od_pat_num: int,
) -> dict[str, Any]:
    first = ""
    last = ""
    try:
        od_patient = client.get_patient(od_pat_num)
        first = od_patient.FName
        last = od_patient.LName
    except OpenDentalAPIError:
        logger.warning("copilot: OpenDental patient %s could not be loaded", od_pat_num)
    return {
        "patient": {
            "id": str(patient_id),
            "first_name": first,
            "last_name": last,
        },
        "latest_eligibility_check": {"id": None, "is_active": None},
        "agent_runs": [],
    }


def list_opendental_directory(
    client: OpenDentalClient,
    *,
    search: str | None = None,
    limit: int = _DIRECTORY_LIMIT,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 75))
    needle = safe_search_fragment(search)
    where = "PatStatus IN (0, 2, 3, 4)"
    if needle:
        like = needle.replace("%", "").replace("_", "")
        where += (
            f" AND (LName LIKE '{like}%' OR FName LIKE '{like}%' "
            f"OR CONCAT(FName, ' ', LName) LIKE '%{like}%')"
        )
        order = "LName, FName"
    else:
        order = "DateTStamp DESC"
    sql = (
        "SELECT PatNum, FName, LName, Birthdate, ChartNumber "
        f"FROM patient WHERE {where} ORDER BY {order} LIMIT {safe_limit}"
    )
    return client.short_query(sql, replay_stem="shortquery_copilot_patients")


def _parse_pat_num(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _display_name(first: object, last: object, fallback: str) -> str:
    combined = f"{first or ''} {last or ''}".strip()
    return combined or fallback


def list_copilot_directory(
    settings: Settings,
    *,
    practice_id: str,
    query: str | None = None,
) -> dict[str, Any]:
    needle = safe_search_fragment(query)
    od_rows: list[dict[str, Any]] = []
    od_connected = False
    od_error: dict[str, str] | None = None
    try:
        connection = get_connection(settings, practice_id=practice_id)
        if connection:
            client = OpenDentalClient.from_connection(connection, settings=get_elig_settings())
            od_rows = list_opendental_directory(client, search=needle or None)
            od_connected = True
    except (OpenDentalConfigError, OpenDentalAPIError, NeonNotConfiguredError) as exc:
        logger.warning("copilot directory: OpenDental unavailable", exc_info=True)
        raw = exc.body if isinstance(exc, OpenDentalAPIError) else str(exc)
        od_error = friendly_opendental_test_error(raw)

    eligibility_rows: list[dict[str, Any]] = []
    try:
        eligibility_rows = list_eligibility_queue(settings, practice_id=practice_id, limit=75)
    except (NeonNotConfiguredError, RuntimeError):
        logger.warning("copilot directory: eligibility queue unavailable", exc_info=True)

    merged: dict[str, dict[str, Any]] = {}

    def upsert(
        *,
        patient_id: str,
        name: str,
        subtitle: str,
        od_pat_num: int | None,
        source: str,
    ) -> None:
        key = f"pat:{od_pat_num}" if od_pat_num is not None else f"id:{patient_id}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                "patient_id": patient_id,
                "od_pat_num": od_pat_num,
                "name": name,
                "subtitle": subtitle,
                "sources": [source],
            }
            return
        if source not in existing["sources"]:
            existing["sources"].append(source)
        if od_pat_num is not None:
            existing["od_pat_num"] = od_pat_num
        if source == "eligibility":
            existing["patient_id"] = patient_id
            if subtitle:
                existing["subtitle"] = subtitle

    for row in od_rows:
        pat_num = _parse_pat_num(row.get("PatNum"))
        if pat_num is None:
            continue
        name = _display_name(row.get("FName"), row.get("LName"), f"PatNum {pat_num}")
        subtitle = str(row.get("ChartNumber") or row.get("Birthdate") or "").strip()
        upsert(
            patient_id=str(copilot_patient_uuid(practice_id, pat_num)),
            name=name,
            subtitle=subtitle,
            od_pat_num=pat_num,
            source="opendental",
        )

    for row in eligibility_rows:
        raw_id = row.get("patient_id")
        if not raw_id:
            continue
        name = str(
            row.get("patient_name")
            or _display_name(row.get("first_name"), row.get("last_name"), "Patient")
        )
        if needle and needle.lower() not in name.lower():
            continue
        subtitle = str(row.get("payer_label") or row.get("primary_payer_id") or "").strip()
        upsert(
            patient_id=str(raw_id),
            name=name,
            subtitle=subtitle,
            od_pat_num=_parse_pat_num(row.get("od_pat_num")),
            source="eligibility",
        )

    patients = list(merged.values())
    patients.sort(key=lambda item: str(item.get("name") or "").lower())
    return {
        "practice_id": practice_id,
        "opendental_connected": od_connected,
        "opendental_error": od_error,
        "patients": patients[:75],
    }
