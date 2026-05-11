"""
Dental Coding Agent — explicit synchronous loop.

Flow (claw-style):
1. Agent step: call tool to generate codes (tool → optional pgvector CDT memory → LLM / OpenRouter).
2. Agent step: validate ICD-10 via Supabase (`icd10_dental_gem_axis`).
3. Agent step: validate CDT via Supabase (`cdt_codes`).
4. Agent step: apply payer rules from `payer_rules`.
5. Agent step: assemble JSON for human review (status always pending_review).

Agent logic stays dumb and linear; tools own side effects and domain rules.
"""

from __future__ import annotations

from app.config import Settings
from app.schemas.coding import CodingAgentRequest, CodingAgentResponse
from app.tools.coding_tools import (
    apply_payer_rules_tool,
    generate_codes_tool,
    validate_cdt_tool,
    validate_icd_tool,
)
from supabase import Client


def run_coding_agent(
    settings: Settings,
    supabase: Client | None,
    request: CodingAgentRequest,
) -> CodingAgentResponse:
    # --- Step 1: LLM proposal (invoked only inside generate_codes_tool) ---
    generated = generate_codes_tool(
        settings,
        supabase,
        request.clinical_note,
        request.patient_age,
        request.insurance,
    )
    cdt_codes = list(generated.get("cdt_codes") or [])
    icd10_codes = list(generated.get("icd10_codes") or [])
    confidence = float(generated.get("confidence") or 0.0)
    justification = str(generated.get("justification") or "")

    # --- Step 2: validate ICD-10 against dental GEM / axis table ---
    icd_validation = validate_icd_tool(supabase, icd10_codes)

    # --- Step 3: validate CDT against local reference (subset of ADA CDT) ---
    cdt_validation = validate_cdt_tool(supabase, cdt_codes)

    # --- Step 4: payer rules (single public.payer_rules table) ---
    payer = apply_payer_rules_tool(
        supabase,
        cdt_codes,
        request.insurance,
        request.patient_age,
    )

    # --- Step 5: assemble final payload; human-in-the-loop default ---
    payer_flags: list[str] = []
    payer_flags.extend(payer.get("payer_flags") or [])
    payer_flags.extend(icd_validation.get("icd_flags") or [])
    payer_flags.extend(cdt_validation.get("cdt_flags") or [])

    if icd_validation.get("invalid"):
        payer_flags.append("Human review: one or more ICD-10 codes failed master lookup")
    if cdt_validation.get("invalid"):
        payer_flags.append("Human review: one or more CDT codes missing from reference table")
    if confidence < 0.75:
        payer_flags.append("Human review: model confidence below 0.75")

    return CodingAgentResponse(
        cdt_codes=cdt_codes,
        icd10_codes=icd10_codes,
        confidence=confidence,
        justification=justification,
        payer_flags=payer_flags,
        payer_rules_matched=list(payer.get("payer_rules_matched") or []),
        status="pending_review",
    )
