"""Tests for Open Dental benefit-grid provenance helpers."""

from __future__ import annotations

from app.integrations.opendental.benefit_provenance import (
    BENEFIT_GRID_MUTATION_EVENT,
    BenefitGridGuard,
    collect_agent_benefit_nums,
    insadjust_fingerprint,
    last_insadjust_fingerprint,
)
from app.integrations.opendental.models import ODBenefit
from app.integrations.opendental.writeback import run_opendental_benefits_grid_writeback


def test_collect_agent_benefit_nums_from_audit_rows() -> None:
    rows = [
        {
            "event_type": BENEFIT_GRID_MUTATION_EVENT,
            "detail": {
                "plan_num": 19,
                "mutations": [
                    {"benefit_num": 189, "provenance": "eligibility-agent-v1", "action": "created"},
                    {"benefit_num": 999, "provenance": "other", "action": "created"},
                ],
            },
        },
        {
            "event_type": BENEFIT_GRID_MUTATION_EVENT,
            "detail": {
                "plan_num": 20,
                "mutations": [{"benefit_num": 1, "provenance": "eligibility-agent-v1"}],
            },
        },
    ]
    assert collect_agent_benefit_nums(rows, 19) == {189}


def test_benefits_grid_skips_human_edited_row() -> None:
    existing = [
        ODBenefit(BenefitNum=192, PlanNum=19, CovCatNum=4, BenefitType="CoInsurance", Percent=50),
    ]

    class _Stub:
        def get_covcats(self):  # type: ignore[no-untyped-def]
            from tests.eligibility_agent.test_opendental_writeback import _COVCATS

            return _COVCATS

        def get_benefits(self, plan_num):  # type: ignore[no-untyped-def]
            return existing

        def create_benefit(self, payload):  # type: ignore[no-untyped-def]
            raise AssertionError("should not create when human row blocks update")

        def update_benefit(self, benefit_num, payload):  # type: ignore[no-untyped-def]
            raise AssertionError("should not update human-owned row")

    from tests.eligibility_agent.test_opendental_writeback import _CANONICAL, _UNIVERSAL_RECORD

    result = run_opendental_benefits_grid_writeback(
        _Stub(),  # type: ignore[arg-type]
        plan_num=19,
        canonical=_CANONICAL,
        universal_record=_UNIVERSAL_RECORD,
        respect_manual_edits=True,
        agent_benefit_nums=set(),
    )
    assert any(a.get("action") == "skipped_human_edit" for a in result["actions"])


def test_benefits_grid_updates_agent_owned_row() -> None:
    # Prior agent value within ±5 of proposed 80% so confidence gating allows auto-update.
    existing = [
        ODBenefit(BenefitNum=192, PlanNum=19, CovCatNum=4, BenefitType="CoInsurance", Percent=78),
    ]

    class _Stub:
        updated: list[tuple[int, dict]]

        def __init__(self) -> None:
            self.updated = []

        def get_covcats(self):  # type: ignore[no-untyped-def]
            from tests.eligibility_agent.test_opendental_writeback import _COVCATS

            return _COVCATS

        def get_benefits(self, plan_num):  # type: ignore[no-untyped-def]
            return existing

        def create_benefit(self, payload):  # type: ignore[no-untyped-def]
            raise AssertionError("unexpected create")

        def update_benefit(self, benefit_num, payload):  # type: ignore[no-untyped-def]
            self.updated.append((benefit_num, payload.model_dump(exclude_none=True)))
            return ODBenefit(BenefitNum=benefit_num, **payload.model_dump(exclude_none=True))

    from tests.eligibility_agent.test_opendental_writeback import _CANONICAL, _UNIVERSAL_RECORD

    stub = _Stub()
    result = run_opendental_benefits_grid_writeback(
        stub,  # type: ignore[arg-type]
        plan_num=19,
        canonical=_CANONICAL,
        universal_record=_UNIVERSAL_RECORD,
        respect_manual_edits=True,
        agent_benefit_nums={192},
    )
    assert ("BASIC/Restorative", "updated") in {
        (a.get("target"), a.get("action")) for a in result["actions"]
    }
    assert stub.updated == [(192, {"Percent": 80})]


def test_insadjust_fingerprint_idempotency_helpers() -> None:
    fp = insadjust_fingerprint(
        pat_plan_num=101,
        ins_used=144.0,
        deductible_used=50.0,
        on_date="2026-06-21",
    )
    rows = [
        {
            "event_type": "opendental_insadjust",
            "detail": {"pat_plan_num": 101, "fingerprint": fp},
        }
    ]
    assert last_insadjust_fingerprint(rows, 101) == fp
    assert last_insadjust_fingerprint(rows, 999) is None


def test_benefit_grid_guard_marks_created_rows() -> None:
    guard = BenefitGridGuard(agent_benefit_nums=set(), check_id="abc")
    guard.record({"action": "created", "benefit_num": 55}, benefit_num=55)
    assert guard.allow_update(55)
    assert not guard.allow_update(99)
