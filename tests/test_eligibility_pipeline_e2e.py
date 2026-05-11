"""
End-to-end eligibility pipeline tests (Layer 3–6).

``call_stedi`` (Layer 2) is mocked with each ``fixtures/raw/*.json`` payload.
``get_supabase`` is patched to a local MagicMock so inserts and prior-auth lookups run
without a live database, while :func:`app.eligibility.router.route` executes normally.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.eligibility.models import EligibilityRequest, TriggerEvent
from app.eligibility.services import run_realtime_pipeline

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_RAW = _ROOT / "fixtures" / "raw"


def _load_raw(name: str) -> dict:
    with (_FIXTURES_RAW / name).open(encoding="utf-8") as f:
        return json.load(f)


def _request(*, payer_id: str, cdt_codes: list[str]) -> EligibilityRequest:
    return EligibilityRequest(
        patient_id=uuid4(),
        first_name="Test",
        last_name="Patient",
        dob=date(1990, 1, 15),
        subscriber_id="SUBSCRIBER-E2E-1",
        primary_payer_id=str(payer_id).strip(),
        cdt_codes=cdt_codes,
        trigger_event=TriggerEvent.APPOINTMENT_BOOKED,
    )


def _settings_stub() -> MagicMock:
    """Duck-type :class:`~app.eligibility.config.EligibilitySettings` for ``build_payload`` (Stedi is mocked)."""
    m = MagicMock()
    m.provider_name = "Mock Dental Practice"
    m.provider_npi = "1999999984"
    m.provider_tax_id = "123456789"
    return m


def _stub_supabase() -> MagicMock:
    """Minimal Supabase client so Layer 6 ``route`` and DB inserts do not hit the network."""
    uid = str(uuid4())
    client = MagicMock()

    def table(name: str) -> MagicMock:
        t = MagicMock()
        if name == "payer_prior_auth_rules":
            end = MagicMock()
            end.execute.return_value = MagicMock(data=[])
            t.select.return_value.eq.return_value.eq.return_value.limit.return_value = end
        elif name == "payer_fee_schedules":
            end = MagicMock()
            end.execute.return_value = MagicMock(data=[])
            t.select.return_value.eq.return_value.lte.return_value.order.return_value = end
        elif name == "eligibility_checks":
            ins = MagicMock()
            ins.execute.return_value = MagicMock(data=[{"id": uid}])
            t.insert.return_value = ins
        elif name == "procedure_estimates":
            ins = MagicMock()
            ins.execute.return_value = MagicMock(data=[])
            t.insert.return_value = ins
        return t

    client.table.side_effect = table
    return client


def _assert_no_silent_core_nulls(canonical: dict) -> None:
    assert canonical.get("is_active") is not None
    assert canonical.get("is_covered") is not None
    assert canonical.get("response_complete") is not None
    mf = canonical.get("missing_fields")
    assert mf is not None
    assert isinstance(mf, list)
    ec = canonical.get("eligibility_canonical")
    if isinstance(ec, dict):
        assert ec.get("procedure_covered") is not None


def _run_with_fixture(
    filename: str,
    *,
    cdt_codes: list[str],
) -> dict:
    raw = _load_raw(filename)
    tpsid = str(raw["tradingPartnerServiceId"])
    req = _request(payer_id=tpsid, cdt_codes=cdt_codes)
    settings = _settings_stub()
    with (
        patch("app.eligibility.services.call_stedi", return_value=copy.deepcopy(raw)) as m_stedi,
        patch("app.eligibility.services.get_supabase", return_value=_stub_supabase()) as m_sb,
    ):
        out = run_realtime_pipeline(
            req,
            settings=settings,
            coverage_order="primary",
            trading_partner_service_id=tpsid,
        )
    assert m_stedi.called
    assert m_sb.called
    return out


@pytest.mark.parametrize(
    ("filename", "cdt_codes", "expected_status", "extra"),
    [
        (
            "happy_path_innetwork.json",
            ["D0120"],
            "CLEARED",
            {"expect_in_network": True},
        ),
        (
            "active_outofnetwork.json",
            ["D1110"],
            "CLEARED",
            {"expect_in_network": False},
        ),
        (
            "active_missing_financials.json",
            ["D0274"],
            "CLEARED",
            {"expect_response_complete": True},
        ),
        (
            "inactive_subscriber.json",
            ["D1110"],
            "INACTIVE",
            {},
        ),
        (
            "procedure_not_covered.json",
            ["D2740"],
            "NOT_COVERED",
            {},
        ),
    ],
)
def test_eligibility_e2e_fixture(
    filename: str,
    cdt_codes: list[str],
    expected_status: str,
    extra: dict,
) -> None:
    out = _run_with_fixture(filename, cdt_codes=cdt_codes)
    routing = out["routing"]
    canonical = out["canonical"]

    assert routing["status"] == expected_status

    if extra.get("expect_in_network") is True:
        assert canonical.get("in_network") is True
    if extra.get("expect_in_network") is False:
        assert canonical.get("in_network") is False
    if extra.get("expect_incomplete_non_empty_missing"):
        assert canonical.get("response_complete") is False
        assert len(canonical.get("missing_fields") or []) > 0
    if extra.get("expect_response_complete"):
        assert canonical.get("response_complete") is True

    if expected_status == "INACTIVE":
        assert canonical.get("is_active") is False
        assert canonical.get("is_covered") is False
        assert canonical.get("response_complete") is not None
        mf = canonical.get("missing_fields")
        assert isinstance(mf, list)
        ec = canonical.get("eligibility_canonical")
        if isinstance(ec, dict):
            assert ec.get("procedure_covered") is False
        return

    _assert_no_silent_core_nulls(canonical)
