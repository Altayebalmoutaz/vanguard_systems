"""Deterministic gap checks and payer documentation lookups (no LLM)."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.coding.cache import cached
from app.coding.cdt_requirements import code_range_requirements
from app.coding.config import CodingSettings
from app.coding.schemas import (
    CodingSuggestRequest,
    MissingInfoCode,
    MissingInfoItem,
    ProcedureLine,
    resolved_quadrant,
)
from app.integrations.db_tables import CDT_CODES, PAYER_RULES
from supabase import Client

logger = logging.getLogger(__name__)

# Gaps that mean we could not produce a usable suggestion at all. Chairside
# coding is suggestion-for-review, not claim completeness: missing teeth,
# surfaces, findings, material, or per-line quadrant are advisory (surfaced to
# the dentist, do NOT force needs_info). Radiograph is advisory because the
# enforceable requirement is payer-specific (payer_rules.documentation_required).
BLOCKING_MISSING_CODES = frozenset(
    {
        MissingInfoCode.PROCEDURE_EMPTY,
        MissingInfoCode.CDT_UNCERTAIN,
    }
)


def is_blocking(item: MissingInfoItem) -> bool:
    return item.code in BLOCKING_MISSING_CODES


def has_blocking(items: list[MissingInfoItem]) -> bool:
    return any(is_blocking(i) for i in items)


_PER_QUADRANT_CODES = frozenset({"D4341", "D4342", "D4921"})
_SRP_CODES = frozenset({"D4341", "D4342"})
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
# Whole-token aliases from scribe payloads (normalized: lowercase, non-alnum → _).
_RADIOGRAPH_ATTACHMENT_ALIASES = frozenset(
    {
        "full_mouth_series",
        "full_mouth_radiograph",
        "fullmouth_series",
        "complete_series",
        "intraoral_complete_series",
        "fmx",
        "fms",
        "bitewing",
        "bitewings",
        "bitewing_radiograph",
        "periapical",
        "periapical_radiograph",
        "pano",
        "panoramic",
        "panoramic_radiograph",
        "cbct",
        "xray",
        "x_ray",
        "radiograph",
    }
)
_RADIOGRAPH_ATTACHMENT_HINTS = (
    "radiograph",
    "xray",
    "x-ray",
    "x_ray",
    "bitewing",
    "pano",
    "panoramic",
    "periapical",
    "fmx",
    "fms",
    "full_mouth",
    "fullmouth",
    "cbct",
    "image",
)


# Soft-tissue followers that mean buccal/lingual/mesial/distal is anatomy, not a
# restorative surface (e.g. "buccal mucosa", "lingual gingiva").
_SOFT_TISSUE_FOLLOWERS = (
    "mucosa",
    "mucosal",
    "gingiva",
    "gingival",
    "papilla",
    "vestibule",
    "tissue",
    "tissues",
    "frenum",
    "frena",
)
_ANATOMIC_SURFACE_WORDS = frozenset({"buccal", "lingual", "mesial", "distal", "facial", "labial"})

# Exam / imaging / hygiene lines are not restorative even if they mention anatomy.
_EXAM_OR_DIAGNOSTIC_TOKENS = (
    "examination",
    "exam",
    "evaluation",
    "periodic oral",
    "comprehensive oral",
    "limited oral",
    "periodontal evaluation",
    "periodontal chart",
    "periodontal charting",
    "oral hygiene",
    "prophylaxis",
    "prophy",
    "pre-procedural",
    "ppe",
)
_IMAGING_LINE_TOKENS = (
    "x-ray",
    "xray",
    "radiograph",
    "full mouth",
    "bitewing",
    "periapical",
    "panoramic",
    "cbct",
    "fmx",
)
_CROWN_PROCEDURE_TOKENS = ("crown", "onlay", "inlay", "veneer")
_RECEMENT_TOKENS = ("recement", "re-cement", "re cement")
_TEMPORARY_CROWN_TOKENS = ("temporary crown", "provisional crown", "temp crown")
_IMPLANT_TOKENS = ("implant",)
_FILLING_PROCEDURE_TOKENS = (
    "composite",
    "amalgam",
    "filling",
    "caries",
    "decay",
    "cavity",
    "occlusal",
    "interproximal",
)

# Findings that imply a tooth-specific procedure (restorative / crown / endo etc.).
# Buccal/lingual/mesial/distal are handled separately — they are often anatomy.
_RESTORATIVE_TOKENS = (
    "caries",
    "decay",
    "cavity",
    "composite",
    "amalgam",
    "filling",
    "restoration",
    "crown",
    "onlay",
    "inlay",
    "occlusal",
    "interproximal",
)
# Findings that specifically imply a *surface* is codeable (excludes crown/caries
# alone, which need a tooth but not necessarily a surface).
_SURFACE_TOKENS = (
    "composite",
    "amalgam",
    "filling",
    "occlusal",
    "interproximal",
    "mesial",
    "distal",
    "buccal",
    "lingual",
    "surface",
)
# Negation cues that cancel a nearby clinical token (e.g. "no decay noted").
_NEGATION_CUES = frozenset(
    {
        "no",
        "not",
        "non",
        "without",
        "denies",
        "denied",
        "negative",
        "neg",
        "r/o",
        "ro",
        "rule",
        "ruled",
        "resolved",
        "absent",
        "free",
    }
)


_QUADRANT_DOC_TOKENS = (
    "quadrant",
    "upper right",
    "upper left",
    "lower right",
    "lower left",
    "maxillary right",
    "maxillary left",
    "mandibular right",
    "mandibular left",
)


def findings_blob(line: ProcedureLine) -> str:
    return " ".join(line.findings).lower()


def _quadrant_documented(line: ProcedureLine) -> bool:
    blob = findings_blob(line)
    if any(tok in blob for tok in _QUADRANT_DOC_TOKENS):
        return True
    return bool(re.search(r"\b(?:ur|ul|lr|ll)\b", blob))


def _token_present_unnegated(blob: str, token: str) -> bool:
    """True if ``token`` appears on a word boundary and is not negated nearby."""
    for m in re.finditer(r"\b" + re.escape(token) + r"\b", blob):
        if _anatomic_not_restorative(blob, m):
            continue
        # Only look back within the current clause (stop at punctuation).
        clause_prefix = re.split(r"[.,;:]", blob[: m.start()])[-1]
        prev_words = re.findall(r"[a-z/]+", clause_prefix)[-3:]
        if any(cue in _NEGATION_CUES for cue in prev_words):
            continue
        return True
    return False


def _anatomic_not_restorative(blob: str, match: re.Match[str]) -> bool:
    """Skip buccal/lingual/etc. when they name soft tissue or furcation sites."""
    token = match.group(0).lower()
    if token not in _ANATOMIC_SURFACE_WORDS:
        return False
    nxt = blob[match.end() :].lstrip()
    if any(nxt.startswith(w) for w in _SOFT_TISSUE_FOLLOWERS):
        return True
    prefix = blob[max(0, match.start() - 48) : match.start()].lower()
    return "furcation" in prefix or "recession" in prefix


def _any_unnegated(findings: list[str], tokens: tuple[str, ...]) -> bool:
    blob = " ".join(findings).lower()
    return any(_token_present_unnegated(blob, tok) for tok in tokens)


def looks_exam_or_diagnostic(line: ProcedureLine) -> bool:
    """True for exam/eval/imaging/hygiene lines that are not the billed restoration."""
    blob = findings_blob(line)
    diagnostic = any(tok in blob for tok in _EXAM_OR_DIAGNOSTIC_TOKENS) or any(
        tok in blob for tok in _IMAGING_LINE_TOKENS
    )
    if not diagnostic:
        return False
    return not looks_crown_procedure(line) and not looks_filling_procedure(line)


def looks_crown_procedure(line: ProcedureLine) -> bool:
    return _any_unnegated(line.findings, _CROWN_PROCEDURE_TOKENS)


def looks_recement_procedure(line: ProcedureLine) -> bool:
    return _any_unnegated(line.findings, _RECEMENT_TOKENS)


def looks_temporary_crown(line: ProcedureLine) -> bool:
    return looks_crown_procedure(line) and _any_unnegated(
        line.findings, _TEMPORARY_CROWN_TOKENS
    )


def looks_implant_procedure(line: ProcedureLine) -> bool:
    return _any_unnegated(line.findings, _IMPLANT_TOKENS)


def looks_definitive_tooth_crown(line: ProcedureLine) -> bool:
    """New/replacement tooth crown — not recement, temp, or implant restoration."""
    return (
        looks_crown_procedure(line)
        and not looks_recement_procedure(line)
        and not looks_temporary_crown(line)
        and not looks_implant_procedure(line)
    )


def looks_filling_procedure(line: ProcedureLine) -> bool:
    return _any_unnegated(line.findings, _FILLING_PROCEDURE_TOKENS)


def _looks_restorative_from_findings(line: ProcedureLine) -> bool:
    """Negation- and word-boundary-aware restorative detection."""
    return _any_unnegated(line.findings, _RESTORATIVE_TOKENS)


def _surface_indicated_from_findings(line: ProcedureLine) -> bool:
    return _any_unnegated(line.findings, _SURFACE_TOKENS)


def pre_check_line(line: ProcedureLine) -> list[MissingInfoItem]:
    """Advisory gaps known before coding (tooth/surface/findings).

    These notes help the dentist confirm the suggestion; they do not withhold a
    code. A tooth/surface hint is only emitted for tooth-specific restorative
    findings (crowns, exams, and anatomic "buccal mucosa" / furcation notes do
    not demand a surface).
    """
    missing: list[MissingInfoItem] = []
    empty = (
        not line.findings
        and not line.tooth_numbers
        and not line.surfaces
        and line.quadrant is None
        and line.arch is None
    )
    if empty:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.FINDING_MISSING,
                message=f"Line {line.line_id}: provide findings and/or tooth/surface detail",
            )
        )
        return missing

    if looks_exam_or_diagnostic(line):
        return missing

    if looks_crown_procedure(line):
        if not line.tooth_numbers:
            missing.append(
                MissingInfoItem(
                    code=MissingInfoCode.TOOTH_MISSING,
                    message=f"Line {line.line_id}: tooth number required for restorative finding",
                )
            )
        return missing

    restorative = _looks_restorative_from_findings(line) or looks_filling_procedure(line)
    if restorative and not line.tooth_numbers:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.TOOTH_MISSING,
                message=f"Line {line.line_id}: tooth number required for restorative finding",
            )
        )
    if restorative and _surface_indicated_from_findings(line) and not line.surfaces:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.SURFACE_MISSING,
                message=f"Line {line.line_id}: surface(s) required for this restorative finding",
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
    if meta:
        # DB reference row exists -> requires_* flags are authoritative (both True
        # and False are respected; this is what stops crowns being surface-gated).
        needs_tooth = bool(meta.get("requires_tooth"))
        needs_surface = bool(meta.get("requires_surfaces"))
        needs_radio = bool(meta.get("requires_radiograph"))
    else:
        # No reference row -> deterministic code-range fallback (shared with the
        # reference backfill), not the blunt code-prefix heuristics.
        req = code_range_requirements(code)
        needs_tooth = req.requires_tooth
        needs_surface = req.requires_surfaces
        needs_radio = req.requires_radiograph or code in _IMAGING_CODES

    if code in {"D4341", "D4342"} and _quadrant_documented(line):
        needs_tooth = False

    if code in _SRP_CODES and resolved_quadrant(line) is not None and not line.tooth_numbers:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.OTHER,
                message=(
                    f"Line {line.line_id}: quadrant-only SRP suggested as {code} "
                    "(4+ teeth). Confirm D4342 if only 1–3 teeth will be treated."
                ),
            )
        )
    elif needs_tooth and not line.tooth_numbers:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.TOOTH_MISSING,
                message=f"Line {line.line_id}: tooth number not spoken; confirm before writeback for {code}",
            )
        )
    if needs_surface and not line.surfaces:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.SURFACE_MISSING,
                message=f"Line {line.line_id}: surface(s) not spoken; confirm before writeback for {code}",
            )
        )
    if needs_radio and not _has_radiograph_attachment(attachments_present):
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.RADIOGRAPH_MISSING,
                message=(f"Line {line.line_id}: radiographic documentation expected for {code}"),
            )
        )
    if code in _PER_QUADRANT_CODES and resolved_quadrant(line) is None:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.OTHER,
                message=(
                    f"Line {line.line_id}: {code} is billed per quadrant; confirm "
                    "UR/UL/LR/LL (or one writeback line per quadrant) before posting."
                ),
            )
        )
    if confidence < threshold:
        missing.append(
            MissingInfoItem(
                code=MissingInfoCode.OTHER,
                message=(
                    f"Line {line.line_id}: confidence {confidence:.2f} below "
                    f"threshold {threshold:.2f}; dentist should confirm the suggestion"
                ),
            )
        )
    return missing


def _normalize_attachment(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (token or "").lower()).strip("_")


def _has_radiograph_attachment(attachments: list[str]) -> bool:
    for raw in attachments:
        text = (raw or "").strip().lower()
        if not text:
            continue
        norm = _normalize_attachment(text)
        if norm in _RADIOGRAPH_ATTACHMENT_ALIASES:
            return True
        if any(hint in text or hint in norm for hint in _RADIOGRAPH_ATTACHMENT_HINTS):
            return True
    return False


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
                .select("code,description,requires_tooth,requires_surfaces,requires_radiograph")
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
