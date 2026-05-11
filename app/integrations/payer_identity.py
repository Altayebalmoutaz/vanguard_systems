"""
Canonical payer identity — **single directory** `payer_network`.

- **Canonical key:** `payer_id` (PK), aligned with `trading_partner_service_id` for Stedi in seeds.
- **Aliases:** JSON array on `payer_network.aliases` (lowercase strings for matching free-text insurance).

Use `resolve_canonical_payer_id` for human insurance strings; pass Stedi ids through unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from supabase import Client

logger = logging.getLogger(__name__)


def normalize_insurance_alias(s: str) -> str:
    """Lowercase, collapse whitespace — used for exact alias lookups."""
    return " ".join(s.strip().lower().split())


def _iter_payer_alias_pairs(rows: list[dict[str, Any]]) -> Iterator[tuple[str, str]]:
    """Yield (payer_id, normalized_alias_or_display) for resolution."""
    for row in rows:
        pid = str(row.get("payer_id") or "").strip()
        if not pid:
            continue
        raw_aliases = row.get("aliases")
        if isinstance(raw_aliases, list):
            for a in raw_aliases:
                if isinstance(a, str) and a.strip():
                    yield pid, normalize_insurance_alias(a)
        dn = row.get("display_name")
        if dn and str(dn).strip():
            yield pid, normalize_insurance_alias(str(dn))


def _fetch_dental_payer_rows(supabase: Client) -> list[dict[str, Any]]:
    try:
        res = (
            supabase.table("payer_network")
            .select("payer_id,trading_partner_service_id,display_name,aliases,coverage_type")
            .eq("coverage_type", "dental")
            .execute()
        )
        return list(getattr(res, "data", None) or [])
    except Exception as e:
        logger.warning("payer_network dental fetch failed: %s", e)
        return []


def get_payer_directory_row(supabase: Client, payer_id: str) -> dict[str, Any] | None:
    """Single row from payer_network by canonical payer_id."""
    pid = (payer_id or "").strip()
    if not pid:
        return None
    try:
        res = (
            supabase.table("payer_network")
            .select("payer_id,trading_partner_service_id,display_name,coverage_type,aliases")
            .eq("payer_id", pid)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception as e:
        logger.warning("get_payer_directory_row failed: %s", e)
        return None


def resolve_canonical_payer_id(supabase: Client, insurance_or_id: str) -> str | None:
    """
    Resolve a user-supplied insurance string or literal payer id to canonical `payer_id`.

    Order:
    1. Exact match on `payer_id` or `trading_partner_service_id`.
    2. Exact match on full normalized string against `aliases` + `display_name` (from dental payers).
    3. Longest contained alias (min length 5) inside the normalized string.
    """
    raw = (insurance_or_id or "").strip()
    if not raw:
        return None

    candidates = {raw, raw.upper()}
    if raw.isalnum():
        candidates.add(raw.upper())

    for cand in candidates:
        if not cand:
            continue
        try:
            r = (
                supabase.table("payer_network")
                .select("payer_id")
                .eq("payer_id", cand)
                .limit(1)
                .execute()
            )
            rows = getattr(r, "data", None) or []
            if rows:
                return str(rows[0]["payer_id"])
            r2 = (
                supabase.table("payer_network")
                .select("payer_id")
                .eq("trading_partner_service_id", cand)
                .limit(1)
                .execute()
            )
            rows2 = getattr(r2, "data", None) or []
            if rows2:
                return str(rows2[0]["payer_id"])
        except Exception as e:
            logger.debug("payer_network id lookup: %s", e)

    norm = normalize_insurance_alias(raw)
    if not norm:
        return None

    rows = _fetch_dental_payer_rows(supabase)
    pairs = list(_iter_payer_alias_pairs(rows))

    for pid, alias in pairs:
        if alias == norm:
            return pid

    best: tuple[int, str] | None = None
    for pid, alias in pairs:
        if len(alias) < 5:
            continue
        if alias in norm and (best is None or len(alias) > best[0]):
            best = (len(alias), pid)
    if best:
        return best[1]

    return None
