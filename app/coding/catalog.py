"""In-process CDT/ICD catalog for chairside suggest validation.

Loaded once per process (or TTL) from Supabase. Tests/evals inject a fixture
via ``seed_catalog`` so invalid leftovers can be voided without a live DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.coding.cache import cached
from app.coding.config import CodingSettings, get_coding_settings
from app.integrations.db_tables import CDT_CODES, ICD10_DENTAL_GEM_AXIS
from app.security.phi import scrub_for_log
from supabase import Client

logger = logging.getLogger(__name__)

_CDT_CACHE_KEY = "catalog:cdt"
_ICD_CACHE_KEY = "catalog:icd"


@dataclass(frozen=True)
class CatalogValidation:
    invalid_cdt: frozenset[str] = field(default_factory=frozenset)
    invalid_icd: frozenset[str] = field(default_factory=frozenset)
    loaded: bool = False


@dataclass
class _CatalogOverride:
    cdt: dict[str, dict[str, Any]]
    icd_display: set[str]
    icd_compact: set[str]


_override: _CatalogOverride | None = None


def seed_catalog(
    cdt: dict[str, dict[str, Any]],
    icd: set[str] | None = None,
) -> None:
    """Force a loaded catalog for tests and evals. ``cdt`` keys are CDT codes."""
    global _override
    display: set[str] = set()
    compact: set[str] = set()
    for raw in icd or ():
        token = str(raw).upper().replace(" ", "").strip()
        if not token:
            continue
        display.add(token)
        compact.add(token.replace(".", ""))
    normalized_cdt: dict[str, dict[str, Any]] = {}
    for code, meta in cdt.items():
        key = str(code).upper().strip()
        if key:
            normalized_cdt[key] = dict(meta or {})
    _override = _CatalogOverride(
        cdt=normalized_cdt, icd_display=display, icd_compact=compact
    )


def clear_catalog() -> None:
    """Drop a test seed. Does not clear the shared TTL cache of other keys."""
    global _override
    _override = None


def catalog_is_loaded() -> bool:
    if _override is not None:
        return True
    return bool(_cdt_index()) and bool(_icd_sets()[0] or _icd_sets()[1])


def validate_codes(
    cdt_codes: list[str],
    icd_codes: list[str],
    *,
    supabase: Client | None,
    ttl_seconds: float | None = None,
) -> CatalogValidation:
    """Return invalid codes when the catalog is loaded; otherwise do not void."""
    cdt_index = _cdt_index(supabase=supabase, ttl_seconds=ttl_seconds)
    display, compact = _icd_sets(supabase=supabase, ttl_seconds=ttl_seconds)
    loaded = _override is not None or bool(cdt_index)
    if not loaded:
        return CatalogValidation(loaded=False)

    invalid_cdt = frozenset(
        c for c in _norm_cdt_list(cdt_codes) if c not in cdt_index
    )
    invalid_icd: set[str] = set()
    icd_ready = bool(display or compact) or _override is not None
    if icd_ready:
        for original in icd_codes:
            token = str(original).upper().replace(" ", "").strip()
            if not token:
                continue
            if token in display or token.replace(".", "") in compact:
                continue
            invalid_icd.add(token)
    return CatalogValidation(
        invalid_cdt=invalid_cdt,
        invalid_icd=frozenset(invalid_icd),
        loaded=True,
    )


def cdt_metadata(
    codes: list[str],
    *,
    supabase: Client | None,
    ttl_seconds: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Map CDT → description + requires_* flags from the in-memory catalog."""
    index = _cdt_index(supabase=supabase, ttl_seconds=ttl_seconds)
    out: dict[str, dict[str, Any]] = {}
    for code in _norm_cdt_list(codes):
        row = index.get(code)
        if row:
            out[code] = row
    return out


def eval_fixture_catalog() -> dict[str, dict[str, Any]]:
    """Small CDT set covering golden + chairside evals. Excludes invented codes."""
    codes = (
        "D0120",
        "D0150",
        "D0180",
        "D0210",
        "D0220",
        "D0274",
        "D0330",
        "D1110",
        "D1120",
        "D1206",
        "D1330",
        "D1351",
        "D2140",
        "D2150",
        "D2160",
        "D2161",
        "D2330",
        "D2331",
        "D2332",
        "D2335",
        "D2391",
        "D2392",
        "D2393",
        "D2394",
        "D2740",
        "D2750",
        "D2790",
        "D3310",
        "D3320",
        "D3330",
        "D4341",
        "D4342",
        "D4910",
        "D4921",
        "D7140",
        "D7953",
        "D9944",
    )
    return {
        code: {
            "description": code,
            "requires_tooth": False,
            "requires_surfaces": False,
            "requires_radiograph": False,
        }
        for code in codes
    }


def eval_fixture_icd() -> set[str]:
    return {"K02.9", "K04.02", "K03.81", "K05.30"}


def _norm_cdt_list(codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = str(raw).upper().strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _ttl(ttl_seconds: float | None) -> float:
    if ttl_seconds is not None:
        return float(ttl_seconds)
    cfg: CodingSettings = get_coding_settings()
    return float(cfg.coding_reference_cache_ttl_seconds)


def _cdt_index(
    *,
    supabase: Client | None = None,
    ttl_seconds: float | None = None,
) -> dict[str, dict[str, Any]]:
    if _override is not None:
        return _override.cdt
    if supabase is None:
        return {}

    def _load() -> dict[str, dict[str, Any]]:
        try:
            result = (
                supabase.table(CDT_CODES)
                .select("code,description,requires_tooth,requires_surfaces,requires_radiograph")
                .execute()
            )
            rows = getattr(result, "data", None) or []
            out: dict[str, dict[str, Any]] = {}
            for row in rows:
                code = str(row.get("code") or "").upper().strip()
                if not code:
                    continue
                out[code] = {
                    "description": str(row.get("description") or "").strip(),
                    "requires_tooth": bool(row.get("requires_tooth")),
                    "requires_surfaces": bool(row.get("requires_surfaces")),
                    "requires_radiograph": bool(row.get("requires_radiograph")),
                }
            return out
        except Exception as exc:
            logger.warning("cdt catalog load failed: %s", scrub_for_log(str(exc)))
            return {}

    return cached(_CDT_CACHE_KEY, _ttl(ttl_seconds), _load)


def _icd_sets(
    *,
    supabase: Client | None = None,
    ttl_seconds: float | None = None,
) -> tuple[set[str], set[str]]:
    if _override is not None:
        return _override.icd_display, _override.icd_compact
    if supabase is None:
        return set(), set()

    def _load() -> tuple[set[str], set[str]]:
        try:
            result = (
                supabase.table(ICD10_DENTAL_GEM_AXIS)
                .select("icd10_code,icd10_code_compact")
                .execute()
            )
            rows = getattr(result, "data", None) or []
            display: set[str] = set()
            compact: set[str] = set()
            for row in rows:
                if row.get("icd10_code"):
                    display.add(str(row["icd10_code"]).upper().replace(" ", ""))
                if row.get("icd10_code_compact"):
                    compact.add(str(row["icd10_code_compact"]).upper().replace(" ", ""))
            return display, compact
        except Exception as exc:
            logger.warning("icd catalog load failed: %s", scrub_for_log(str(exc)))
            return set(), set()

    return cached(_ICD_CACHE_KEY, _ttl(ttl_seconds), _load)
