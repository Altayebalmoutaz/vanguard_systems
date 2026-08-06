"""Orchestrate scribe suggest: idempotency → LLM → validate → gaps → persist."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

import httpx

from app.audit.writer import write_audit_log
from app.coding.adapter import (
    build_clinical_note,
    insurance_label,
    map_flat_codes_to_lines,
    patient_age,
    structured_prompt_block,
)
from app.coding.config import CodingSettings, get_coding_settings
from app.coding.gaps import (
    confidence_threshold,
    default_docs_for_code,
    fetch_cdt_metadata,
    fetch_required_documentation,
    post_check_line,
    pre_check_line,
    pre_check_request,
)
from app.coding.schemas import (
    CodingSuggestRequest,
    CodingSuggestResponse,
    LineRecommendation,
    MissingInfoItem,
)
from app.coding.store import fetch_run_by_request_id, insert_coding_run
from app.config import Settings, get_settings
from app.integrations.supabase_client import create_supabase
from app.llm.coding_llm import llm_generate_codes, llm_generate_line_recommendations
from app.observability.metrics import inc
from app.security.phi import scrub_for_log
from app.services.cdt_vector_memory import fetch_cdt_vector_memory
from app.tools.coding_tools import apply_payer_rules_tool, validate_cdt_tool, validate_icd_tool

logger = logging.getLogger(__name__)


def run_coding_suggest(
    request: CodingSuggestRequest,
    *,
    settings: Settings | None = None,
    coding_settings: CodingSettings | None = None,
) -> CodingSuggestResponse:
    """Synchronous suggest path for real-time dentist review."""
    started = time.perf_counter()
    app_settings = settings or get_settings()
    cfg = coding_settings or get_coding_settings()
    practice_id = request.practice_id.strip()

    existing = fetch_run_by_request_id(
        app_settings, practice_id=practice_id, request_id=request.request_id
    )
    if existing and isinstance(existing.get("response_payload"), dict):
        try:
            replay = CodingSuggestResponse.model_validate(existing["response_payload"])
            replay.idempotent_replay = True
            if existing.get("id") and replay.coding_run_id is None:
                replay.coding_run_id = UUID(str(existing["id"]))
            inc("coding_suggest_total", {"result": "idempotent"})
            return replay
        except Exception as exc:
            logger.warning(
                "coding_runs replay validate failed request_id=%s: %s",
                request.request_id,
                scrub_for_log(str(exc)),
            )

    supabase = create_supabase(app_settings)
    warnings: list[str] = []
    global_missing = pre_check_request(request)
    line_pre: dict[str, list[MissingInfoItem]] = {
        p.line_id: pre_check_line(p) for p in request.procedures
    }

    fast = bool(request.fast or cfg.coding_default_fast_mode)
    note = build_clinical_note(request)
    insurance = insurance_label(request)
    age = patient_age(request)
    line_ids = [p.line_id for p in request.procedures]

    line_recs_raw: list[dict[str, Any]]
    overall_confidence = 0.0
    justification = ""

    try:
        memory = ""
        if not fast and supabase is not None and app_settings.jina_api_key:
            memory = fetch_cdt_vector_memory(
                supabase,
                note,
                insurance,
                jina_api_key=app_settings.jina_api_key,
                match_count=app_settings.cdt_vector_match_count,
                match_threshold=app_settings.cdt_vector_match_threshold,
            )
        generated = llm_generate_line_recommendations(
            app_settings,
            structured_block=structured_prompt_block(request),
            clinical_note=note,
            patient_age=age,
            insurance=insurance,
            line_ids=line_ids,
            retrieval_context=memory or None,
            timeout_seconds=cfg.coding_llm_timeout_seconds,
            max_retries=cfg.coding_llm_max_retries,
        )
        line_recs_raw = list(generated.get("recommendations") or [])
        overall_confidence = float(generated.get("overall_confidence") or 0.0)
        justification = str(generated.get("justification") or "")
    except (RuntimeError, httpx.HTTPError, httpx.TimeoutException, ValueError, TypeError) as exc:
        logger.warning(
            "line-level LLM failed; falling back to flat coding path: %s",
            scrub_for_log(str(exc)),
        )
        warnings.append(f"LLM line path degraded: {type(exc).__name__}")
        line_recs_raw, overall_confidence, justification = _flat_fallback(
            app_settings,
            cfg,
            note=note,
            age=age,
            insurance=insurance,
            request=request,
            supabase=supabase,
            fast=fast,
            warnings=warnings,
        )

    cdt_codes = [
        str(r.get("cdt_code")).upper().strip()
        for r in line_recs_raw
        if r.get("cdt_code")
    ]
    icd_codes: list[str] = []
    for r in line_recs_raw:
        for code in r.get("icd10_codes") or []:
            if code and str(code) not in icd_codes:
                icd_codes.append(str(code).upper().strip())

    cdt_validation = validate_cdt_tool(supabase, cdt_codes)
    icd_validation = validate_icd_tool(supabase, icd_codes)
    payer = apply_payer_rules_tool(supabase, cdt_codes, insurance, age)
    if cdt_validation.get("cdt_flags"):
        warnings.extend(str(f) for f in cdt_validation["cdt_flags"])
    if icd_validation.get("icd_flags"):
        warnings.extend(str(f) for f in icd_validation["icd_flags"])
    if payer.get("payer_flags"):
        warnings.extend(str(f) for f in payer["payer_flags"][:12])

    invalid_cdt = {str(c).upper().strip() for c in (cdt_validation.get("invalid") or [])}
    cdt_meta = fetch_cdt_metadata(
        supabase, cdt_codes, ttl_seconds=cfg.coding_reference_cache_ttl_seconds
    )
    docs_by_code = fetch_required_documentation(
        supabase,
        cdt_codes=cdt_codes,
        payer_name=insurance,
        ttl_seconds=cfg.coding_reference_cache_ttl_seconds,
    )
    threshold = confidence_threshold(cfg)

    by_line = {str(r.get("line_id")): r for r in line_recs_raw}
    recommendations: list[LineRecommendation] = []
    for proc in request.procedures:
        raw = by_line.get(proc.line_id) or {
            "line_id": proc.line_id,
            "cdt_code": None,
            "confidence": 0.0,
            "explanation": justification,
            "icd10_codes": [],
        }
        cdt = raw.get("cdt_code")
        cdt_norm = str(cdt).upper().strip() if cdt else None
        if cdt_norm and cdt_norm in invalid_cdt:
            warnings.append(f"CDT {cdt_norm} failed reference validation on line {proc.line_id}")
        try:
            conf = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        docs = list(docs_by_code.get(cdt_norm or "", []) or [])
        if not docs:
            docs = default_docs_for_code(cdt_norm)
        meta = cdt_meta.get(cdt_norm or "") or {}
        missing = list(line_pre.get(proc.line_id) or [])
        missing.extend(
            post_check_line(
                proc,
                cdt_code=cdt_norm,
                attachments_present=request.attachments_present,
                confidence=conf,
                threshold=threshold,
                cdt_meta=meta,
            )
        )
        recommendations.append(
            LineRecommendation(
                line_id=proc.line_id,
                cdt_code=cdt_norm,
                cdt_description=str(meta.get("description") or "") or None,
                confidence=conf,
                explanation=str(raw.get("explanation") or justification or ""),
                icd10_codes=[str(c).upper().strip() for c in (raw.get("icd10_codes") or [])],
                required_supporting_documentation=docs,
                missing_info=_dedupe_missing(missing),
            )
        )

    if recommendations:
        overall_confidence = sum(r.confidence for r in recommendations) / len(recommendations)

    needs_info = bool(global_missing) or any(r.missing_info for r in recommendations)
    status = "needs_info" if needs_info else "pending_review"

    response = CodingSuggestResponse(
        request_id=request.request_id,
        status=status,
        recommendations=recommendations,
        global_missing_info=_dedupe_missing(global_missing),
        warnings=warnings,
        overall_confidence=max(0.0, min(1.0, overall_confidence)),
        idempotent_replay=False,
    )

    run_id = insert_coding_run(
        app_settings,
        practice_id=practice_id,
        request_id=request.request_id,
        patient_id=request.patient_id,
        provider_id=request.provider_id,
        encounter_datetime=request.encounter_datetime,
        payer_id=request.payer.id,
        request_payload=request.model_dump(mode="json"),
        response_payload=response.model_dump(mode="json"),
        status=status,
        overall_confidence=response.overall_confidence,
    )
    response.coding_run_id = run_id

    write_audit_log(
        app_settings,
        practice_id=practice_id,
        action="coding.suggest",
        entity_type="coding_run",
        entity_id=run_id,
        performed_by="coding_agent_v1",
        metadata={
            "request_id": str(request.request_id),
            "status": status,
            "overall_confidence": response.overall_confidence,
            "line_count": len(recommendations),
            "fast": fast,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    inc("coding_suggest_total", {"result": status})
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    inc("coding_suggest_latency_ms_sum", amount=max(elapsed_ms, 0))
    logger.info(
        "coding suggest done practice_id=%s request_id=%s status=%s latency_ms=%s",
        practice_id,
        request.request_id,
        status,
        elapsed_ms,
    )
    return response


def _flat_fallback(
    app_settings: Settings,
    cfg: CodingSettings,
    *,
    note: str,
    age: int,
    insurance: str,
    request: CodingSuggestRequest,
    supabase: Any,
    fast: bool,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], float, str]:
    try:
        memory = ""
        if not fast and supabase is not None and app_settings.jina_api_key:
            memory = fetch_cdt_vector_memory(
                supabase,
                note,
                insurance,
                jina_api_key=app_settings.jina_api_key,
                match_count=app_settings.cdt_vector_match_count,
                match_threshold=app_settings.cdt_vector_match_threshold,
            )
        flat = llm_generate_codes(
            app_settings,
            note,
            age,
            insurance,
            retrieval_context=memory or None,
            timeout_seconds=cfg.coding_llm_timeout_seconds,
            max_retries=cfg.coding_llm_max_retries,
        )
        mapped = map_flat_codes_to_lines(
            request,
            cdt_codes=list(flat.get("cdt_codes") or []),
            icd10_codes=list(flat.get("icd10_codes") or []),
            confidence=float(flat.get("confidence") or 0.0),
            justification=str(flat.get("justification") or ""),
        )
        return mapped, float(flat.get("confidence") or 0.0), str(flat.get("justification") or "")
    except Exception as exc:
        warnings.append(f"LLM unavailable: {type(exc).__name__}")
        logger.warning("flat LLM fallback failed: %s", scrub_for_log(str(exc)))
        empty = map_flat_codes_to_lines(
            request,
            cdt_codes=[],
            icd10_codes=[],
            confidence=0.0,
            justification="Coding model unavailable; complete missing info and retry",
        )
        return empty, 0.0, "Coding model unavailable"


def _dedupe_missing(items: list[MissingInfoItem]) -> list[MissingInfoItem]:
    seen: set[tuple[str, str]] = set()
    out: list[MissingInfoItem] = []
    for item in items:
        key = (item.code.value, item.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
