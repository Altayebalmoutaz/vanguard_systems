"""Resolve OpenDental procedure codes (ADA D-codes or trial T-codes) to CDTs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

# Seeded from local OD trial ``procedurecode`` catalog (T-codes → ADA CDT).
OD_TRIAL_CODE_ALIASES: dict[str, str] = {
    "T1254": "D1206",  # Fluoride
    "T1356": "D0120",  # Exam (periodic; Phase 1 lock)
    "T1546": "D0220",  # Intraoral Periapical Film
    "T1632": "D0272",  # 2 Bitewings
    "T1665": "D0330",  # Panoramic
    "T1698": "D0274",  # 4 Bitewings
    "T2345": "D2950",  # Build Up
    "T3512": "D2330",  # Composite-1 Surf, Anterior
    "T3522": "D2331",  # Composite-2 Surf, Anterior
    "T3532": "D2332",  # Composite-3 Surf, Anterior
    "T3541": "D1110",  # Prophy, Adult
    "T3542": "D2335",  # Composite-4 Surf, Anterior or More Surfaces
    "T3546": "D1351",  # Sealant
    "T4528": "D2140",  # Amalgam-1 Surf
    "T4538": "D2150",  # Amalgam-2 Surf
    "T4548": "D2160",  # Amalgam-3 Surf
    "T4558": "D2161",  # Amalgam-4 Surf or More Surfaces
    "T5823": "D2391",  # Composite-1 Surf, Posterior
    "T5833": "D2392",  # Composite-2 Surf, Posterior
    "T5843": "D2393",  # Composite-3 Surf, Posterior
    "T5853": "D2394",  # Composite-4 Surf, Posterior or More Surfaces
    "T6245": "D6245",  # Bridge Pontic, PFM
    "T6255": "D6750",  # Bridge Retainer, PFM
    "T6357": "D7140",  # Extraction (simple)
    "T6452": "D2952",  # Post & Core
    "T6462": "D3346",  # Root Canal, Retreat Anterior
    "T6472": "D3347",  # Root Canal, Retreat PreMolar
    "T6482": "D3348",  # Root Canal, Retreat Molar
    "T6531": "D2750",  # PFM Crown
    "T7956": "D3310",  # Root Canal, Anterior
    "T7966": "D3320",  # Root Canal, Bicuspid
    "T7976": "D3330",  # Root Canal, Molar
    "T9826": "D5110",  # Maxillary Denture
    "T9836": "D5120",  # Mandibular Denture
}

_ADA_CDT_RE = re.compile(r"^D\d{4}$", re.IGNORECASE)

RowSource = Literal["ada", "code_alias"]
OverallSource = Literal["appointment", "clinic_default"]


@dataclass(frozen=True)
class ResolvedCdt:
    cdt_code: str
    source: RowSource


@dataclass(frozen=True)
class ProcedureResolveRow:
    od_proc_code: str
    od_descript: str | None
    resolved_cdt: str | None
    cdt_source: RowSource | None


@dataclass(frozen=True)
class ResolveResult:
    cdt_codes: list[str]
    cdt_source: OverallSource
    procedures: list[ProcedureResolveRow] = field(default_factory=list)
    unmapped: list[ProcedureResolveRow] = field(default_factory=list)

    def to_input_json(self, *, apt_nums: list[int] | None = None) -> dict[str, Any]:
        return {
            "apt_nums": list(apt_nums or []),
            "cdt_source": self.cdt_source,
            "od_procedures": [
                {
                    "od_proc_code": p.od_proc_code,
                    "od_descript": p.od_descript,
                    "resolved_cdt": p.resolved_cdt,
                    "cdt_source": p.cdt_source,
                }
                for p in self.procedures
            ],
            "unmapped_od_codes": [
                {
                    "od_proc_code": p.od_proc_code,
                    "od_descript": p.od_descript,
                }
                for p in self.unmapped
            ],
        }


def resolve_od_proc_code(
    proc_code: str | None,
    descript: str | None = None,
) -> ResolvedCdt | None:
    """Map a single OD procCode to an ADA CDT, or None if unmapped."""
    raw = (proc_code or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    if _ADA_CDT_RE.match(upper):
        return ResolvedCdt(cdt_code=upper, source="ada")
    aliased = OD_TRIAL_CODE_ALIASES.get(upper)
    if aliased:
        return ResolvedCdt(cdt_code=aliased.upper(), source="code_alias")
    return None


def _normalize_clinic_defaults(clinic_defaults: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in clinic_defaults or []:
        code = str(raw or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def resolve_appointment_procedures(
    rows: list[Mapping[str, Any]] | list[Any],
    *,
    clinic_defaults: list[str] | None = None,
) -> ResolveResult:
    """Resolve procedurelog-like rows; fall back to clinic defaults when empty."""
    procedures: list[ProcedureResolveRow] = []
    unmapped: list[ProcedureResolveRow] = []
    resolved: list[str] = []
    seen: set[str] = set()

    for row in rows:
        if hasattr(row, "model_dump"):
            data = row.model_dump()
        elif isinstance(row, Mapping):
            data = dict(row)
        else:
            data = {
                "procCode": getattr(row, "procCode", None),
                "descript": getattr(row, "descript", None),
            }
        proc_code = str(data.get("procCode") or data.get("ProcCode") or "").strip()
        descript = data.get("descript") or data.get("Descript")
        descript_s = str(descript).strip() if descript is not None else None
        hit = resolve_od_proc_code(proc_code, descript_s)
        if hit is None:
            if proc_code:
                unmapped.append(
                    ProcedureResolveRow(
                        od_proc_code=proc_code,
                        od_descript=descript_s,
                        resolved_cdt=None,
                        cdt_source=None,
                    )
                )
            continue
        procedures.append(
            ProcedureResolveRow(
                od_proc_code=proc_code,
                od_descript=descript_s,
                resolved_cdt=hit.cdt_code,
                cdt_source=hit.source,
            )
        )
        if hit.cdt_code not in seen:
            seen.add(hit.cdt_code)
            resolved.append(hit.cdt_code)

    if resolved:
        return ResolveResult(
            cdt_codes=resolved,
            cdt_source="appointment",
            procedures=procedures,
            unmapped=unmapped,
        )

    defaults = _normalize_clinic_defaults(clinic_defaults)
    return ResolveResult(
        cdt_codes=defaults,
        cdt_source="clinic_default",
        procedures=procedures,
        unmapped=unmapped,
    )
