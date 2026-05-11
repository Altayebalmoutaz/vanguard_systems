"""Fixture-driven Layer 3 tests across representative payer payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.eligibility.normalizer import normalize

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "eligibility_271"


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def _assert_expected_scalar(canonical: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in (
        "is_active",
        "in_network",
        "coverage_percent",
        "deductible_remaining",
        "annual_max_remaining",
        "coinsurance",
        "copay",
    ):
        if key not in expected:
            continue
        exp = expected[key]
        got = canonical.get(key)
        if isinstance(exp, float):
            assert got == pytest.approx(exp), f"mismatch for {key}: got={got} exp={exp}"
        else:
            assert got == exp, f"mismatch for {key}: got={got} exp={exp}"


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_normalize_payer_fixture(fixture_path: Path) -> None:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    raw = dict(payload["raw_271"])
    request_codes = list(payload.get("request_procedure_codes") or [])
    tpsid = str(payload["trading_partner_service_id"])

    raw["_request_procedure_codes"] = request_codes
    raw["_has_secondary"] = False
    raw["_secondary_payer_id"] = None
    raw["_trading_partner_service_id"] = tpsid

    canonical = normalize(raw, payload.get("coverage_order", "primary"))
    expected = payload.get("expected", {})

    assert canonical["payer_id"] == tpsid
    _assert_expected_scalar(canonical, expected)

    expected_procedure = expected.get("procedure_expectations") or {}
    if expected_procedure:
        got_by_code = {p["cdt_code"]: p for p in canonical.get("procedure_details") or []}
        for code, exp in expected_procedure.items():
            assert code in got_by_code, f"missing procedure detail for {code}"
            for k, v in exp.items():
                assert got_by_code[code].get(k) == v, f"{code} mismatch for {k}"

    for snippet in expected.get("warning_contains") or []:
        warnings = canonical.get("normalization_warnings") or []
        assert any(snippet in w for w in warnings), f"expected warning containing: {snippet}"
