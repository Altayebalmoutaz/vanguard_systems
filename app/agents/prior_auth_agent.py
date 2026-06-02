"""
Prior Authorization Agent — linear claw-style flow.

1. Tool: deterministic auth rules from CDT + insurance.
2. Tool: LLM structured opinion (fallback if LLM fails).
3. Tool: risk assessment from CDT + merged document list.
4. Assemble final JSON (status always pending_review).

Agent orchestrates; tools execute; LLM lives only behind prior_auth_llm_tool.
"""

from __future__ import annotations

import json
import logging
from typing import Literal, cast

from app.config import Settings
from app.integrations.payer_identity import resolve_canonical_payer_id
from app.schemas.prior_auth import PriorAuthAgentRequest, PriorAuthAgentResponse
from app.tools.prior_auth_tools import (
    apply_auth_rules_tool,
    assess_risk_tool,
    merge_risk_level,
    prior_auth_llm_tool,
)

logger = logging.getLogger(__name__)


def _llm_fallback() -> dict:
    """When LLM is unavailable: neutral opinion; rules engine remains source of truth for auth flags."""
    return {
        "requires_auth": False,
        "required_documents": [],
        "payer_rules": ["LLM unavailable — using rules engine only; verify with payer"],
        "risk_level": "medium",
        "risk_reason": "Automated reasoning skipped; manual review recommended",
    }


def run_prior_auth_agent(
    settings: Settings, request: PriorAuthAgentRequest
) -> PriorAuthAgentResponse:
    cdt_codes = list(request.coding.cdt_codes)
    insurance = request.insurance

    supabase = None
    try:
        from app.integrations.supabase_client import get_supabase_client

        supabase = get_supabase_client()
    except RuntimeError:
        pass
    except Exception as e:
        logger.debug("prior_auth: optional Supabase unavailable: %s", e)

    # --- Step 1: deterministic rules (DB + stub) ---
    rules_out = apply_auth_rules_tool(
        cdt_codes,
        insurance,
        supabase=supabase,
        patient_age=request.patient_age,
    )

    # --- Step 2: LLM reasoning (structured JSON); fallback on any failure ---
    llm_out: dict
    try:
        llm_out = prior_auth_llm_tool(settings, request)
    except (RuntimeError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("prior_auth LLM fallback: %s", e)
        llm_out = _llm_fallback()
    except Exception as e:
        logger.warning("prior_auth LLM unexpected error: %s", e)
        llm_out = _llm_fallback()

    # --- Merge rules + LLM (OR on requires_auth; union lists) ---
    requires_auth = bool(rules_out["requires_auth"] or llm_out.get("requires_auth", False))
    required_documents = list(
        dict.fromkeys(
            list(rules_out["required_documents"]) + list(llm_out.get("required_documents") or [])
        )
    )
    payer_rules = list(
        dict.fromkeys(list(rules_out["payer_rules"]) + list(llm_out.get("payer_rules") or []))
    )

    # --- Step 3: risk tool uses merged document expectations ---
    risk_tool = assess_risk_tool(cdt_codes, required_documents)
    risk_level_raw = merge_risk_level(
        risk_tool["risk_level"],
        str(llm_out.get("risk_level") or "low"),
    )
    risk_final: Literal["low", "medium", "high"]
    if risk_level_raw in ("low", "medium", "high"):
        risk_final = cast(Literal["low", "medium", "high"], risk_level_raw)
    else:
        risk_final = "medium"

    risk_reason_parts = [risk_tool["risk_reason"]]
    llm_reason = str(llm_out.get("risk_reason") or "").strip()
    if llm_reason:
        risk_reason_parts.append(f"Model note: {llm_reason}")
    risk_reason = " | ".join(risk_reason_parts)

    response = PriorAuthAgentResponse(
        requires_auth=requires_auth,
        required_documents=required_documents,
        payer_rules=payer_rules,
        risk_level=risk_final,
        risk_reason=risk_reason,
        status="pending_review",
    )

    if supabase is not None:
        try:
            from app.integrations.agent_runs import AGENT_PRIOR_AUTH, insert_agent_run

            rid = resolve_canonical_payer_id(supabase, insurance)
            gate_blocked = bool(requires_auth or required_documents)
            insert_agent_run(
                supabase,
                agent=AGENT_PRIOR_AUTH,
                input_json=request.model_dump(mode="json"),
                output_json=response.model_dump(mode="json"),
                meta={
                    "claim_gate_blocked": gate_blocked,
                    "rules_engine_requires_auth": bool(rules_out["requires_auth"]),
                    "llm_requires_auth": bool(llm_out.get("requires_auth")),
                },
                payer_id=rid,
                patient_id=request.patient_id,
                practice_id=request.practice_id,
            )
        except Exception as e:
            logger.warning("prior_auth: agent_runs persist skipped: %s", e)

    return response
