"""Reliability controls: retrieval gating + verifier/repair pass.

Keeps the chairside happy path fast for routine visits while spending extra
retrieval/verification budget only where it changes outcomes (non-routine,
low-confidence, high-stakes, or payer-conflict lines).
"""

from __future__ import annotations

import logging
import re

from app.coding.config import CodingSettings
from app.coding.schemas import CodingSuggestRequest, ProcedureLine
from app.config import Settings
from app.llm.coding_llm import llm_verify_line
from app.security.phi import scrub_for_log

logger = logging.getLogger(__name__)

# Routine, high-frequency dentistry that rarely benefits from retrieval.
_ROUTINE_TOKENS = (
    "exam",
    "evaluation",
    "periodic",
    "comprehensive oral",
    "prophy",
    "prophylaxis",
    "cleaning",
    "bitewing",
    "fluoride",
    "varnish",
    "radiograph",
    "x-ray",
    "xray",
    "recall",
    "sealant",
)
# Non-routine work where reference retrieval materially helps code selection.
# Matched on word boundaries so "periodic" does not trip "perio", etc.
_NON_ROUTINE_TOKENS = (
    "crown",
    "onlay",
    "inlay",
    "endo",
    "endodont",
    "root canal",
    "pulp",
    "implant",
    "extraction",
    "surgical",
    "graft",
    "periodont",
    "scaling and root",
    "denture",
    "bridge",
    "pontic",
    "buildup",
    "build-up",
    "post and core",
    "veneer",
)
_NON_ROUTINE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _NON_ROUTINE_TOKENS) + r")",
)
# D4346 is a frequent false-positive for laser / SRP adjuncts; always second-pass it.
_ALWAYS_VERIFY_CODES = frozenset({"D4346"})


def _blob(line: ProcedureLine) -> str:
    return " ".join(line.findings).lower()


def encounter_is_routine(request: CodingSuggestRequest) -> bool:
    """True only if every line looks routine and none looks non-routine."""
    if not request.procedures:
        return True
    for line in request.procedures:
        blob = _blob(line)
        if _NON_ROUTINE_RE.search(blob):
            return False
        if not any(tok in blob for tok in _ROUTINE_TOKENS):
            return False  # unknown/ambiguous -> not clearly routine
    return True


def should_use_retrieval(request: CodingSuggestRequest, *, fast: bool, cfg: CodingSettings) -> bool:
    """Retrieval on for slow mode, or (fast + non-routine) when default is enabled."""
    if not fast:
        return True
    return cfg.coding_retrieval_default and not encounter_is_routine(request)


def is_high_stakes(cdt_code: str | None, cfg: CodingSettings) -> bool:
    if not cdt_code:
        return False
    code = cdt_code.upper().strip()
    return code.startswith(cfg.high_stakes_prefixes)


def needs_verification(
    *,
    cdt_code: str | None,
    confidence: float,
    payer_conflict: bool,
    cfg: CodingSettings,
) -> bool:
    if not cdt_code:
        return False
    code = cdt_code.upper().strip()
    if code in _ALWAYS_VERIFY_CODES:
        return True
    if not cfg.coding_verifier_enabled:
        return False
    return (
        confidence < cfg.coding_verifier_confidence_threshold
        or is_high_stakes(code, cfg)
        or payer_conflict
    )


def verify_line(
    settings: Settings,
    cfg: CodingSettings,
    *,
    line: ProcedureLine,
    candidate_cdt: str,
    candidate_description: str,
    payer_notes: str,
) -> dict[str, object] | None:
    """Run the verifier; return {'cdt_code','confidence','explanation','changed'}.

    Returns ``None`` on any failure so the caller keeps the original code.
    """
    line_summary = (
        f"line_id={line.line_id}; teeth={list(line.tooth_numbers)}; "
        f"surfaces={list(line.surfaces)}; findings={list(line.findings)}; "
        f"status={line.planned_or_performed}"
    )
    try:
        result = llm_verify_line(
            settings,
            line_summary=line_summary,
            candidate_cdt=candidate_cdt,
            candidate_description=candidate_description,
            payer_notes=payer_notes,
            timeout_seconds=cfg.coding_llm_timeout_seconds,
            max_retries=0,
        )
    except Exception as exc:
        logger.warning(
            "verifier pass failed for line %s: %s", line.line_id, scrub_for_log(str(exc))
        )
        return None
    endorsed = result.get("cdt_code")
    if not endorsed:
        return None
    result["changed"] = str(endorsed).upper().strip() != candidate_cdt.upper().strip()
    return result
