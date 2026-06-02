"""Parameterized normalization checks against bundled TR3-style 271 fixtures."""

from __future__ import annotations

import pytest

from app.eligibility.normalizer import normalize
from tests.eligibility_agent.fixture_bridge import (
    FIXTURE_JSON_PATH,
    fixture_doc_hint,
    fixture_to_stedi_raw,
    load_all_fixtures,
    semantic_expectations,
)

ALL_FIXTURES = load_all_fixtures()


@pytest.mark.parametrize(
    "fixture",
    ALL_FIXTURES,
    ids=lambda f: str(f.get("fixture_id") or "MISSING_FIXTURE_ID"),
)
def test_tr3_fixture_through_normalize(fixture: dict) -> None:
    """
    Bridge fixture semantics → Stedi-shaped JSON → Layer 3 normalize.

    On failure, see ``fixture_doc_hint`` for corpus path (271_fixtures.json).
    """
    fid = str(fixture.get("fixture_id") or "")
    hint = fixture_doc_hint(fid)

    raw = fixture_to_stedi_raw(fixture)
    raw["_request_procedure_codes"] = ["D0120"]
    raw["_trading_partner_service_id"] = str(fixture.get("payer_id") or "TEST")

    canonical = normalize(raw, "primary")
    exp = semantic_expectations(fixture)

    if exp.get("_reject_fixture"):
        aaa_codes = list(exp.get("_aaa_codes_expected") or [])
        if exp.get("_expect_min_payer_aaa_errors"):
            assert len(canonical["payer_aaa_errors"]) >= 1, hint
        extracted = [str(e.get("code") or "") for e in (canonical["payer_aaa_errors"] or [])]
        for code in aaa_codes:
            assert code in extracted, (
                f"{hint} expected AAA code {code!r} in payer_aaa_errors={extracted!r}"
            )
        return

    if "is_active" in exp:
        assert canonical["is_active"] == exp["is_active"], hint

    if "is_covered" in exp:
        assert canonical["is_covered"] == exp["is_covered"], hint

    if "in_network" in exp:
        assert canonical["in_network"] == exp["in_network"], hint

    if "deductible_total" in exp:
        assert canonical["deductible_total"] == pytest.approx(
            exp["deductible_total"], rel=1e-4, abs=0.01
        ), hint

    if "deductible_remaining" in exp:
        assert canonical["deductible_remaining"] == pytest.approx(
            exp["deductible_remaining"], rel=1e-4, abs=0.01
        ), hint

    if "annual_max_total" in exp:
        assert canonical["annual_max_total"] == pytest.approx(
            exp["annual_max_total"], rel=1e-4, abs=0.01
        ), hint

    if "annual_max_remaining" in exp:
        assert canonical["annual_max_remaining"] == pytest.approx(
            exp["annual_max_remaining"], rel=1e-4, abs=0.01
        ), hint

    if "out_of_pocket_max_total" in exp:
        assert canonical["out_of_pocket_max_total"] == pytest.approx(
            exp["out_of_pocket_max_total"], rel=1e-4, abs=0.01
        ), hint

    if "out_of_pocket_max_remaining" in exp:
        assert canonical["out_of_pocket_max_remaining"] == pytest.approx(
            exp["out_of_pocket_max_remaining"], rel=1e-4, abs=0.01
        ), hint

    if "coinsurance_patient_pct_stc35_inn" in exp:
        db = canonical.get("dental_benefit_breakdown") or {}
        by_stc = db.get("coinsurance_patient_pct_by_stc") or {}
        assert isinstance(by_stc, dict), hint
        assert by_stc.get("35") == pytest.approx(
            exp["coinsurance_patient_pct_stc35_inn"], rel=1e-4, abs=0.01
        ), hint


def test_fixture_bundle_path_exists() -> None:
    assert FIXTURE_JSON_PATH.is_file(), f"Missing bundled fixtures at {FIXTURE_JSON_PATH}"


def test_fixture_bundle_has_expected_count() -> None:
    assert len(ALL_FIXTURES) == 100, "Update README if synthetic corpus size changes"
