"""Supabase-only reference data reads (non-PHI payer/CDT/network tables)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.eligibility.config import EligibilitySettings, get_settings
from supabase import Client, create_client

_supabase: Client | None = None


def get_supabase(settings: EligibilitySettings | None = None) -> Client:
    global _supabase
    s = settings or get_settings()
    if not s.supabase_url or not s.supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set for database operations")
    if _supabase is None:
        _supabase = create_client(s.supabase_url, s.supabase_key)
    return _supabase


def reset_supabase_client() -> None:
    """Test helper to clear cached client."""
    global _supabase
    _supabase = None


def validate_dental_payer(supabase: Client, trading_partner_service_id: str) -> bool:
    """Return True if payer exists in payer_network with coverage_type = dental."""
    res = (
        supabase.table("payer_network")
        .select("payer_id")
        .eq("trading_partner_service_id", trading_partner_service_id)
        .eq("coverage_type", "dental")
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return len(rows) > 0


def fetch_existing_cdt_codes(supabase: Client, codes: list[str]) -> set[str]:
    """Return set of CDT codes that exist in public.cdt_codes (code column assumed)."""
    if not codes:
        return set()
    unique = sorted({c.strip().upper() for c in codes if c and str(c).strip()})
    if not unique:
        return set()
    found: set[str] = set()
    chunk_size = 200
    for i in range(0, len(unique), chunk_size):
        chunk = unique[i : i + chunk_size]
        try:
            res = supabase.table("cdt_codes").select("code").in_("code", chunk).execute()
        except Exception:
            res = supabase.table("cdt_codes").select("cdt_code").in_("cdt_code", chunk).execute()
        for row in res.data or []:
            c = row.get("code") or row.get("cdt_code")
            if c:
                found.add(str(c).strip().upper())
    return found


def _parse_pg_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val[:10])
        except ValueError:
            return None
    return None


def _provider_network_row_active_on(r: dict[str, Any], as_of: date) -> bool:
    start = _parse_pg_date(r.get("effective_from"))
    if start is not None and as_of < start:
        return False
    end = _parse_pg_date(r.get("effective_to"))
    return not (end is not None and as_of > end)


def fetch_active_provider_payer_network(
    supabase: Client,
    *,
    practice_id: str,
    rendering_provider_npi: str,
    payer_trading_partner_id: str,
    provider_service_location_key: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    """
    Resolve provider_payer_network row for fee-path (INN vs OON).

    Prefers an exact provider_service_location_key match when provided; otherwise falls back
    to a row with a NULL site key (practice default for that NPI + payer).
    """
    as_of_d = as_of or datetime.now(UTC).date()
    pid = (practice_id or "").strip()
    npi = (rendering_provider_npi or "").strip()
    payer = (payer_trading_partner_id or "").strip()
    if not pid or not npi or not payer:
        return None

    res = (
        supabase.table("provider_payer_network")
        .select("*")
        .eq("practice_id", pid)
        .eq("rendering_provider_npi", npi)
        .eq("payer_id", payer)
        .execute()
    )

    rows = [
        r
        for r in (res.data or [])
        if isinstance(r, dict) and _provider_network_row_active_on(r, as_of_d)
    ]
    if not rows:
        return None

    loc = (provider_service_location_key or "").strip() or None
    if loc:
        for r in rows:
            if (r.get("provider_service_location_key") or "").strip() == loc:
                return r

    for r in rows:
        sk = r.get("provider_service_location_key")
        if sk is None or (isinstance(sk, str) and not sk.strip()):
            return r

    return rows[0]


def fetch_payer_fee_schedule_as_dict(
    supabase: Client, payer_id: str, as_of: date | None = None
) -> dict[str, Any]:
    """
    Build fee_schedule dict for cost_calculator:
    { cdt_code: ucr_or_billed_float, 'contracted': { payer_id: { cdt: fee } }, 'billed': { cdt: fee } }
    """
    as_of = as_of or datetime.now(UTC).date()
    res = (
        supabase.table("payer_fee_schedules")
        .select("*")
        .eq("payer_id", payer_id)
        .lte("effective_date", as_of.isoformat())
        .order("effective_date", desc=True)
        .execute()
    )
    contracted: dict[str, dict[str, float]] = {payer_id: {}}
    billed: dict[str, float] = {}
    for row in res.data or []:
        code = str(row.get("cdt_code") or "").strip().upper()
        if not code:
            continue
        fee = float(row.get("contracted_fee") or 0)
        if code not in contracted[payer_id]:
            contracted[payer_id][code] = fee
        billed.setdefault(code, fee)
    return {"contracted": contracted, "billed": billed}


def payer_requires_prior_auth(supabase: Client, payer_id: str, cdt_codes: list[str]) -> bool:
    for code in cdt_codes:
        c = code.strip().upper()
        res = (
            supabase.table("payer_prior_auth_rules")
            .select("auth_required")
            .eq("payer_id", payer_id)
            .eq("cdt_code", c)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows and rows[0].get("auth_required") is True:
            return True
    return False


def decimal_or_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float)):
        return v
    return float(v)
