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


def batch_payer_display_names(supabase: Client, payer_ids: set[str]) -> dict[str, str]:
    """Map payer_id / trading_partner_service_id → display_name (best-effort)."""
    cleaned = sorted({str(p).strip() for p in payer_ids if p and str(p).strip()})
    if not cleaned:
        return {}

    out: dict[str, str] = {}

    def _ingest(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            dn = str(row.get("display_name") or "").strip()
            if not dn:
                continue
            pid = str(row.get("payer_id") or "").strip()
            tps = str(row.get("trading_partner_service_id") or "").strip()
            if pid:
                out[pid] = dn
            if tps:
                out[tps] = dn

    try:
        for i in range(0, len(cleaned), 100):
            chunk = cleaned[i : i + 100]
            res = (
                supabase.table("payer_network")
                .select("payer_id,trading_partner_service_id,display_name")
                .in_("payer_id", chunk)
                .execute()
            )
            _ingest(list(getattr(res, "data", None) or []))
            missing = [c for c in chunk if c not in out]
            if not missing:
                continue
            res2 = (
                supabase.table("payer_network")
                .select("payer_id,trading_partner_service_id,display_name")
                .in_("trading_partner_service_id", missing)
                .execute()
            )
            _ingest(list(getattr(res2, "data", None) or []))
    except Exception as e:
        logger.warning("batch_payer_display_names failed: %s", e)
        return out
    return out


def payer_label_needs_directory_name(label: str | None) -> bool:
    """True when label looks like a Stedi/ElectID code rather than a carrier name."""
    s = (label or "").strip()
    if not s:
        return True
    if any(ch.isspace() for ch in s):
        return False
    return any(ch.isdigit() for ch in s)


def enrich_queue_payer_labels(
    rows: list[dict[str, Any]],
    *,
    supabase: Client | None,
) -> list[dict[str, Any]]:
    """Replace bare payer ids on queue rows with payer_network display names when possible."""
    if not rows or supabase is None:
        return rows

    ids: set[str] = set()
    for row in rows:
        label = str(row.get("payer_label") or "").strip()
        pid = str(row.get("primary_payer_id") or "").strip()
        if not payer_label_needs_directory_name(label):
            continue
        if label:
            ids.add(label)
        if pid:
            ids.add(pid)
    if not ids:
        return rows

    names = batch_payer_display_names(supabase, ids)
    if not names:
        return rows

    for row in rows:
        label = str(row.get("payer_label") or "").strip()
        if not payer_label_needs_directory_name(label):
            continue
        pid = str(row.get("primary_payer_id") or "").strip()
        dn = names.get(label) or names.get(pid)
        if dn:
            row["payer_label"] = dn
    return rows


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
