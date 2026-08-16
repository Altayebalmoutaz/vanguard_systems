"""Deterministic CDT proposals for settled chairside patterns (no LLM).

Keyed on structured findings + tooth/surfaces/age only. Unmatched lines stay
unresolved so the service can send just those leftovers to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.coding.adapter import patient_age
from app.coding.gaps import (
    _any_unnegated,
    findings_blob,
    looks_crown_procedure,
    looks_filling_procedure,
)
from app.coding.guards import planned_crown_material_documented
from app.coding.schemas import CodingSuggestRequest, ProcedureLine

RULE_CONFIDENCE = 0.97

_ANTERIOR_TEETH = frozenset({6, 7, 8, 9, 10, 11, 22, 23, 24, 25, 26, 27})
_CHILD_PROPHY_MAX_AGE = 13

_NULL_PPE_TOKENS = ("ppe", "personal protective")
_NULL_RINSE_TOKENS = ("pre-procedural rinse", "preprocedural rinse", "pre-procedural")
_NULL_LASER_TOKENS = ("laser",)

_IRRIGATION_TOKENS = ("gingival irrigation", "irrigation", "gingival lavage")
_SRP_TOKENS = (
    "scaling and root planing",
    "root planing",
    "non-surgical periodontal",
    "nonsurgical periodontal",
    "non surgical periodontal",
    "srp",
)
_SRP_LOCALIZED_TOKENS = ("1-3 teeth", "1 to 3 teeth", "one to three teeth")
_QUADRANT_TOKENS = (
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

_FMX_TOKENS = ("fmx", "fms", "complete series", "full mouth series")
_BITEWING_FOUR_TOKENS = (
    "four bitewing",
    "4 bitewing",
    "bitewings four",
    "four bite-wing",
    "4 bite-wing",
)
_PERIAPICAL_TOKENS = ("periapical",)
_PANO_TOKENS = ("panoramic", "pano")

_PERIO_EVAL_TOKENS = (
    "periodontal evaluation",
    "periodontal eval",
    "perio eval",
    "periodontal charting",
)
_COMPREHENSIVE_TOKENS = (
    "comprehensive examination",
    "comprehensive exam",
    "comprehensive oral",
    "comprehensive evaluation",
)
_PERIODIC_TOKENS = (
    "periodic oral",
    "periodic exam",
    "periodic evaluation",
    "recall exam",
    "recall evaluation",
)

_OHI_TOKENS = ("oral hygiene instruction", "oral hygiene instructions", "ohi")
_FLUORIDE_VARNISH_TOKENS = ("fluoride varnish", "varnish")
_PROPHY_TOKENS = ("prophylaxis", "prophy")
_CHILD_PROPHY_TOKENS = ("child prophylaxis", "child prophy")
_ADULT_PROPHY_TOKENS = ("adult prophylaxis", "adult prophy")

_PFM_TOKENS = ("pfm", "porcelain-fused", "porcelain fused")
_CERAMIC_TOKENS = (
    "porcelain",
    "ceramic",
    "zirconia",
    "zircon",
    "lithium",
    "emax",
    "e.max",
)
_CAST_TOKENS = ("full cast", "full-cast", "gold")
_EXISTING_MATERIAL_CUES = (
    "existing",
    "current",
    "old",
    "present",
    "failed",
    "defective",
    "failing",
)

_COMPOSITE_CODES_POSTERIOR = {1: "D2391", 2: "D2392", 3: "D2393"}
_COMPOSITE_CODES_ANTERIOR = {1: "D2330", 2: "D2331", 3: "D2332"}
_AMALGAM_CODES = {1: "D2140", 2: "D2150", 3: "D2160"}

_SEALANT_TOKENS = ("sealant", "sealants", "pits and fissures", "pit and fissure")
_ENDO_TOKENS = (
    "root canal",
    "rct",
    "endodontic therapy",
    "irreversible pulpitis",
    "necrosis of pulp",
    "pulpal necrosis",
)
_EXTRACTION_TOKENS = (
    "extraction",
    "extracted",
    "extract",
    "vertical root fracture",
    "retained root",
)
_SURGICAL_EXTRACTION_TOKENS = (
    "surgical",
    "impacted",
    "completely bony",
    "partially bony",
    "flap",
    "sectioned",
)
_MAINTENANCE_TOKENS = ("periodontal maintenance", "perio maintenance")
_SOCKET_GRAFT_TOKENS = (
    "socket preservation",
    "ridge preservation",
    "socket graft",
    "socket preservation bone graft",
)
_PREMOLAR_TEETH = frozenset({4, 5, 12, 13, 20, 21, 28, 29})
_MOLAR_TEETH = frozenset({1, 2, 3, 14, 15, 16, 17, 18, 19, 30, 31, 32})


@dataclass(frozen=True)
class ProposedLine:
    """One deterministic proposal. ``resolved`` means do not send this line to the LLM."""

    line_id: str
    cdt_code: str | None
    confidence: float
    explanation: str
    resolved: bool
    icd10_codes: tuple[str, ...] = ()

    def as_rec(self) -> dict[str, object]:
        return {
            "line_id": self.line_id,
            "cdt_code": self.cdt_code,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "icd10_codes": list(self.icd10_codes),
        }


def propose(request: CodingSuggestRequest) -> list[ProposedLine]:
    """Propose a CDT (or explicit null) per line. Unmatched lines stay unresolved."""
    age = patient_age(request)
    return [_propose_line(proc, age=age) for proc in request.procedures]


def _propose_line(line: ProcedureLine, *, age: int) -> ProposedLine:
    blob = findings_blob(line)

    if _has(line, _NULL_PPE_TOKENS) or _has(line, _NULL_RINSE_TOKENS) or _has(line, _NULL_LASER_TOKENS):
        return _resolved_null(line, "No billable CDT for PPE, pre-procedural rinse, or laser adjunct.")

    if _has(line, _IRRIGATION_TOKENS):
        return _hit(line, "D4921", "Gingival irrigation is D4921.")

    if _has(line, _SRP_TOKENS):
        srp = _srp_code(line, blob)
        if srp:
            units = _quadrant_units(blob)
            explanation = "Scaling and root planing from quadrant or tooth count."
            if units > 1:
                explanation = f"Scaling and root planing ({srp}) × {units} quadrants."
            return _hit(line, srp, explanation)
        return _unresolved(line)

    if _has(line, _MAINTENANCE_TOKENS):
        return _hit(line, "D4910", "Periodontal maintenance.")

    if _has(line, _SEALANT_TOKENS) and line.tooth_numbers:
        units = len(line.tooth_numbers)
        explanation = "Sealant per tooth (D1351)."
        if units > 1:
            teeth = ", ".join(line.tooth_numbers)
            explanation = f"Sealant (D1351) × {units} (teeth {teeth})."
        return _hit(line, "D1351", explanation)

    if _has(line, _ENDO_TOKENS) and line.tooth_numbers:
        endo = _endo_code(line)
        if endo:
            return _hit(line, endo, "Root canal therapy from tooth class.")
        return _unresolved(line)

    if _has(line, _EXTRACTION_TOKENS) and line.tooth_numbers:
        if _has(line, _SURGICAL_EXTRACTION_TOKENS):
            return _unresolved(line)
        return _hit(line, "D7140", "Simple extraction of an erupted tooth.")

    if _has(line, _SOCKET_GRAFT_TOKENS):
        return _hit(line, "D7953", "Socket/ridge preservation bone graft (D7953).")

    if looks_crown_procedure(line):
        if not planned_crown_material_documented(line):
            return _resolved_null(
                line,
                "Planned crown material is not documented; cannot choose a crown code.",
            )
        crown = _crown_code(line)
        if crown:
            return _hit(line, crown, "Crown code from documented planned material.")
        return _unresolved(line)

    if looks_filling_procedure(line) and line.surfaces and line.tooth_numbers:
        filling = _filling_code(line, blob)
        if filling:
            return _hit(line, filling, "Filling code from material, arch, and surface count.")
        return _unresolved(line)

    imaging = _imaging_code(blob)
    if imaging:
        return _hit(line, imaging, "Imaging code from documented study type.")

    if _has(line, _PERIO_EVAL_TOKENS):
        return _hit(line, "D0180", "Periodontal evaluation.")
    if _has(line, _COMPREHENSIVE_TOKENS):
        return _hit(line, "D0150", "Comprehensive oral evaluation.")
    if _has(line, _PERIODIC_TOKENS):
        return _hit(line, "D0120", "Periodic oral evaluation.")

    if _has(line, _OHI_TOKENS):
        return _hit(line, "D1330", "Oral hygiene instructions.")
    if _has(line, _FLUORIDE_VARNISH_TOKENS) and "fluoride" in blob:
        return _hit(line, "D1206", "Topical fluoride varnish.")
    if _has(line, _PROPHY_TOKENS):
        return _hit(line, _prophy_code(line, age), "Prophylaxis.")

    return _unresolved(line)


def _endo_code(line: ProcedureLine) -> str | None:
    classes = {_tooth_class(t) for t in line.tooth_numbers}
    if len(classes) != 1:
        return None
    cls = next(iter(classes))
    return {"anterior": "D3310", "premolar": "D3320", "molar": "D3330"}.get(cls or "")


def _tooth_class(tooth: str) -> str | None:
    digits = "".join(ch for ch in tooth if ch.isdigit())
    if not digits:
        return None
    n = int(digits)
    if n in _ANTERIOR_TEETH:
        return "anterior"
    if n in _PREMOLAR_TEETH:
        return "premolar"
    if n in _MOLAR_TEETH:
        return "molar"
    return None


def _quadrant_units(blob: str) -> int:
    if re.search(r"\b(?:all\s+)?(?:four|4)\s+quadrants?\b", blob) or "all quadrants" in blob:
        return 4
    named = sum(1 for tok in ("upper right", "upper left", "lower right", "lower left") if tok in blob)
    abbrev = len(set(re.findall(r"\b(?:ur|ul|lr|ll)\b", blob)))
    return max(named, abbrev, 1)


def _companion_icd(line: ProcedureLine, cdt_code: str) -> tuple[str, ...]:
    if _has(line, ("irreversible pulpitis",)):
        return ("K04.02",)
    if _has(line, ("vertical root fracture", "cracked tooth")):
        return ("K03.81",)
    if cdt_code in {"D4341", "D4342", "D4910"} or _has(
        line, ("periodontitis", "periodontal disease")
    ):
        return ("K05.30",)
    return ()


def _srp_code(line: ProcedureLine, blob: str) -> str | None:
    if _has(line, _SRP_LOCALIZED_TOKENS) or 0 < len(line.tooth_numbers) <= 3:
        return "D4342"
    if any(tok in blob for tok in _QUADRANT_TOKENS) or len(line.tooth_numbers) >= 4:
        return "D4341"
    if re.search(r"\b(?:ur|ul|lr|ll)\b", blob):
        return "D4341"
    return None


def _crown_code(line: ProcedureLine) -> str | None:
    for finding in line.findings:
        for clause in re.split(r"[.;\n]", finding.lower()):
            if any(cue in clause for cue in _EXISTING_MATERIAL_CUES):
                continue
            if any(tok in clause for tok in _PFM_TOKENS):
                return "D2750"
            if any(tok in clause for tok in _CERAMIC_TOKENS):
                return "D2740"
            if any(tok in clause for tok in _CAST_TOKENS):
                return "D2790"
    return None


def expected_filling_code(line: ProcedureLine) -> str | None:
    """CDT implied by material, arch, and surface count, or None if incomplete."""
    if not line.tooth_numbers or not line.surfaces:
        return None
    return _filling_code(line, findings_blob(line))


def _filling_code(line: ProcedureLine, blob: str) -> str | None:
    n = _surface_count(line.surfaces)
    if n < 1:
        return None
    amalgam = "amalgam" in blob and "composite" not in blob and "resin" not in blob
    anterior = _is_anterior(line.tooth_numbers[0])
    if amalgam:
        return _AMALGAM_CODES.get(n, "D2161")
    if anterior:
        return _COMPOSITE_CODES_ANTERIOR.get(n, "D2335")
    return _COMPOSITE_CODES_POSTERIOR.get(n, "D2394")


def _imaging_code(blob: str) -> str | None:
    if any(tok in blob for tok in _FMX_TOKENS) or (
        "full mouth" in blob and any(tok in blob for tok in ("x-ray", "xray", "radiograph", "series"))
    ):
        return "D0210"
    if any(tok in blob for tok in _BITEWING_FOUR_TOKENS):
        return "D0274"
    if any(tok in blob for tok in _PERIAPICAL_TOKENS):
        return "D0220"
    if "panoramic" in blob or re.search(r"\bpano\b", blob):
        return "D0330"
    return None


def _prophy_code(line: ProcedureLine, age: int) -> str:
    if _has(line, _CHILD_PROPHY_TOKENS):
        return "D1120"
    if _has(line, _ADULT_PROPHY_TOKENS):
        return "D1110"
    if 0 < age <= _CHILD_PROPHY_MAX_AGE:
        return "D1120"
    return "D1110"


def _surface_count(surfaces: list[str]) -> int:
    seen: set[str] = set()
    for raw in surfaces:
        token = raw.upper().replace(" ", "")
        if not token:
            continue
        if all(ch in "MODBLFI" for ch in token):
            seen.update(token)
        else:
            seen.add(token)
    return len(seen)


def _is_anterior(tooth: str) -> bool:
    digits = "".join(ch for ch in tooth if ch.isdigit())
    if not digits:
        return False
    return int(digits) in _ANTERIOR_TEETH


def _has(line: ProcedureLine, tokens: tuple[str, ...]) -> bool:
    return _any_unnegated(line.findings, tokens)


def _hit(line: ProcedureLine, cdt_code: str, explanation: str) -> ProposedLine:
    return ProposedLine(
        line_id=line.line_id,
        cdt_code=cdt_code,
        confidence=RULE_CONFIDENCE,
        explanation=explanation,
        resolved=True,
        icd10_codes=_companion_icd(line, cdt_code),
    )


def _resolved_null(line: ProcedureLine, explanation: str) -> ProposedLine:
    return ProposedLine(
        line_id=line.line_id,
        cdt_code=None,
        confidence=0.0,
        explanation=explanation,
        resolved=True,
    )


def _unresolved(line: ProcedureLine) -> ProposedLine:
    return ProposedLine(
        line_id=line.line_id,
        cdt_code=None,
        confidence=0.0,
        explanation="",
        resolved=False,
    )
