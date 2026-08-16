"""Deterministic CDT documentation-requirement derivation.

Two sources, in priority order (see the accuracy plan, workstream 4a):

1. OpenDental ``procedurecode.TreatArea`` — authoritative for tooth/surface.
2. CDT code-range rules — fallback for any code missing from the OD catalog.

``requires_radiograph`` is intentionally only a conservative *hint* here; the
enforceable, payer-specific requirement lives in ``payer_rules`` with
``rule_type = documentation_required``.
"""

from __future__ import annotations

from dataclasses import dataclass

# OpenDental TreatmentArea enum: name -> (requires_tooth, requires_surfaces).
# Ordinals (some API versions emit ints): Surf=0, Tooth=1, Mouth=2, Quad=3,
# Sextant=4, Arch=5, ToothRange=6.
_TREAT_AREA_BY_NAME: dict[str, tuple[bool, bool]] = {
    "surf": (True, True),
    "tooth": (True, False),
    "toothrange": (True, False),
    "mouth": (False, False),
    "quad": (False, False),
    "sextant": (False, False),
    "arch": (False, False),
}
_TREAT_AREA_BY_ORDINAL: dict[int, str] = {
    0: "surf",
    1: "tooth",
    2: "mouth",
    3: "quad",
    4: "sextant",
    5: "arch",
    6: "toothrange",
}


@dataclass(frozen=True)
class CdtRequirements:
    requires_tooth: bool
    requires_surfaces: bool
    requires_radiograph: bool
    source: str  # "opendental_treatarea" | "code_range" | "unknown"


def treat_area_to_flags(treat_area: str | int | None) -> tuple[bool, bool] | None:
    """Map an OD TreatArea (name or ordinal) to (requires_tooth, requires_surfaces).

    Returns ``None`` when the value is missing/unrecognized so the caller can
    fall back to code-range rules.
    """
    if treat_area is None:
        return None
    if isinstance(treat_area, bool):  # guard: bool is a subclass of int
        return None
    if isinstance(treat_area, int):
        name = _TREAT_AREA_BY_ORDINAL.get(treat_area)
        return _TREAT_AREA_BY_NAME.get(name) if name else None
    key = str(treat_area).strip().lower()
    if key.isdigit():
        name = _TREAT_AREA_BY_ORDINAL.get(int(key))
        return _TREAT_AREA_BY_NAME.get(name) if name else None
    return _TREAT_AREA_BY_NAME.get(key)


def _cdt_number(code: str) -> int | None:
    """Return the 4-digit numeric portion of a Dxxxx code, or None."""
    c = (code or "").upper().strip()
    if len(c) < 5 or not c.startswith("D"):
        return None
    digits = c[1:5]
    return int(digits) if digits.isdigit() else None


def _is_filling(n: int) -> bool:
    # Amalgam D2140-D2161, resin/composite D2330-D2394, glass ionomer D2391-2394.
    return 2140 <= n <= 2161 or 2330 <= n <= 2394


def _is_crown_or_indirect(n: int) -> bool:
    # Inlays/onlays/crowns D2510-D2799, build-ups/posts/other indirect D2900-D2999.
    return 2510 <= n <= 2799 or 2900 <= n <= 2999


def _radiograph_hint(n: int) -> bool:
    # Conservative "a pre-op / diagnostic film is typically expected" hint only.
    if 210 <= n <= 367:  # intraoral/extraoral imaging (D0210-D0367)
        return True
    if _is_crown_or_indirect(n):  # crowns/onlays document the defect
        return True
    if 3000 <= n <= 3999:  # endodontics
        return True
    if 4210 <= n <= 4249 or n in (4341, 4342):  # perio surgery + SRP
        return True
    return 7000 <= n <= 7999  # oral surgery / extractions


def code_range_requirements(cdt_code: str) -> CdtRequirements:
    """Deterministic fallback requirements from the CDT code number alone."""
    n = _cdt_number(cdt_code)
    if n is None:
        return CdtRequirements(False, False, False, "unknown")

    requires_tooth = False
    requires_surfaces = False

    if 2000 <= n <= 2999:  # restorative
        requires_tooth = True
        requires_surfaces = _is_filling(n)  # crowns/indirect -> tooth only
    elif (
        3000 <= n <= 3999
        or 4210 <= n <= 4249
        or 6000 <= n <= 6199
        or 6200 <= n <= 6999
        or 7000 <= n <= 7999
        or n in (1351, 1352, 1353, 1354)
    ):  # endodontics
        requires_tooth = True

    return CdtRequirements(
        requires_tooth=requires_tooth,
        requires_surfaces=requires_surfaces,
        requires_radiograph=_radiograph_hint(n),
        source="code_range",
    )


def resolve_requirements(
    cdt_code: str,
    *,
    treat_area: str | int | None = None,
) -> CdtRequirements:
    """Authoritative OD TreatArea when available, else code-range fallback.

    ``requires_radiograph`` always comes from the conservative code-range hint,
    since OD TreatArea does not encode documentation needs.
    """
    fallback = code_range_requirements(cdt_code)
    flags = treat_area_to_flags(treat_area)
    if flags is None:
        return fallback
    requires_tooth, requires_surfaces = flags
    return CdtRequirements(
        requires_tooth=requires_tooth,
        requires_surfaces=requires_surfaces,
        requires_radiograph=fallback.requires_radiograph,
        source="opendental_treatarea",
    )
