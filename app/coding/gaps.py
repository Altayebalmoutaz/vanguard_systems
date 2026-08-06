"""Deterministic gap checks and payer documentation lookups (no LLM)."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.coding.cache import cached
from app.coding.config import CodingSettings
from app.coding.schemas import (
    CodingSuggestRequest,
    MissingInfoCode,
    MissingInfoItem,
    ProcedureLine,
)
from app.integrations.db_tables import CDT_CODES, PAYER_RULES
from supabase import Client

logger = logging.getLogger(__name__)

# Restorative / endo / crown-ish families that typically need tooth + surface.
_TOOTH_REQUIRED_PREFIXES = ("D2", "D3", "D4", "D6", "D7")
_SURFACE_REQUIRED_PREFIXES = ("D2",)  # amalgam/composite/resin restorations
_IMAGING_CODES = frozenset(
    {
        "D0210",
        "D0220",
        "D0230",
        "D0240",
        "D0250",
        "D0251",
        "D0270",
        "D0272",
        "D0273",
        "D0274",
        "D0330",
        "D0364",
        "D0365",
        "D0366",
        "D0367",
        "D0372",
    }
)
_RADIOGRAPH_ATTACHMENT_HINTS = (
    "radiograph",
    "xray",
    "x-ray",
    "bitewing",
    "pano",
    "periapical",
    "pa",
    "cbct",
    "image",
)


def _looks_restorative_from_findings(line: ProcedureLine) -> bool:
    blob = " ".join(line.findings).lower()
    return any(
        token in blob
        for token in (
            "caries",
            "decay",
            "cavity",
            "composite",
            "amalgam",
            "filling",
            "restoration",
            "occlusal",
            "interproximal",
        )
    )


def pre_check_line(line: ProcedureLine) -> list[MissingInfoItem]:
    """Gaps known before coding (tooth/surface/findings)."""
    missing: list[MissingInfoItem] = []
    restorative = _looks_restorative_from_findings(line)
    if restorative and not line.tooth_numbers:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.TOOTH_MISSING,
                message=f"Line {line.line_id}: tooth number required for restorative finding",
            )
        )
    if restorative and not line.surfaces:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.SURFACE_MISSING,
                message=f"Line {line.line_id}: surface(s) required for restorative finding",
            )
        )
    if not line.findings and not line.tooth_numbers and not line.surfaces:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.FINDING_MISSING,
                message=f"Line {line.line_id}: provide findings and/or tooth/surface detail",
            )
        )
    return missing


def pre_check_request(request: CodingSuggestRequest) -> list[MissingInfoItem]:
    global_missing: list[MissingInfoItem] = []
    if not (request.payer.id or request.payer.name):
        global_missing.append(
            MissingInfoItem(
                code=MissingInfoCode.PAYER_MISSING,
                message="payer.id or payer.name helps documentation and rule matching",
            )
        )
    if request.patient.age is None:
        global_missing.append(
            MissingInfoItem(
                code=MissingInfoCode.AGE_MISSING,
                message="patient.age is recommended for age-gated payer rules",
            )
        )
    if not request.procedures:
        global_missing.append(
            MissingInfoItem(
                code=MissingInfoCode.PROCEDURE_EMPTY,
                message="at least one procedure line is required",
            )
        )
    note = (request.supporting_note or "").strip()
    if note and len(note) < 12:
        global_missing.append(
            MissingInfoItem(
                code=MissingInfoCode.SUPPORTING_NOTE_THIN,
                message="supporting_note is very short; add clinical detail if available",
            )
        )
    return global_missing


def post_check_line(
    line: ProcedureLine,
    *,
    cdt_code: str | None,
    attachments_present: list[str],
    confidence: float,
    threshold: float,
    cdt_meta: dict[str, Any] | None = None,
) -> list[MissingInfoItem]:
    """Gaps after a CDT is proposed (imaging docs, restorative completeness)."""
    missing: list[MissingInfoItem] = []
    code = (cdt_code or "").upper().strip()
    if not code:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.CDT_UNCERTAIN,
                message=f"Line {line.line_id}: no CDT recommendation produced",
            )
        )
        return missing

    meta = cdt_meta or {}
    needs_tooth = bool(meta.get("requires_tooth")) or code.startswith(
        _TOOTH_REQUIRED_PREFIXES
    )
    needs_surface = bool(meta.get("requires_surfaces")) or code.startswith(
        _SURFACE_REQUIRED_PREFIXES
    )
    needs_radio = bool(meta.get("requires_radiograph")) or (
        code in _IMAGING_CODES or code.startswith("D02") or code.startswith("D03")
    )

    if needs_tooth and not line.tooth_numbers:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.TOOTH_MISSING,
                message=f"Line {line.line_id}: tooth number required for {code}",
            )
        )
    if needs_surface and not line.surfaces:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.SURFACE_MISSING,
                message=f"Line {line.line_id}: surface(s) required for {code}",
            )
        )
    if needs_radio and not _has_radiograph_attachment(attachments_present):
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.RADIOGRAPH_MISSING,
                message=(
                    f"Line {line.line_id}: radiographic documentation expected for {code}"
                ),
            )
        )
    if confidence < threshold:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.CDT_UNCERTAIN,
                message=(
                    f"Line {line.line_id}: confidence {confidence:.2f} below "
                    f"threshold {threshold:.2f}"
                ),
            )
        )
    return missing


def _has_radiograph_attachment(attachments: list[str]) -> bool:
    joined = " ".join(attachments).lower()
    return any(hint in joined for hint in _RADIOGRAPH_ATTACHMENT_HINTS)


def fetch_cdt_metadata(
    supabase: Client | None,
    codes: list[str],
    *,
    ttl_seconds: float,
) -> dict[str, dict[str, Any]]:
    """Map CDT code → description + requires_* flags from analytics.cdt_codes (cached)."""
    normalized = sorted({c.upper().strip() for c in codes if c and str(c).strip()})
    if not normalized or supabase is None:
        return {}

    def _load() -> dict[str, dict[str, Any]]:
        try:
            result = (
                supabase.table(CDT_CODES)
                .select(
                    "code,description,requires_tooth,requires_surfaces,requires_radiograph"
                )
                .in_("code", normalized)
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
            logger.warning("cdt metadata lookup failed: %s", exc)
            return {}

    key = "cdt_meta:" + ",".join(normalized)
    return cached(key, ttl_seconds, _load)


def fetch_cdt_descriptions(
    supabase: Client | None,
    codes: list[str],
    *,
    ttl_seconds: float,
) -> dict[str, str]:
    """Map CDT code → description from analytics.cdt_codes (cached)."""
    meta = fetch_cdt_metadata(supabase, codes, ttl_seconds=ttl_seconds)
    return {code: str(row.get("description") or "") for code, row in meta.items()}


def fetch_required_documentation(
    supabase: Client | None,
    *,
    cdt_codes: list[str],
    payer_name: str | None,
    ttl_seconds: float,
) -> dict[str, list[str]]:
    """
    Map CDT → required supporting documentation strings from payer_rules
    rows with rule_type = documentation_required.
    """
    codes = sorted({c.upper().strip() for c in cdt_codes if c and str(c).strip()})
    if not codes or supabase is None:
        return {}

    payer_key = (payer_name or "").strip().lower() or "*"

    def _load() -> dict[str, list[str]]:
        try:
            result = (
                supabase.table(PAYER_RULES)
                .select("payer_name,rule_type,code,rule_text")
                .eq("rule_type", "documentation_required")
                .in_("code", codes)
                .execute()
            )
            rows = getattr(result, "data", None) or []
        except Exception as exc:
            logger.warning("documentation_required lookup failed: %s", exc)
            return {}

        by_code: dict[str, list[str]] = {c: [] for c in codes}
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_payer = str(row.get("payer_name") or "").strip().lower()
            if (
                payer_key not in ("*", "")
                and row_payer not in ("*", "any", "all", "")
                and payer_key not in row_payer
                and row_payer not in payer_key
            ):
                continue
            code = str(row.get("code") or "").upper().strip()
            text = _clean_doc_text(str(row.get("rule_text") or ""))
            if code in by_code and text and text not in by_code[code]:
                by_code[code].append(text)
        return by_code

    key = f"docs:{payer_key}:{','.join(codes)}"
    return cached(key, ttl_seconds, _load)


def _clean_doc_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) < 8:
        return ""
    # Cap noisy handbook fragments.
    return cleaned[:240]


def default_docs_for_code(cdt_code: str | None) -> list[str]:
    """Conservative fallbacks when payer_rules has no documentation_required row."""
    code = (cdt_code or "").upper().strip()
    if not code:
        return []
    if code in _IMAGING_CODES or code.startswith("D02") or code.startswith("D03"):
        return ["diagnostic-quality radiograph"]
    if code.startswith("D2"):
        return ["pre-op radiograph documenting caries or defect", "clinical narrative"]
    if code.startswith(("D3", "D4", "D6", "D7")):
        return ["pre-op radiograph", "clinical narrative / by report if required"]
    return []


def confidence_threshold(settings: CodingSettings) -> float:
    return float(settings.coding_confidence_review_threshold)
