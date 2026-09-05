from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.config import Settings
from app.copilot.tools import (
    BLOCKED_WRITE_METHODS,
    READ_ONLY_TOOL_NAMES,
    TOOL_SPECS,
    ToolContext,
    UnknownCopilotToolError,
    execute_tool,
)
from app.integrations.opendental.client import OpenDentalClient

_FIXTURES = Path(__file__).parent / "fixtures" / "opendental"
_PATIENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _client() -> OpenDentalClient:
    return OpenDentalClient(
        base_url="http://localhost:30222/api/v1",
        developer_key="dev",
        customer_key="cust",
        timeout_seconds=5.0,
        replay_dir=str(_FIXTURES),
    )


def _ctx() -> ToolContext:
    return ToolContext(
        settings=Settings(),
        practice_id="practice-1",
        patient_id=_PATIENT_ID,
        od_pat_num=1,
        client=_client(),
        profile={
            "patient": {"id": str(_PATIENT_ID), "first_name": "Aardvark", "last_name": "Dent"},
            "latest_eligibility_check": {
                "id": "22222222-2222-2222-2222-222222222222",
                "is_active": True,
                "coverage_percent": 80,
                "raw_response": {"secret": "drop-me"},
            },
            "agent_runs": [],
        },
    )


def test_registry_is_read_only() -> None:
    names = {spec["function"]["name"] for spec in TOOL_SPECS}
    assert names == set(READ_ONLY_TOOL_NAMES)
    assert names.isdisjoint(BLOCKED_WRITE_METHODS)
    for blocked in BLOCKED_WRITE_METHODS:
        with pytest.raises(UnknownCopilotToolError):
            execute_tool(blocked, {}, ctx=_ctx())


def test_get_patient_overview_uses_replay_od() -> None:
    result = execute_tool("get_patient_overview", {}, ctx=_ctx())
    assert result["opendental_patient"]["FName"] == "Aardvark"
    assert result["latest_eligibility_check"]["coverage_percent"] == 80
    assert "raw_response" not in result["latest_eligibility_check"]


def test_get_insurance_and_benefits() -> None:
    result = execute_tool("get_insurance_and_benefits", {}, ctx=_ctx())
    assert len(result["plans"]) == 2
    assert result["plans"][0]["carrier"]["CarrierNum"] == 401
    assert result["plans"][0]["benefits"][0]["Percent"] == 80


def test_get_recent_procedures() -> None:
    result = execute_tool("get_recent_procedures", {}, ctx=_ctx())
    assert result["procedures"][0]["ProcCode"] == "D1110"


def test_get_claims_and_payments() -> None:
    result = execute_tool("get_claims_and_payments", {}, ctx=_ctx())
    assert result["claims"][0]["ClaimNum"] == 77
    assert result["claims"][0]["InsPayAmt"] == 96.0


def test_get_appointments() -> None:
    result = execute_tool("get_appointments", {}, ctx=_ctx())
    assert result["appointments"][0]["AptNum"] == 42
    assert result["appointments"][0]["AptStatus"] == "Scheduled"


def test_get_treatment_plan() -> None:
    result = execute_tool("get_treatment_plan", {}, ctx=_ctx())
    assert result["treatment_plan"][0]["ProcCode"] == "D2393"


def test_get_account_ledger() -> None:
    result = execute_tool("get_account_ledger", {}, ctx=_ctx())
    assert result["summary"]["BalTotal"] == 240.0
    assert result["payments"][0]["PayAmt"] == 50.0
    assert result["adjustments"][0]["AdjAmt"] == -24.0


def test_get_claim_procedures() -> None:
    result = execute_tool("get_claim_procedures", {}, ctx=_ctx())
    assert result["claim_procedures"][0]["Status"] == "Received"
    assert result["claim_procedures"][0]["InsPayAmt"] == 96.0


def test_get_recalls() -> None:
    result = execute_tool("get_recalls", {}, ctx=_ctx())
    assert result["recalls"][0]["RecallNum"] == 12


def test_get_commlogs() -> None:
    result = execute_tool("get_commlogs", {}, ctx=_ctx())
    assert result["commlogs"][0]["Note"] == "Confirmed recall appointment"


def test_get_documents() -> None:
    result = execute_tool("get_documents", {}, ctx=_ctx())
    assert result["documents"][0]["FileName"] == "bwx_2026-08-01.jpg"


def test_get_referrals() -> None:
    result = execute_tool("get_referrals", {}, ctx=_ctx())
    assert result["referrals"][0]["LName"] == "Endo"


def test_get_statements() -> None:
    result = execute_tool("get_statements", {}, ctx=_ctx())
    assert result["statements"][0]["StatementNum"] == 61


def test_get_health_history() -> None:
    result = execute_tool("get_health_history", {}, ctx=_ctx())
    assert result["medications"][0]["MedName"] == "Amoxicillin"
    assert result["allergies"][0]["Description"] == "Penicillin"
    assert result["problems"][0]["ProbStatus"] == "Active"


def test_get_perio_exams() -> None:
    result = execute_tool("get_perio_exams", {}, ctx=_ctx())
    assert result["perio_exams"][0]["PerioExamNum"] == 2


def test_get_clinical_notes() -> None:
    result = execute_tool("get_clinical_notes", {}, ctx=_ctx())
    assert result["clinical_notes"][0]["ProcNum"] == 11


def test_get_family_members() -> None:
    result = execute_tool("get_family_members", {}, ctx=_ctx())
    assert len(result["family_members"]) == 2
    assert result["family_members"][1]["FName"] == "Ava"


def test_get_eligibility_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.copilot.tools.list_procedure_estimates_for_check",
        lambda *args, **kwargs: [
            {"cdt_code": "D1110", "insurance_pays": 96, "patient_responsibility": 24}
        ],
    )
    result = execute_tool("get_eligibility_history", {}, ctx=_ctx())
    assert result["procedure_estimates"][0]["cdt_code"] == "D1110"
    assert "raw_response" not in result["latest_eligibility_check"]


def test_explain_carc_code() -> None:
    result = execute_tool("explain_carc_code", {"reason_code": "45"}, ctx=_ctx())
    assert result["found"] is True
    assert result["policy"]["bucket"] == "write_off"
    assert result["policy"]["bill_patient"] is False


def test_missing_od_pat_num_returns_error() -> None:
    ctx = _ctx()
    ctx.od_pat_num = None
    result = execute_tool("get_recent_procedures", {}, ctx=ctx)
    assert result["error"] == "opendental_unavailable"
