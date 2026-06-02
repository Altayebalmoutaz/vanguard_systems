"""Layer 3 numeric consistency + optional LLM enrichment."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.eligibility.layer3_llm_enrich import apply_layer3_numeric_consistency, enrich_with_llm


def _llm_settings_stub(*, enabled: bool = True, api_key: str = "sk-test") -> SimpleNamespace:
    """Avoid pydantic-settings env overriding constructor kwargs in tests."""
    return SimpleNamespace(
        eligibility_layer3_llm_enrich_enabled=enabled,
        eligibility_layer3_llm_openrouter_api_key=api_key,
        eligibility_layer3_llm_model="openai/gpt-4o-mini",
        eligibility_layer3_llm_timeout_seconds=45.0,
    )


def test_apply_layer3_numeric_consistency_clamps_deductible() -> None:
    canonical = {
        "deductible_total": 100.0,
        "deductible_remaining": 500.0,
        "annual_max_total": 2000.0,
        "annual_max_remaining": 1500.0,
        "normalization_warnings": [],
    }
    apply_layer3_numeric_consistency(canonical)
    assert canonical["deductible_remaining"] == 100.0
    assert (
        "layer3_clamp:deductible_remaining_capped_to_deductible_total"
        in canonical["normalization_warnings"]
    )


def test_apply_layer3_numeric_consistency_clamps_out_of_pocket_max() -> None:
    canonical = {
        "out_of_pocket_max_total": 5000.0,
        "out_of_pocket_max_remaining": 6500.0,
        "normalization_warnings": [],
    }
    apply_layer3_numeric_consistency(canonical)
    assert canonical["out_of_pocket_max_remaining"] == 5000.0
    assert (
        "layer3_clamp:out_of_pocket_max_remaining_capped_to_out_of_pocket_max_total"
        in canonical["normalization_warnings"]
    )


def test_apply_layer3_numeric_consistency_clamps_annual_max() -> None:
    canonical = {
        "deductible_total": None,
        "deductible_remaining": None,
        "annual_max_total": 7000.0,
        "annual_max_remaining": 9000.0,
        "normalization_warnings": [],
    }
    apply_layer3_numeric_consistency(canonical)
    assert canonical["annual_max_remaining"] == 7000.0
    assert (
        "layer3_clamp:annual_max_remaining_capped_to_annual_max_total"
        in canonical["normalization_warnings"]
    )


def test_enrich_with_llm_noop_when_disabled() -> None:
    canonical: dict = {"is_covered": None, "normalization_warnings": []}
    enrich_with_llm(canonical, "60054", ["D0120"], settings=_llm_settings_stub(enabled=False))
    assert "coverage_confidence" not in canonical


def test_enrich_with_llm_noop_without_api_key() -> None:
    canonical: dict = {"is_covered": None, "normalization_warnings": []}
    enrich_with_llm(canonical, "60054", ["D0120"], settings=_llm_settings_stub(api_key=""))
    assert "coverage_confidence" not in canonical


def test_enrich_with_llm_merges_parsed_response() -> None:
    canonical: dict = {
        "is_covered": None,
        "in_network": None,
        "procedure_details": [{"cdt_code": "D0120", "procedure_covered": None}],
        "normalization_warnings": [],
    }
    s = _llm_settings_stub()
    body = {
        "coverage_confidence": "medium",
        "null_field_notes": {
            "is_in_network": "Payer did not send a clear in/out-of-network indicator for this service type.",
            "bogus_key": "should be dropped",
        },
        "summary": "Financial fields partially present; network ambiguous.",
    }

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    with patch("app.eligibility.layer3_llm_enrich.httpx.Client") as m_client:
        client_inst = MagicMock()
        client_inst.post.return_value = _FakeResp()
        client_inst.__enter__.return_value = client_inst
        client_inst.__exit__.return_value = None
        m_client.return_value = client_inst
        enrich_with_llm(canonical, "60054", ["D0120"], settings=s)

    assert canonical.get("coverage_confidence") == "medium"
    assert "bogus_key" not in (canonical.get("layer3_llm_null_field_notes") or {})
    assert "is_in_network" in (canonical.get("layer3_llm_null_field_notes") or {})
    assert canonical.get("layer3_llm_summary", "").startswith("Financial")


def test_enrich_with_llm_ignores_invalid_coverage_confidence() -> None:
    canonical: dict = {"normalization_warnings": []}
    s = _llm_settings_stub()

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": json.dumps({"coverage_confidence": "invalid"})}}
                ]
            }

    with patch("app.eligibility.layer3_llm_enrich.httpx.Client") as m_client:
        client_inst = MagicMock()
        client_inst.post.return_value = _FakeResp()
        client_inst.__enter__.return_value = client_inst
        client_inst.__exit__.return_value = None
        m_client.return_value = client_inst
        enrich_with_llm(canonical, "x", [], settings=s)
    assert "coverage_confidence" not in canonical
