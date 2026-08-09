"""Orchestrate scribe suggest: idempotency → LLM → validate → gaps → persist."""

from __future__ import annotations

import contextlib
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
from app.coding.autonomy import decide_tier, fetch_autonomy_allowlist
from app.coding.calibration import CalibrationMap, calibrate
from app.coding.config import CodingSettings, get_coding_settings
from app.coding.gaps import (
    confidence_threshold,
    default_docs_for_code,
    fetch_cdt_metadata,
    fetch_required_documentation,
    has_blocking,
    post_check_line,
    pre_check_line,
    pre_check_request,
)
from app.coding.reliability import (
    needs_verification,
    should_use_retrieval,
    verify_line,
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
    use_retrieval = should_use_retrieval(request, fast=fast, cfg=cfg)
    note = build_clinical_note(request)
    insurance = insurance_label(request)
    age = patient_age(request)
    line_ids = [p.line_id for p in request.procedures]

    line_recs_raw: list[dict[str, Any]]
    overall_confidence = 0.0
    justification = ""

    try:
        memory = ""
        if use_retrieval and supabase is not None and app_settings.jina_api_key:
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
            use_retrieval=use_retrieval,
            warnings=warnings,
        )

    cdt_codes = [str(r.get("cdt_code")).upper().strip() for r in line_recs_raw if r.get("cdt_code")]
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
    # Keep actionable matched-rule flags; drop the noisy "insurance name
    # didn't fuzzy-match any payer_rules.payer_name" diagnostic — docs already
    # fall back via documentation_required / default_docs_for_code.
    payer_flags_out: list[str] = []
    for flag in payer.get("payer_flags") or []:
        text = str(flag)
        if "none matched encounter insurance" in text:
            logger.debug("suppressed payer_rules insurance mismatch: %s", text)
            continue
        payer_flags_out.append(text)
    warnings.extend(payer_flags_out[:12])

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

    # Lines whose code is implicated by a matched payer rule (transform/doc/bundle)
    # are candidates for the verifier pass.
    _conflict_kw = ("transform", "downcode", "document", "bundle", "replace", "alternate")
    payer_conflict_codes = {
        code
        for code in cdt_codes
        for flag in payer_flags_out
        if code in flag and any(kw in flag.lower() for kw in _conflict_kw)
    }

    # Identity by default; ops can fit/store a map from calibration.py output.
    cmap: CalibrationMap = []
    autonomy_allowlist = fetch_autonomy_allowlist(app_settings, practice_id=practice_id, cfg=cfg)

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
        explanation = str(raw.get("explanation") or justification or "")

        meta = cdt_meta.get(cdt_norm or "") or {}
        line_payer_conflict = cdt_norm in payer_conflict_codes

        # Verifier/repair pass for low-confidence, high-stakes, or payer-conflict
        # lines (no-op unless CODING_VERIFIER_ENABLED).
        if cdt_norm and needs_verification(
            cdt_code=cdt_norm,
            confidence=conf,
            payer_conflict=line_payer_conflict,
            cfg=cfg,
        ):
            verdict = verify_line(
                app_settings,
                cfg,
                line=proc,
                candidate_cdt=cdt_norm,
                candidate_description=str(meta.get("description") or ""),
                payer_notes="; ".join(f for f in payer_flags_out if cdt_norm in f),
            )
            if verdict and verdict.get("cdt_code"):
                new_code = str(verdict["cdt_code"]).upper().strip()
                accept_verdict = True
                if verdict.get("changed"):
                    verifier_validation = validate_cdt_tool(supabase, [new_code])
                    verifier_invalid = {
                        str(code).upper().strip()
                        for code in (verifier_validation.get("invalid") or [])
                    }
                    if new_code in verifier_invalid:
                        accept_verdict = False
                        warnings.extend(
                            str(flag) for flag in (verifier_validation.get("cdt_flags") or [])
                        )
                        warnings.append(
                            f"Verifier proposed invalid CDT {new_code} on line "
                            f"{proc.line_id}; kept {cdt_norm}"
                        )
                        inc("coding_verifier_total", {"result": "invalid_change"})
                    else:
                        warnings.append(
                            f"Verifier changed line {proc.line_id}: {cdt_norm} -> {new_code}"
                        )
                        inc("coding_verifier_total", {"result": "changed"})
                        cdt_norm = new_code
                        # Re-resolve reference metadata for the endorsed code.
                        meta = (
                            fetch_cdt_metadata(
                                supabase,
                                [cdt_norm],
                                ttl_seconds=cfg.coding_reference_cache_ttl_seconds,
                            ).get(cdt_norm)
                            or {}
                        )
                else:
                    inc("coding_verifier_total", {"result": "confirmed"})
                if accept_verdict:
                    explanation = str(verdict.get("explanation") or explanation)
                    with contextlib.suppress(TypeError, ValueError):
                        conf = max(conf, float(verdict.get("confidence") or conf))

        # Calibrate confidence (identity until a calibration map is fit/stored).
        conf = calibrate(conf, cmap)

        docs = list(docs_by_code.get(cdt_norm or "", []) or [])
        if not docs:
            docs = default_docs_for_code(cdt_norm)
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
        deduped_missing = _dedupe_missing(missing)
        tier = decide_tier(
            cdt_code=cdt_norm,
            calibrated_confidence=conf,
            has_blocking_gap=has_blocking(deduped_missing),
            is_valid=bool(cdt_norm) and cdt_norm not in invalid_cdt,
            payer_conflict=line_payer_conflict,
            cfg=cfg,
            allowlist=autonomy_allowlist,
        )
        recommendations.append(
            LineRecommendation(
                line_id=proc.line_id,
                cdt_code=cdt_norm,
                cdt_description=str(meta.get("description") or "") or None,
                confidence=conf,
                explanation=explanation,
                icd10_codes=[str(c).upper().strip() for c in (raw.get("icd10_codes") or [])],
                required_supporting_documentation=docs,
                missing_info=deduped_missing,
                autonomy=tier,
            )
        )

    if recommendations:
        overall_confidence = sum(r.confidence for r in recommendations) / len(recommendations)

    # Only blocking gaps force needs_info. Advisory gaps (payer/age/thin-note,
    # radiograph hint) are still surfaced but keep the codes reviewable.
    needs_info = has_blocking(global_missing) or any(
        has_blocking(r.missing_info) for r in recommendations
    )
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
    age: int | None,
    insurance: str,
    request: CodingSuggestRequest,
    supabase: Any,
    use_retrieval: bool,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], float, str]:
    try:
        memory = ""
        if use_retrieval and supabase is not None and app_settings.jina_api_key:
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
