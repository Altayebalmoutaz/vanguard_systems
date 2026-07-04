"""Provenance and audit helpers for Open Dental benefit-grid write-back."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations.opendental.models import ODBenefit

PROVENANCE_AGENT = "eligibility-agent-v1"

BENEFIT_GRID_MUTATION_EVENT = "opendental_benefit_grid_mutation"
INSADJUST_MUTATION_EVENT = "opendental_insadjust"


def benefit_row_fingerprint(row: ODBenefit) -> dict[str, Any]:
    return {
        "benefit_num": row.BenefitNum,
        "benefit_type": row.BenefitType,
        "cov_cat_num": row.CovCatNum,
        "percent": row.Percent,
        "monetary_amt": row.MonetaryAmt,
        "quantity": row.Quantity,
        "quantity_qualifier": row.QuantityQualifier,
        "time_period": row.TimePeriod,
    }


def collect_agent_benefit_nums(audit_rows: list[dict[str, Any]], plan_num: int) -> set[int]:
    """Rebuild agent-owned BenefitNum set from prior eligibility audit rows."""
    owned: set[int] = set()
    for row in audit_rows:
        if row.get("event_type") != BENEFIT_GRID_MUTATION_EVENT:
            continue
        detail = row.get("detail") or {}
        if int(detail.get("plan_num") or 0) != int(plan_num):
            continue
        for mut in detail.get("mutations") or []:
            if mut.get("provenance") != PROVENANCE_AGENT:
                continue
            bn = mut.get("benefit_num")
            if bn is not None:
                owned.add(int(bn))
    return owned


def insadjust_fingerprint(
    *,
    pat_plan_num: int,
    ins_used: float | None,
    deductible_used: float | None,
    on_date: str,
) -> dict[str, Any]:
    return {
        "pat_plan_num": pat_plan_num,
        "ins_used": ins_used,
        "deductible_used": deductible_used,
        "date": on_date,
        "mode": "set",
    }


def last_insadjust_fingerprint(audit_rows: list[dict[str, Any]], pat_plan_num: int) -> dict[str, Any] | None:
    for row in audit_rows:
        if row.get("event_type") != INSADJUST_MUTATION_EVENT:
            continue
        detail = row.get("detail") or {}
        if int(detail.get("pat_plan_num") or 0) != int(pat_plan_num):
            continue
        return detail.get("fingerprint")
    return None


@dataclass
class BenefitGridGuard:
    """Skip updates to benefit rows not previously written by this agent."""

    respect_manual_edits: bool = True
    agent_benefit_nums: set[int] = field(default_factory=set)
    check_id: str | None = None
    mutations: list[dict[str, Any]] = field(default_factory=list)

    def allow_update(self, benefit_num: int | None) -> bool:
        if not self.respect_manual_edits or benefit_num is None:
            return True
        return int(benefit_num) in self.agent_benefit_nums

    def record(
        self,
        action: dict[str, Any],
        *,
        benefit_num: int | None,
    ) -> dict[str, Any]:
        if benefit_num is not None and action.get("action") in ("created", "updated"):
            self.agent_benefit_nums.add(int(benefit_num))
        tagged = {
            **action,
            "provenance": PROVENANCE_AGENT,
            "check_id": self.check_id,
        }
        self.mutations.append(tagged)
        return tagged
