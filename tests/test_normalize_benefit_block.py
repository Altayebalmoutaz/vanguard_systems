"""Unit tests for ``normalize_benefit_block`` — Step 2 raw 271 fixtures."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app.eligibility.benefit_block import normalize_benefit_block

_ROOT = Path(__file__).resolve().parents[1]
_RAW = _ROOT / "fixtures" / "raw"


def _load_fixture(name: str) -> dict:
    path = _RAW / name
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _payer_id(data: dict) -> str:
    return str(data["payer"]["payorIdentification"])


def _assert_network_policy(row: dict) -> None:
    """``is_in_network`` is bool, or None with an encoded reason in ``benefit_type``."""
    inn = row["is_in_network"]
    assert inn is True or inn is False or inn is None
    if inn is None:
        assert row["benefit_type"].startswith("NETWORK_UNKNOWN:")


def _assert_core(row: dict, *, benefit_amount_may_be_none: bool) -> None:
    assert isinstance(row["is_covered"], bool)
    assert row["is_covered"] is not None
    assert row["benefit_type"] is not None
    assert row["qualifier"] is not None
    assert row["time_qualifier"] is not None
    if not benefit_amount_may_be_none:
        assert row["benefit_amount"] is not None
    _assert_network_policy(row)


def test_variant_1_top_level_procedure_code_happy_path_innetwork() -> None:
    """Variant 1: top-level ``procedureCode`` (happy_path_innetwork.json)."""
    data = _load_fixture("happy_path_innetwork.json")
    payer_id = _payer_id(data)
    raw = next(b for b in data["benefitsInformation"] if b.get("procedureCode") == "D0120")
    row = normalize_benefit_block(raw, payer_id)
    _assert_core(row, benefit_amount_may_be_none=True)
    assert row["qualifier"] == "D0120"
    assert row["is_covered"] is True
    assert row["is_in_network"] is True


def test_variant_2_composite_medical_procedure_identifier_happy_path_derived() -> None:
    """Variant 2: ``compositeMedicalProcedureIdentifier`` instead of top-level procedure code."""
    data = _load_fixture("happy_path_innetwork.json")
    payer_id = _payer_id(data)
    base = next(b for b in data["benefitsInformation"] if b.get("procedureCode") == "D0120")
    raw = copy.deepcopy(base)
    del raw["procedureCode"]
    raw["compositeMedicalProcedureIdentifier"] = {
        "productOrServiceIDQualifier": "AD",
        "procedureCode": "D0120",
    }
    row = normalize_benefit_block(raw, payer_id)
    _assert_core(row, benefit_amount_may_be_none=True)
    assert row["qualifier"].startswith("D0120")
    assert "AD" in row["qualifier"]
    assert row["is_covered"] is True


def test_variant_3_remaining_only_time_qualifier_active_missing_financials() -> None:
    """Variant 3: Remaining slice only — no total row on the EB (active_missing_financials.json)."""
    data = _load_fixture("active_missing_financials.json")
    payer_id = _payer_id(data)
    raw = copy.deepcopy(
        next(b for b in data["benefitsInformation"] if b.get("name") == "Deductible Remaining")
    )
    raw["planNetworkDescription"] = "In Network"
    bdi = raw.setdefault("benefitsDateInformation", {})
    bdi["timeQualifier"] = "Remaining"
    row = normalize_benefit_block(raw, payer_id)
    _assert_core(row, benefit_amount_may_be_none=False)
    assert row["time_qualifier"] == "Remaining"
    assert "remaining_only_no_total_row" in row["benefit_type"]


def test_variant_4_benefit_code_n_or_i_procedure_not_covered() -> None:
    """Variant 4: benefit code ``N`` or ``I`` means not covered (procedure_not_covered.json)."""
    data = _load_fixture("procedure_not_covered.json")
    payer_id = _payer_id(data)
    raw_n = next(b for b in data["benefitsInformation"] if b.get("code") == "N")
    row_n = normalize_benefit_block(raw_n, payer_id)
    _assert_core(row_n, benefit_amount_may_be_none=True)
    assert row_n["is_covered"] is False
    assert row_n["qualifier"] == "D2740"

    raw_i = copy.deepcopy(raw_n)
    raw_i["code"] = "I"
    raw_i["name"] = "Not covered (I)"
    row_i = normalize_benefit_block(raw_i, payer_id)
    _assert_core(row_i, benefit_amount_may_be_none=True)
    assert row_i["is_covered"] is False

    # inactive_subscriber.json — still a Step 2 raw file; administrative EB row
    inv = _load_fixture("inactive_subscriber.json")
    row_in = normalize_benefit_block(inv["benefitsInformation"][0], _payer_id(inv))
    _assert_core(row_in, benefit_amount_may_be_none=True)
    assert row_in["is_covered"] is False


def test_variant_5_duplicate_inn_oon_do_not_overwrite_active_outofnetwork() -> None:
    """Variant 5: duplicate INN vs OON blocks — each call returns its own ``is_in_network``."""
    data = _load_fixture("active_outofnetwork.json")
    payer_id = _payer_id(data)
    inn_row = next(
        b
        for b in data["benefitsInformation"]
        if b.get("procedureCode") == "D1110"
        and "in network" in str(b.get("planNetworkDescription", "")).lower()
    )
    oon_row = next(
        b
        for b in data["benefitsInformation"]
        if b.get("procedureCode") == "D1110"
        and "out of network" in str(b.get("planNetworkDescription", "")).lower()
    )
    a = normalize_benefit_block(inn_row, payer_id)
    b = normalize_benefit_block(oon_row, payer_id)
    _assert_core(a, benefit_amount_may_be_none=True)
    _assert_core(b, benefit_amount_may_be_none=True)
    assert a["is_in_network"] is True
    assert b["is_in_network"] is False
    assert a["qualifier"] == b["qualifier"] == "D1110"
    assert a is not b
    assert a["is_in_network"] != b["is_in_network"]
