"""Convert scribe structured JSON into engine prompts and line-level results."""

from __future__ import annotations

from typing import Any

from app.coding.schemas import CodingSuggestRequest, ProcedureLine
from app.security.phi import scrub_for_llm


def insurance_label(request: CodingSuggestRequest) -> str:
    if request.payer.name and request.payer.name.strip():
        return request.payer.name.strip()
    if request.payer.id and request.payer.id.strip():
        return request.payer.id.strip()
    return "Unknown"


def patient_age(request: CodingSuggestRequest) -> int:
    return int(request.patient.age) if request.patient.age is not None else 0


def build_clinical_note(request: CodingSuggestRequest) -> str:
    """Flatten structured procedures into a note the legacy engine can consume."""
    parts: list[str] = []
    if request.supporting_note and request.supporting_note.strip():
        parts.append(request.supporting_note.strip())
    for proc in request.procedures:
        parts.append(_format_procedure_line(proc))
    if request.attachments_present:
        parts.append("Attachments present: " + ", ".join(request.attachments_present))
    return "\n".join(parts).strip() or "No clinical detail provided."


def _format_procedure_line(proc: ProcedureLine) -> str:
    teeth = ", ".join(proc.tooth_numbers) if proc.tooth_numbers else "unspecified"
    surfaces = ", ".join(proc.surfaces) if proc.surfaces else "unspecified"
    findings = "; ".join(proc.findings) if proc.findings else "none listed"
    extra = ""
    if proc.quadrant is not None:
        extra += f"; quadrant={proc.quadrant.value}"
    if proc.arch is not None:
        extra += f"; arch={proc.arch.value}"
    return (
        f"Line {proc.line_id}: tooth={teeth}; surfaces={surfaces}; "
        f"findings={findings}{extra}; status={proc.planned_or_performed.value}"
    )


def structured_prompt_block(request: CodingSuggestRequest) -> str:
    """JSON-ish block for the LLM (PHI-scrubbed)."""
    lines = [
        "Structured encounter (from scribe):",
        f"- encounter_datetime: {request.encounter_datetime.isoformat()}",
        f"- provider_id: {scrub_for_llm(request.provider_id)}",
        f"- patient_id: {scrub_for_llm(request.patient_id)}",
        f"- payer: {insurance_label(request)}",
        f"- patient_age: {patient_age(request)}",
        "- procedures:",
    ]
    for proc in request.procedures:
        lines.append(
            "  * "
            + scrub_for_llm(
                f"line_id={proc.line_id}; tooth={proc.tooth_numbers}; "
                f"surfaces={proc.surfaces}; quadrant={proc.quadrant}; "
                f"arch={proc.arch}; findings={proc.findings}; "
                f"planned_or_performed={proc.planned_or_performed.value}"
            )
        )
    if request.supporting_note:
        lines.append("- supporting_note:")
        lines.append(scrub_for_llm(request.supporting_note))
    if request.attachments_present:
        lines.append("- attachments_present: " + ", ".join(request.attachments_present))
    return "\n".join(lines)


def map_flat_codes_to_lines(
    request: CodingSuggestRequest,
    *,
    cdt_codes: list[str],
    icd10_codes: list[str],
    confidence: float,
    justification: str,
) -> list[dict[str, Any]]:
    """
    Fallback when the LLM returns a flat code list: assign one CDT per line
    (round-robin) so the v1 response stays line-addressable.
    """
    codes = [c for c in cdt_codes if c]
    if not codes:
        return [
            {
                "line_id": proc.line_id,
                "cdt_code": None,
                "confidence": confidence,
                "explanation": justification,
                "icd10_codes": list(icd10_codes),
            }
            for proc in request.procedures
        ]

    out: list[dict[str, Any]] = []
    for idx, proc in enumerate(request.procedures):
        code = codes[idx] if idx < len(codes) else codes[-1]
        out.append(
            {
                "line_id": proc.line_id,
                "cdt_code": code,
                "confidence": confidence,
                "explanation": justification,
                "icd10_codes": list(icd10_codes),
            }
        )
    return out
