"""Deterministic clinical guards applied after the LLM, before gap checks.

These catch high-frequency CDT mistakes the model still makes on scribe
payloads: undocumented crown material (suggest D2740 + confirm), D4346 used
as a laser/SRP adjunct, D4999 for gingival irrigation, same-day D0150 + D0180,
and D4341 vs D4342 tooth-count remaps.
"""

from __future__ import annotations

import re
from typing import Any

from app.coding.gaps import findings_blob, looks_crown_procedure
from app.coding.schemas import CodingSuggestRequest, ProcedureLine, resolved_quadrant

_CROWN_FAMILY_PREFIX = "D27"
_DEFAULT_CROWN_CODE = "D2740"
_CROWN_MATERIAL_CONFIRM_CONFIDENCE = 0.7
_SRP_CODES = frozenset({"D4341", "D4342"})
_EVAL_COMPREHENSIVE = "D0150"
_EVAL_PERIODONTAL = "D0180"
_IRRIGATION_CODES_TO_REWRITE = frozenset({"", "D4999", "D4346", "D9630"})

_PLANNED_MATERIAL_TOKENS = (
    "porcelain",
    "ceramic",
    "zirconia",
    "zircon",
    "pfm",
    "porcelain-fused",
    "porcelain fused",
    "high noble",
    "noble metal",
    "predominantly base",
    "base metal",
    "full cast",
    "gold",
    "stainless",
    "resin",
    "lithium",
    "emax",
    "e.max",
)
_EXISTING_MATERIAL_CUES = (
    "existing",
    "current",
    "old",
    "present",
    "failed",
    "defective",
    "failing",
)
_PERIODONTITIS_TOKENS = (
    "periodontitis",
    "periodontal disease",
    "periodontal classification",
    "bone loss",
    "attachment loss",
    "clinical attachment",
    "furcation",
    "root planing",
    "scaling and root",
    "non-surgical periodontal",
    "nonsurgical periodontal",
    "srp",
)
_LASER_TOKENS = ("laser",)
_IRRIGATION_TOKENS = ("irrigation", "gingival lavage")
_FILLING_FAMILY = frozenset(
    {
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
    }
)


def apply_clinical_guards(
    request: CodingSuggestRequest,
    recs: list[dict[str, Any]],
) -> list[str]:
    """Mutate ``recs`` in place. Return warning strings for the response."""
    warnings: list[str] = []
    by_id = {p.line_id: p for p in request.procedures}

    encounter_has_srp = False
    encounter_has_periodontitis = False
    for rec in recs:
        code = _code_of(rec)
        if code in _SRP_CODES:
            encounter_has_srp = True
        line = by_id.get(str(rec.get("line_id") or ""))
        if line and _blob_has_unnegated(findings_blob(line), _PERIODONTITIS_TOKENS):
            encounter_has_periodontitis = True

    _guard_eval_exclusivity(recs, by_id, warnings)

    for rec in recs:
        line_id = str(rec.get("line_id") or "")
        line = by_id.get(line_id)
        if line is None:
            continue
        code = _code_of(rec)
        blob = findings_blob(line)

        if looks_crown_procedure(line) and not planned_crown_material_documented(line):
            if not code:
                rec["cdt_code"] = _DEFAULT_CROWN_CODE
                code = _DEFAULT_CROWN_CODE
            if code.startswith(_CROWN_FAMILY_PREFIX):
                _cap_confidence(rec, _CROWN_MATERIAL_CONFIRM_CONFIDENCE)
                rec["explanation"] = _append_explanation(
                    rec.get("explanation"),
                    "Confirm crown material (porcelain/ceramic vs PFM vs full cast) "
                    "before writeback.",
                )
                warnings.append(
                    f"Guard kept line {line_id} as {code}: crown material not spoken; "
                    "confirm before writeback"
                )

        if code == "D4346" and (
            encounter_has_srp or encounter_has_periodontitis or _blob_has(blob, _LASER_TOKENS)
        ):
            _void(
                rec,
                "D4346 is scaling in the presence of gingivitis, not a laser or "
                "SRP adjunct, and is not billed with D4341/D4342.",
            )
            warnings.append(f"Guard cleared line {line_id}: D4346 contraindicated")
            continue

        if _blob_has(blob, _IRRIGATION_TOKENS) and code in _IRRIGATION_CODES_TO_REWRITE:
            rec["cdt_code"] = "D4921"
            try:
                rec["confidence"] = min(float(rec.get("confidence") or 0.75), 0.8)
            except (TypeError, ValueError):
                rec["confidence"] = 0.75
            rec["explanation"] = (
                "Gingival irrigation is D4921 (per quadrant), not an unspecified "
                "periodontal procedure."
            )
            warnings.append(f"Guard mapped line {line_id} to D4921")
            code = "D4921"

        n_teeth = len(line.tooth_numbers)
        if code in _SRP_CODES and n_teeth == 0 and resolved_quadrant(line) is not None:
            if code != "D4341":
                rec["cdt_code"] = "D4341"
                warnings.append(f"Guard mapped line {line_id} {code} -> D4341")
            rec["explanation"] = (
                "Quadrant SRP without a tooth list is suggested as D4341 "
                "(4+ teeth). Confirm D4342 if only 1–3 teeth will be treated."
            )
        elif code == "D4342" and n_teeth >= 4:
            rec["cdt_code"] = "D4341"
            rec["explanation"] = "Four or more teeth in this quadrant map to D4341, not D4342."
            warnings.append(f"Guard mapped line {line_id} D4342 -> D4341")
        elif code == "D4341" and 1 <= n_teeth <= 3:
            rec["cdt_code"] = "D4342"
            rec["explanation"] = "One to three teeth in this quadrant map to D4342, not D4341."
            warnings.append(f"Guard mapped line {line_id} D4341 -> D4342")

        if code in _FILLING_FAMILY and line.tooth_numbers and line.surfaces:
            from app.coding.propose import expected_filling_code

            expected = expected_filling_code(line)
            if expected and code != expected:
                _void(
                    rec,
                    f"Filling {code} does not match tooth/surface count (expected {expected}).",
                )
                warnings.append(
                    f"Guard cleared line {line_id}: filling {code} != {expected}"
                )

    return warnings


def planned_crown_material_documented(line: ProcedureLine) -> bool:
    """True when a crown material is stated for the planned/billed restoration.

    Material that only describes an existing restoration (e.g. "existing full
    gold crown") does not count — replacement material still has to be named.
    """
    for finding in line.findings:
        for clause in re.split(r"[.;\n]", finding.lower()):
            if not any(tok in clause for tok in _PLANNED_MATERIAL_TOKENS):
                continue
            if any(cue in clause for cue in _EXISTING_MATERIAL_CUES):
                continue
            return True
    return False


def _guard_eval_exclusivity(
    recs: list[dict[str, Any]],
    by_id: dict[str, ProcedureLine],
    warnings: list[str],
) -> None:
    comprehensive = [r for r in recs if _code_of(r) == _EVAL_COMPREHENSIVE]
    periodontal = [r for r in recs if _code_of(r) == _EVAL_PERIODONTAL]
    if not comprehensive or not periodontal:
        return

    keep_perio = False
    for rec in periodontal:
        line = by_id.get(str(rec.get("line_id") or ""))
        if line is None:
            continue
        blob = findings_blob(line)
        if "periodontal" in blob or "perio eval" in blob:
            keep_perio = True
            break

    drop, keep_code = (
        (comprehensive, _EVAL_PERIODONTAL) if keep_perio else (periodontal, _EVAL_COMPREHENSIVE)
    )
    for rec in drop:
        line_id = str(rec.get("line_id") or "")
        dropped = _code_of(rec)
        _void(
            rec,
            f"{dropped} is mutually exclusive with {keep_code} on the same date of service.",
        )
        warnings.append(f"Guard cleared line {line_id}: {dropped} exclusive with {keep_code}")


def _code_of(rec: dict[str, Any]) -> str:
    cdt = rec.get("cdt_code")
    return str(cdt).upper().strip() if cdt not in (None, "") else ""


def void_recommendation(rec: dict[str, Any], explanation: str) -> None:
    """Clear a proposed CDT so post_check emits CDT_UNCERTAIN."""
    _void(rec, explanation)


def _void(rec: dict[str, Any], explanation: str) -> None:
    rec["cdt_code"] = None
    rec["confidence"] = 0.0
    rec["explanation"] = explanation


def _cap_confidence(rec: dict[str, Any], ceiling: float) -> None:
    try:
        rec["confidence"] = min(float(rec.get("confidence") or ceiling), ceiling)
    except (TypeError, ValueError):
        rec["confidence"] = ceiling


def _append_explanation(existing: object, extra: str) -> str:
    base = str(existing or "").strip()
    if not base:
        return extra
    if extra.lower() in base.lower():
        return base
    return f"{base.rstrip('.')} {extra}"


def _blob_has(blob: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in blob for tok in tokens)


def _blob_has_unnegated(blob: str, tokens: tuple[str, ...]) -> bool:
    """True when a token appears and is not preceded by a negation cue."""
    for tok in tokens:
        start = 0
        while True:
            idx = blob.find(tok, start)
            if idx < 0:
                break
            prefix = blob[max(0, idx - 48) : idx]
            if not re.search(r"\b(?:no|not|without|denies|denied)\b", prefix):
                return True
            start = idx + len(tok)
    return False
