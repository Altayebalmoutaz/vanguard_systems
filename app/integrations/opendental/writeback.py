"""Format and push detailed eligibility results back to Open Dental.

Write-back order (each step independently flag-gated and fault-isolated):
1. InsSubs.BenefitNotes - primary structured, deterministic eligibility snapshot
1b. InsSubs.SubscNote - one-line summary, renders bold-red on the insurance grid
2. InsVerifies (PatientEnrollment + InsuranceBenefit) - audit timestamp + note
3. Commlog - human-readable summary for front-desk visibility
4. ClaimProcs InsAdjust - optional Phase 2 financial sync (used amounts)
5. Benefits grid (POST/PUT /benefits) - structured CoInsurance %, Deductible & Annual Max rows
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.integrations.opendental.benefit_provenance import (
    BENEFIT_GRID_MUTATION_EVENT,
    INSADJUST_MUTATION_EVENT,
    BenefitGridGuard,
    collect_agent_benefit_nums,
    insadjust_fingerprint,
    last_insadjust_fingerprint,
    benefit_row_fingerprint,
)
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.models import (
    ODBenefitCreate,
    ODBenefitUpdate,
    ODInsVerifyCreate,
    ODInsVerifyResponse,
)

logger = logging.getLogger(__name__)

# Maps the eligibility pipeline's coarse coverage buckets (universal_dental_record.categories
# "category" values) to OpenDental covcat.EbenefitCat keys. CovCatNum is resolved live from
# GET /covcats since the numbers differ per database.
_UNIVERSAL_TO_EBENEFIT_CATS: dict[str, tuple[str, ...]] = {
    "DIAGNOSTIC": ("Diagnostic", "DiagnosticXRay", "RoutinePreventive"),
    "PREVENTIVE": ("RoutinePreventive",),
    "BASIC": ("Restorative", "Endodontics", "Periodontics", "OralSurgery", "Adjunctive"),
    "MAJOR": ("Crowns", "Prosthodontics", "MaxillofacialProsth"),
    "ORTHO": ("Orthodontics",),
}

# Primary EbenefitCat per coarse category for frequency / waiting rows (first match wins).
_CATEGORY_PRIMARY_EBENEFIT: dict[str, str] = {
    "DIAGNOSTIC": "Diagnostic",
    "PREVENTIVE": "RoutinePreventive",
    "BASIC": "Restorative",
    "MAJOR": "Crowns",
    "ORTHO": "Orthodontics",
    "DENTAL": "General",
}

_MISSING_TOOTH_EXCLUSION_CATS: tuple[str, ...] = ("Prosthodontics", "MaxillofacialProsth")

# Open Dental note field length is not documented; keep notes concise but specific.
_MAX_NOTE_CHARS = 3500

SNAPSHOT_SOURCE = "ezfi"
VERIFIED_BY_EZFI = "Verified by ezfi"


def _breakdown_dict(canonical: dict[str, Any]) -> dict[str, Any]:
    breakdown = canonical.get("dental_benefit_breakdown")
    return breakdown if isinstance(breakdown, dict) else {}


def _frequency_limits_from_canonical(canonical: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in _breakdown_dict(canonical).get("frequency_limitations") or []:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if not desc:
            continue
        key = row.get("cdt_code") or row.get("category") or f"rule_{row.get('source_benefit_index')}"
        out[str(key)] = desc
    return out


def _waiting_periods_from_canonical(canonical: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in _breakdown_dict(canonical).get("waiting_periods") or []:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if not desc:
            continue
        key = row.get("cdt_code") or row.get("category") or f"wait_{row.get('source_benefit_index')}"
        out[str(key)] = desc
    return out


def _missing_tooth_from_canonical(canonical: dict[str, Any]) -> str | None:
    raw = _breakdown_dict(canonical).get("missing_tooth_clause")
    if not isinstance(raw, dict) or not raw.get("present"):
        return None
    desc = raw.get("description")
    return str(desc).strip() if desc else "Missing tooth clause applies"


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "n/a"


def _coinsurance_label(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        c = float(value)
        if 0 <= c <= 1:
            return f"{c * 100:.0f}%"
        return f"{c:.0f}%"
    except (TypeError, ValueError):
        return "n/a"


def _yes_no(value: Any) -> str:
    if value is None:
        return "unknown"
    return "yes" if bool(value) else "no"


def _truncate(note: str, *, limit: int = _MAX_NOTE_CHARS) -> str:
    note = note.strip()
    if len(note) <= limit:
        return note
    return note[: limit - 3].rstrip() + "..."


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CanonicalBenefitSnapshot:
    """Deterministic eligibility snapshot rendered into OD BenefitNotes / Commlog."""

    timestamp: datetime
    routing_status: str
    carrier_name: str | None = None
    plan_name: str | None = None
    deductible_total: float | None = None
    deductible_remaining: float | None = None
    annual_max_total: float | None = None
    annual_max_remaining: float | None = None
    coverage_percent_by_cdt: dict[str, float] = field(default_factory=dict)
    frequency_limits: dict[str, str] = field(default_factory=dict)
    waiting_periods: dict[str, str] = field(default_factory=dict)
    missing_tooth_clause: str | None = None
    copay: float | None = None
    patient_estimated_responsibility: float | None = None
    check_id: str | None = None
    source: str = SNAPSHOT_SOURCE


def build_benefit_snapshot(
    *,
    routing: dict[str, Any],
    canonical: dict[str, Any],
    procedure_estimates: list[dict[str, Any]],
    carrier_name: str | None = None,
    plan_name: str | None = None,
    check_id: str | None = None,
    now: datetime | None = None,
) -> CanonicalBenefitSnapshot:
    """Assemble a CanonicalBenefitSnapshot from the normalized eligibility result.

    Fields not reliably present in the normalized 271 (e.g. frequency limits) are left
    empty and rendered as ``n/a`` rather than fabricated.
    """
    coverage_by_cdt: dict[str, float] = {}
    plan_coverage = _to_float(canonical.get("coverage_percent"))
    total_patient = 0.0
    saw_patient = False
    for row in procedure_estimates:
        cdt = str(row.get("cdt_code") or "").strip().upper()
        if not cdt:
            continue
        pat_val = _to_float(row.get("patient_responsibility"))
        if pat_val is not None:
            total_patient += pat_val
            saw_patient = True
        ins = _to_float(row.get("insurance_pays"))
        allowed = _to_float(row.get("allowed_amount"))
        if ins is not None and allowed and allowed > 0:
            coverage_by_cdt[cdt] = round(ins / allowed * 100)
        elif plan_coverage is not None:
            coverage_by_cdt[cdt] = round(plan_coverage)

    return CanonicalBenefitSnapshot(
        timestamp=now or datetime.now(),
        routing_status=str(routing.get("status") or "UNKNOWN"),
        carrier_name=(carrier_name or None),
        plan_name=(plan_name or None),
        deductible_total=_to_float(canonical.get("deductible_total")),
        deductible_remaining=_to_float(canonical.get("deductible_remaining")),
        annual_max_total=_to_float(canonical.get("annual_max_total")),
        annual_max_remaining=_to_float(canonical.get("annual_max_remaining")),
        coverage_percent_by_cdt=coverage_by_cdt,
        frequency_limits=_frequency_limits_from_canonical(canonical),
        waiting_periods=_waiting_periods_from_canonical(canonical),
        missing_tooth_clause=_missing_tooth_from_canonical(canonical),
        copay=_to_float(canonical.get("copay")),
        patient_estimated_responsibility=(total_patient if saw_patient else None),
        check_id=check_id,
    )


def _plan_line(snapshot: CanonicalBenefitSnapshot) -> str:
    if snapshot.plan_name and snapshot.carrier_name:
        return f"{snapshot.plan_name} - {snapshot.carrier_name}"
    return snapshot.plan_name or snapshot.carrier_name or "n/a"


def format_benefit_notes(snapshot: CanonicalBenefitSnapshot) -> str:
    """Deterministic, ASCII-only, timestamped BenefitNotes block (no free-form narrative)."""
    lines: list[str] = []
    lines.append(f"[{VERIFIED_BY_EZFI}]")
    lines.append(f"Date: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Plan: {_plan_line(snapshot)}")
    lines.append(f"Status: {snapshot.routing_status}")
    if snapshot.check_id:
        lines.append(f"Check: {snapshot.check_id}")
    lines.append("")

    lines.append("Deductible:")
    lines.append(f" - Total: {_money(snapshot.deductible_total)}")
    lines.append(f" - Remaining: {_money(snapshot.deductible_remaining)}")
    lines.append("")

    lines.append("Annual Max:")
    lines.append(f" - Total: {_money(snapshot.annual_max_total)}")
    lines.append(f" - Remaining: {_money(snapshot.annual_max_remaining)}")
    lines.append("")

    lines.append("Coverage:")
    if snapshot.coverage_percent_by_cdt:
        for cdt in sorted(snapshot.coverage_percent_by_cdt):
            lines.append(f" - {cdt}: {_pct(snapshot.coverage_percent_by_cdt[cdt])}")
    else:
        lines.append(" - n/a")
    lines.append("")

    lines.append("Frequency:")
    if snapshot.frequency_limits:
        for label in sorted(snapshot.frequency_limits):
            lines.append(f" - {label}: {snapshot.frequency_limits[label]}")
    else:
        lines.append(" - n/a")
    lines.append("")

    lines.append("Waiting Periods:")
    if snapshot.waiting_periods:
        for label in sorted(snapshot.waiting_periods):
            lines.append(f" - {label}: {snapshot.waiting_periods[label]}")
    else:
        lines.append(" - n/a")
    lines.append("")

    lines.append("Missing Tooth Clause:")
    lines.append(f" - {snapshot.missing_tooth_clause or 'n/a'}")
    lines.append("")

    lines.append("Estimates:")
    if snapshot.patient_estimated_responsibility is not None:
        lines.append(
            f" - Patient estimated responsibility: {_money(snapshot.patient_estimated_responsibility)}"
        )
    else:
        lines.append(" - n/a")
    if snapshot.copay is not None:
        lines.append(f" - Copay: {_money(snapshot.copay)}")
    lines.append("")

    lines.append(VERIFIED_BY_EZFI)

    return _truncate("\n".join(lines)).encode("ascii", "replace").decode("ascii")


def build_subscriber_note(snapshot: CanonicalBenefitSnapshot, canonical: dict[str, Any]) -> str:
    """One-line eligibility summary for InsSub.SubscNote (bold-red on the insurance grid)."""
    parts = [f"Eligibility {snapshot.routing_status}"]
    parts.append(f"Active: {_yes_no(canonical.get('is_active'))}")
    if snapshot.coverage_percent_by_cdt:
        cov = ", ".join(
            f"{cdt} {_pct(snapshot.coverage_percent_by_cdt[cdt])}"
            for cdt in sorted(snapshot.coverage_percent_by_cdt)
        )
        parts.append(cov)
    if snapshot.patient_estimated_responsibility is not None:
        parts.append(f"est patient {_money(snapshot.patient_estimated_responsibility)}")
    summary = (
        f"[{VERIFIED_BY_EZFI}] "
        + " | ".join(parts)
        + f". Verified {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}."
    )
    return _truncate(summary).encode("ascii", "replace").decode("ascii")


def build_commlog_summary(snapshot: CanonicalBenefitSnapshot) -> str:
    """Concise one-glance summary for the front desk (ASCII only)."""
    parts = [
        f"Eligibility {snapshot.routing_status}",
        f"plan {_plan_line(snapshot)}",
        f"deductible remaining {_money(snapshot.deductible_remaining)}",
        f"annual max remaining {_money(snapshot.annual_max_remaining)}",
    ]
    if snapshot.patient_estimated_responsibility is not None:
        parts.append(f"est patient {_money(snapshot.patient_estimated_responsibility)}")
    summary = (
        f"[{VERIFIED_BY_EZFI}] "
        + "; ".join(parts)
        + f". Verified {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}."
    )
    return _truncate(summary).encode("ascii", "replace").decode("ascii")


def build_enrollment_note(
    *,
    check_id: str | None,
    routing: dict[str, Any],
    canonical: dict[str, Any],
    procedure_estimates: list[dict[str, Any]],
) -> str:
    """PatientEnrollment note: routing + member status + per-procedure estimates."""
    lines = [f"{VERIFIED_BY_EZFI} - eligibility verification"]
    if check_id:
        lines.append(f"Check: {check_id}")

    status = routing.get("status") or "UNKNOWN"
    lines.append(f"Routing: {status}")
    action = routing.get("action")
    if action:
        lines.append(f"Next action: {action}")
    suggested = routing.get("suggested_action")
    if suggested:
        lines.append(f"Suggested: {suggested}")

    lines.append(
        f"Active: {_yes_no(canonical.get('is_active'))} | "
        f"Covered: {_yes_no(canonical.get('is_covered'))} | "
        f"Payer: {canonical.get('payer_id') or 'n/a'}"
    )

    if procedure_estimates:
        lines.append("Procedure estimates:")
        total = 0.0
        for row in procedure_estimates:
            cdt = row.get("cdt_code") or "?"
            covered = _yes_no(row.get("procedure_covered"))
            pat_val = row.get("patient_responsibility")
            with contextlib.suppress(TypeError, ValueError):
                total += float(pat_val or 0)
            pat = _money(pat_val)
            ins = _money(row.get("insurance_pays"))
            allowed = _money(row.get("allowed_amount"))
            lines.append(f"  {cdt}: covered={covered}, patient {pat}, ins {ins}, allowed {allowed}")
        lines.append(f"Est. patient responsibility (total): {_money(total)}")
    else:
        lines.append("Procedure estimates: none (no CDT codes or estimates skipped)")

    return _truncate("\n".join(lines))


def build_benefits_note(
    *,
    check_id: str | None,
    routing: dict[str, Any],
    canonical: dict[str, Any],
    procedure_estimates: list[dict[str, Any]],
) -> str:
    """InsuranceBenefit note: financial snapshot from normalized 271."""
    lines = [f"{VERIFIED_BY_EZFI} - benefits snapshot"]
    if check_id:
        lines.append(f"Check: {check_id}")

    lines.append(f"Routing: {routing.get('status') or 'UNKNOWN'}")
    lines.append(
        f"Active: {_yes_no(canonical.get('is_active'))} | "
        f"Response complete: {_yes_no(canonical.get('response_complete'))}"
    )
    lines.append(
        f"Coverage: {_pct(canonical.get('coverage_percent'))} | "
        f"Coinsurance: {_coinsurance_label(canonical.get('coinsurance'))}"
    )
    lines.append(
        f"Deductible remaining: {_money(canonical.get('deductible_remaining'))} | "
        f"Annual max remaining: {_money(canonical.get('annual_max_remaining'))}"
    )
    lines.append(
        f"Annual max total: {_money(canonical.get('annual_max_total'))} | "
        f"Copay: {_money(canonical.get('copay'))}"
    )

    if canonical.get("inactive_reason"):
        lines.append(f"Inactive reason: {canonical['inactive_reason']}")

    missing = canonical.get("missing_fields") or []
    if missing:
        lines.append(f"Missing fields: {', '.join(str(m) for m in missing[:8])}")

    if procedure_estimates:
        lines.append("Per procedure:")
        for row in procedure_estimates:
            cdt = row.get("cdt_code") or "?"
            lines.append(
                f"  {cdt}: patient {_money(row.get('patient_responsibility'))}, "
                f"ins {_money(row.get('insurance_pays'))}"
            )

    return _truncate("\n".join(lines))


def _derive_used(total: Any, remaining: Any) -> float | None:
    """Used = total - remaining, when both are known and non-negative."""
    t = _to_float(total)
    r = _to_float(remaining)
    if t is None or r is None:
        return None
    used = t - r
    return used if used >= 0 else None


def _percent_int(value: Any) -> int | None:
    """Clamp a patient/insurance percentage to an int in [0, 100]."""
    f = _to_float(value)
    if f is None:
        return None
    return max(0, min(100, round(f)))


def _normalize_quantity_qualifier(value: Any) -> str:
    raw = str(value or "").strip()
    low = raw.lower()
    if not raw or "visit" in low or "service" in low:
        return "NumberOfServices"
    if "month" in low:
        return "Months"
    if "year" in low or "calendar" in low:
        return "Years"
    if "day" in low:
        return "Days"
    return raw


def _covcat_for_category(
    category: Any, ebenefit_to_covcat: dict[str, int]
) -> int | None:
    key = _CATEGORY_PRIMARY_EBENEFIT.get(str(category or "").upper())
    if not key:
        return None
    return ebenefit_to_covcat.get(key)


def _build_frequency_grid_targets(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in breakdown.get("frequency_limitations") or []:
        if not isinstance(row, dict):
            continue
        qty = row.get("quantity")
        if qty is None:
            continue
        try:
            quantity = int(qty)
        except (TypeError, ValueError):
            continue
        category = row.get("category")
        qualifier = _normalize_quantity_qualifier(row.get("quantity_qualifier"))
        period_months = row.get("period_months")
        time_period = "CalendarYear"
        if period_months is not None:
            try:
                months = int(period_months)
                if months >= 12 and months % 12 == 0:
                    time_period = "CalendarYear"
                else:
                    time_period = "Months"
            except (TypeError, ValueError):
                pass
        key = (category, quantity, qualifier, time_period)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "category": category,
                "cdt_code": row.get("cdt_code"),
                "quantity": quantity,
                "quantity_qualifier": qualifier,
                "time_period": time_period,
                "label": str(row.get("description") or "frequency").strip() or "frequency",
            }
        )
    return rows


def _build_waiting_grid_targets(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in breakdown.get("waiting_periods") or []:
        if not isinstance(row, dict):
            continue
        months = row.get("months")
        if months is None:
            continue
        try:
            month_count = int(months)
        except (TypeError, ValueError):
            continue
        category = row.get("category")
        key = (category, month_count)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "category": category,
                "cdt_code": row.get("cdt_code"),
                "months": month_count,
                "label": str(row.get("description") or "waiting_period").strip() or "waiting_period",
            }
        )
    return rows


def _build_exclusion_grid_targets(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    raw = breakdown.get("missing_tooth_clause")
    if not isinstance(raw, dict) or not raw.get("present"):
        return []
    return [
        {
            "ebenefit_cats": _MISSING_TOOTH_EXCLUSION_CATS,
            "label": "missing_tooth_clause",
            "description": raw.get("description"),
        }
    ]


def build_benefit_grid_targets(
    *,
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Translate normalized eligibility into structured OD benefit-grid targets.

    Returns a dict with:
      - coverage: list of {ebenefit_cats, percent, label} CoInsurance targets
      - annual_max: float | None  (overall plan annual maximum the insurer pays)
      - deductible: float | None  (overall General deductible total)
      - frequency_limitations: quantity-based Limitations rows per category
      - waiting_periods: WaitingPeriod rows (months) per category
      - exclusions: Exclusions rows (e.g. missing tooth clause on prosth categories)
    Values absent from the 271 are left as None and simply not written.
    """
    coverage: list[dict[str, Any]] = []
    record = universal_record or {}
    breakdown = _breakdown_dict(canonical)
    for cat in record.get("categories") or []:
        name = str(cat.get("category") or "").upper()
        ebenefit_cats = _UNIVERSAL_TO_EBENEFIT_CATS.get(name)
        if not ebenefit_cats:
            continue
        patient_pct = _percent_int((cat.get("coinsurance_patient_pct") or {}).get("value"))
        if patient_pct is None:
            continue
        coverage.append(
            {"ebenefit_cats": ebenefit_cats, "percent": 100 - patient_pct, "label": name}
        )

    return {
        "coverage": coverage,
        "annual_max": _to_float(canonical.get("annual_max_total")),
        "deductible": _to_float(canonical.get("deductible_total")),
        "frequency_limitations": _build_frequency_grid_targets(breakdown),
        "waiting_periods": _build_waiting_grid_targets(breakdown),
        "exclusions": _build_exclusion_grid_targets(breakdown),
    }


def run_opendental_benefits_grid_writeback(
    client: OpenDentalClient,
    *,
    plan_num: int,
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
    respect_manual_edits: bool = True,
    agent_benefit_nums: set[int] | None = None,
    check_id: str | None = None,
) -> dict[str, Any]:
    """Upsert structured benefit-grid rows (CoInsurance %, Deductible, Annual Max, frequency Limitations, WaitingPeriod, Exclusions).

    Idempotent: existing rows (matched by BenefitType + CovCatNum) are PUT-updated only when
    the value changed; missing rows are POST-created. Each row is fault-isolated so one failure
    never aborts the rest. Mutates plan-level benefits shared by all subscribers on the plan,
    which mirrors OpenDental's own "Import Benefits" behavior from a 271.
    """
    try:
        covcats = client.get_covcats()
        existing = client.get_benefits(plan_num)
    except Exception as exc:
        logger.warning("OpenDental benefits-grid fetch failed: %s", exc)
        return {"error": f"fetch_failed: {exc}"}

    ebenefit_to_covcat: dict[str, int] = {}
    for c in covcats:
        if c.EbenefitCat and c.CovCatNum is not None:
            ebenefit_to_covcat.setdefault(c.EbenefitCat, c.CovCatNum)
    general_num = ebenefit_to_covcat.get("General")

    def _find(benefit_type: str, cov_cat_num: int | None) -> Any:
        for b in existing:
            if (b.BenefitType or "") == benefit_type and (b.CovCatNum or 0) == (cov_cat_num or 0):
                return b
        return None

    def _find_limitations_monetary(cov_cat_num: int) -> Any:
        for b in existing:
            if (b.BenefitType or "") != "Limitations" or (b.CovCatNum or 0) != cov_cat_num:
                continue
            if b.MonetaryAmt is not None:
                return b
        return None

    def _find_limitations_quantity(
        cov_cat_num: int, quantity: int, quantity_qualifier: str, time_period: str
    ) -> Any:
        for b in existing:
            if (b.BenefitType or "") != "Limitations" or (b.CovCatNum or 0) != cov_cat_num:
                continue
            if b.MonetaryAmt is not None:
                continue
            if (
                (b.Quantity or 0) == quantity
                and (b.QuantityQualifier or "") == quantity_qualifier
                and (b.TimePeriod or "") == time_period
            ):
                return b
        return None

    targets = build_benefit_grid_targets(canonical=canonical, universal_record=universal_record)
    actions: list[dict[str, Any]] = []
    guard = BenefitGridGuard(
        respect_manual_edits=respect_manual_edits,
        agent_benefit_nums=set(agent_benefit_nums or ()),
        check_id=check_id,
    )

    def _skip_human_edit(existing_row: Any, *, target: str, benefit_type: str) -> bool:
        if guard.allow_update(getattr(existing_row, "BenefitNum", None)):
            return False
        actions.append(
            guard.record(
                {
                    "target": target,
                    "type": benefit_type,
                    "cov_cat_num": existing_row.CovCatNum,
                    "benefit_num": existing_row.BenefitNum,
                    "action": "skipped_human_edit",
                    "previous": benefit_row_fingerprint(existing_row),
                },
                benefit_num=existing_row.BenefitNum,
            )
        )
        return True

    def _upsert_coinsurance(cov_cat_num: int, percent: int, label: str) -> None:
        existing_row = _find("CoInsurance", cov_cat_num)
        try:
            if existing_row is None:
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType="CoInsurance",
                        CoverageLevel="None",
                        CovCatNum=cov_cat_num,
                        Percent=percent,
                        TimePeriod="CalendarYear",
                    )
                )
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": "CoInsurance",
                            "cov_cat_num": cov_cat_num,
                            "percent": percent,
                            "action": "created",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            elif (existing_row.Percent or -1) != percent:
                if _skip_human_edit(existing_row, target=label, benefit_type="CoInsurance"):
                    return
                client.update_benefit(existing_row.BenefitNum, ODBenefitUpdate(Percent=percent))
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": "CoInsurance",
                            "cov_cat_num": cov_cat_num,
                            "percent": percent,
                            "action": "updated",
                            "benefit_num": existing_row.BenefitNum,
                            "previous_percent": existing_row.Percent,
                        },
                        benefit_num=existing_row.BenefitNum,
                    )
                )
            else:
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": "CoInsurance",
                            "cov_cat_num": cov_cat_num,
                            "percent": percent,
                            "action": "unchanged",
                            "benefit_num": existing_row.BenefitNum,
                        },
                        benefit_num=existing_row.BenefitNum,
                    )
                )
        except Exception as exc:
            logger.warning("OpenDental CoInsurance upsert failed (cat %s): %s", cov_cat_num, exc)
            actions.append(
                {
                    "target": label,
                    "type": "CoInsurance",
                    "cov_cat_num": cov_cat_num,
                    "error": str(exc),
                }
            )

    def _upsert_monetary(benefit_type: str, amount: float, label: str) -> None:
        if general_num is None:
            actions.append(
                {"target": label, "type": benefit_type, "action": "skipped_no_general_covcat"}
            )
            return
        existing_row = _find_limitations_monetary(general_num) if benefit_type == "Limitations" else _find(benefit_type, general_num)
        try:
            if existing_row is None:
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType=benefit_type,
                        CoverageLevel="Individual",
                        CovCatNum=general_num,
                        MonetaryAmt=amount,
                        TimePeriod="CalendarYear",
                    )
                )
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": benefit_type,
                            "cov_cat_num": general_num,
                            "amount": amount,
                            "action": "created",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            elif (
                existing_row.MonetaryAmt if existing_row.MonetaryAmt is not None else -1.0
            ) != amount:
                if _skip_human_edit(existing_row, target=label, benefit_type=benefit_type):
                    return
                client.update_benefit(existing_row.BenefitNum, ODBenefitUpdate(MonetaryAmt=amount))
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": benefit_type,
                            "cov_cat_num": general_num,
                            "amount": amount,
                            "action": "updated",
                            "benefit_num": existing_row.BenefitNum,
                            "previous_amount": existing_row.MonetaryAmt,
                        },
                        benefit_num=existing_row.BenefitNum,
                    )
                )
            else:
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": benefit_type,
                            "cov_cat_num": general_num,
                            "amount": amount,
                            "action": "unchanged",
                            "benefit_num": existing_row.BenefitNum,
                        },
                        benefit_num=existing_row.BenefitNum,
                    )
                )
        except Exception as exc:
            logger.warning("OpenDental %s upsert failed: %s", benefit_type, exc)
            actions.append({"target": label, "type": benefit_type, "error": str(exc)})

    def _upsert_frequency_limitation(
        cov_cat_num: int,
        *,
        quantity: int,
        quantity_qualifier: str,
        time_period: str,
        label: str,
    ) -> None:
        existing_row = _find_limitations_quantity(
            cov_cat_num, quantity, quantity_qualifier, time_period
        )
        try:
            if existing_row is None:
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType="Limitations",
                        CoverageLevel="None",
                        CovCatNum=cov_cat_num,
                        Quantity=quantity,
                        QuantityQualifier=quantity_qualifier,
                        TimePeriod=time_period,
                    )
                )
                actions.append(
                    {
                        "target": label,
                        "type": "Limitations",
                        "cov_cat_num": cov_cat_num,
                        "quantity": quantity,
                        "quantity_qualifier": quantity_qualifier,
                        "action": "created",
                        "benefit_num": created.BenefitNum,
                    }
                )
            else:
                actions.append(
                    {
                        "target": label,
                        "type": "Limitations",
                        "cov_cat_num": cov_cat_num,
                        "quantity": quantity,
                        "quantity_qualifier": quantity_qualifier,
                        "action": "unchanged",
                        "benefit_num": existing_row.BenefitNum,
                    }
                )
        except Exception as exc:
            logger.warning("OpenDental frequency Limitations upsert failed (cat %s): %s", cov_cat_num, exc)
            actions.append(
                {
                    "target": label,
                    "type": "Limitations",
                    "cov_cat_num": cov_cat_num,
                    "error": str(exc),
                }
            )

    def _upsert_waiting_period(cov_cat_num: int, *, months: int, label: str) -> None:
        existing_row = _find("WaitingPeriod", cov_cat_num)
        try:
            if existing_row is None:
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType="WaitingPeriod",
                        CoverageLevel="None",
                        CovCatNum=cov_cat_num,
                        Quantity=months,
                        TimePeriod="Months",
                    )
                )
                actions.append(
                    {
                        "target": label,
                        "type": "WaitingPeriod",
                        "cov_cat_num": cov_cat_num,
                        "months": months,
                        "action": "created",
                        "benefit_num": created.BenefitNum,
                    }
                )
            elif (existing_row.Quantity or -1) != months:
                if _skip_human_edit(existing_row, target=label, benefit_type="WaitingPeriod"):
                    return
                client.update_benefit(
                    existing_row.BenefitNum,
                    ODBenefitUpdate(Quantity=months, TimePeriod="Months"),
                )
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": "WaitingPeriod",
                            "cov_cat_num": cov_cat_num,
                            "months": months,
                            "action": "updated",
                            "benefit_num": existing_row.BenefitNum,
                            "previous_months": existing_row.Quantity,
                        },
                        benefit_num=existing_row.BenefitNum,
                    )
                )
            else:
                actions.append(
                    {
                        "target": label,
                        "type": "WaitingPeriod",
                        "cov_cat_num": cov_cat_num,
                        "months": months,
                        "action": "unchanged",
                        "benefit_num": existing_row.BenefitNum,
                    }
                )
        except Exception as exc:
            logger.warning("OpenDental WaitingPeriod upsert failed (cat %s): %s", cov_cat_num, exc)
            actions.append(
                {
                    "target": label,
                    "type": "WaitingPeriod",
                    "cov_cat_num": cov_cat_num,
                    "error": str(exc),
                }
            )

    def _upsert_exclusion(cov_cat_num: int, *, label: str) -> None:
        existing_row = _find("Exclusions", cov_cat_num)
        try:
            if existing_row is None:
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType="Exclusions",
                        CoverageLevel="None",
                        CovCatNum=cov_cat_num,
                    )
                )
                actions.append(
                    {
                        "target": label,
                        "type": "Exclusions",
                        "cov_cat_num": cov_cat_num,
                        "action": "created",
                        "benefit_num": created.BenefitNum,
                    }
                )
            else:
                actions.append(
                    {
                        "target": label,
                        "type": "Exclusions",
                        "cov_cat_num": cov_cat_num,
                        "action": "unchanged",
                        "benefit_num": existing_row.BenefitNum,
                    }
                )
        except Exception as exc:
            logger.warning("OpenDental Exclusions upsert failed (cat %s): %s", cov_cat_num, exc)
            actions.append(
                {
                    "target": label,
                    "type": "Exclusions",
                    "cov_cat_num": cov_cat_num,
                    "error": str(exc),
                }
            )

    # Coverage percentages per resolved category (dedupe so each CovCatNum is written once).
    seen_cov_cats: set[int] = set()
    for target in targets["coverage"]:
        for ebenefit in target["ebenefit_cats"]:
            cov_cat_num = ebenefit_to_covcat.get(ebenefit)
            if cov_cat_num is None or cov_cat_num in seen_cov_cats:
                continue
            seen_cov_cats.add(cov_cat_num)
            _upsert_coinsurance(cov_cat_num, target["percent"], f"{target['label']}/{ebenefit}")

    if targets["annual_max"] is not None:
        _upsert_monetary("Limitations", targets["annual_max"], "annual_max")
    if targets["deductible"] is not None:
        _upsert_monetary("Deductible", targets["deductible"], "general_deductible")

    for target in targets.get("frequency_limitations") or []:
        cov_cat_num = _covcat_for_category(target.get("category"), ebenefit_to_covcat)
        if cov_cat_num is None:
            actions.append(
                {
                    "target": target.get("label"),
                    "type": "Limitations",
                    "action": "skipped_unresolved_covcat",
                }
            )
            continue
        _upsert_frequency_limitation(
            cov_cat_num,
            quantity=int(target["quantity"]),
            quantity_qualifier=str(target["quantity_qualifier"]),
            time_period=str(target["time_period"]),
            label=str(target.get("label") or "frequency"),
        )

    for target in targets.get("waiting_periods") or []:
        cov_cat_num = _covcat_for_category(target.get("category"), ebenefit_to_covcat)
        if cov_cat_num is None:
            actions.append(
                {
                    "target": target.get("label"),
                    "type": "WaitingPeriod",
                    "action": "skipped_unresolved_covcat",
                }
            )
            continue
        _upsert_waiting_period(
            cov_cat_num,
            months=int(target["months"]),
            label=str(target.get("label") or "waiting_period"),
        )

    for target in targets.get("exclusions") or []:
        for ebenefit in target.get("ebenefit_cats") or ():
            cov_cat_num = ebenefit_to_covcat.get(str(ebenefit))
            if cov_cat_num is None:
                actions.append(
                    {
                        "target": target.get("label"),
                        "type": "Exclusions",
                        "ebenefit_cat": ebenefit,
                        "action": "skipped_unresolved_covcat",
                    }
                )
                continue
            _upsert_exclusion(cov_cat_num, label=f"{target.get('label')}/{ebenefit}")

    return {
        "plan_num": plan_num,
        "general_cov_cat_num": general_num,
        "actions": actions,
        "mutations": guard.mutations,
        "agent_benefit_nums": sorted(guard.agent_benefit_nums),
    }


def _load_patient_audit_rows(patient_id: Any) -> list[dict[str, Any]]:
    if not patient_id:
        return []
    try:
        from uuid import UUID

        from app.eligibility.config import get_settings
        from app.eligibility.db import get_supabase, list_audit_for_patient

        supabase = get_supabase(get_settings())
        return list_audit_for_patient(supabase, UUID(str(patient_id)))
    except Exception as exc:
        logger.warning("OpenDental audit load failed: %s", exc)
        return []


def _persist_audit_event(patient_id: Any, event_type: str, detail: dict[str, Any]) -> None:
    if not patient_id:
        return
    try:
        from app.eligibility.audit import write_audit_event

        write_audit_event(patient_id=patient_id, event_type=event_type, detail=detail)
    except Exception as exc:
        logger.warning("OpenDental audit persist failed: %s", exc)


def run_opendental_writeback(
    client: OpenDentalClient,
    *,
    pat_num: int,
    primary_pat_plan_num: int,
    primary_plan_num: int,
    primary_ins_sub_num: int,
    primary_result: dict[str, Any],
    carrier_name: str | None = None,
    plan_name: str | None = None,
    write_benefit_notes: bool = True,
    write_subscriber_note: bool = True,
    write_commlog: bool = True,
    write_insadjust: bool = False,
    write_benefits_grid: bool = False,
    respect_manual_edits: bool = True,
    verified_on: date | None = None,
    check_id: str | None = None,
    patient_id: Any = None,
) -> dict[str, Any]:
    """
    Write eligibility results to Open Dental in order, isolating each step so a single
    failure never aborts the rest:
      1. InsSubs.BenefitNotes (primary structured snapshot)
      2. InsVerifies PatientEnrollment + InsuranceBenefit (audit trail)
      3. Commlog (front-desk visibility)
      4. ClaimProcs InsAdjust (Phase 2 financial sync, opt-in)
    """
    verified = verified_on or date.today()
    routing = primary_result.get("routing") or {}
    canonical = primary_result.get("canonical") or {}
    proc_estimates = primary_result.get("procedure_estimates") or []
    raw_check = check_id if check_id is not None else primary_result.get("check_id")
    check_id_str = str(raw_check) if raw_check else None

    audit_rows = _load_patient_audit_rows(patient_id)
    agent_benefit_nums = collect_agent_benefit_nums(audit_rows, primary_plan_num)

    snapshot = build_benefit_snapshot(
        routing=routing,
        canonical=canonical,
        procedure_estimates=proc_estimates,
        carrier_name=carrier_name,
        plan_name=plan_name,
        check_id=check_id_str,
    )

    result: dict[str, Any] = {
        "benefit_notes": None,
        "subscriber_note": None,
        "insverifies": None,
        "commlog": None,
        "insadjust": None,
        "benefits_grid": None,
    }

    # 1) PRIMARY: InsSubs.BenefitNotes -------------------------------------------------
    benefit_notes_text = format_benefit_notes(snapshot)
    if write_benefit_notes:
        try:
            resp = client.update_inssub_benefit_notes(
                primary_ins_sub_num, primary_plan_num, benefit_notes_text
            )
            result["benefit_notes"] = {
                "ins_sub_num": primary_ins_sub_num,
                "plan_num": primary_plan_num,
                "note_sent": benefit_notes_text,
                "response": resp,
            }
        except Exception as exc:
            logger.warning("OpenDental BenefitNotes write failed: %s", exc)
            result["benefit_notes"] = {"error": str(exc), "note_sent": benefit_notes_text}

    # 1b) GRID-VISIBLE: InsSubs.SubscNote (bold-red on the insurance grid) -------------
    if write_subscriber_note:
        subscriber_note_text = build_subscriber_note(snapshot, canonical)
        try:
            resp = client.update_inssub_subscriber_note(
                primary_ins_sub_num, primary_plan_num, subscriber_note_text
            )
            result["subscriber_note"] = {
                "ins_sub_num": primary_ins_sub_num,
                "plan_num": primary_plan_num,
                "note_sent": subscriber_note_text,
                "response": resp,
            }
        except Exception as exc:
            logger.warning("OpenDental SubscNote write failed: %s", exc)
            result["subscriber_note"] = {"error": str(exc), "note_sent": subscriber_note_text}

    # 2) AUDIT TRAIL: InsVerifies ------------------------------------------------------
    enrollment_note = build_enrollment_note(
        check_id=check_id_str,
        routing=routing,
        canonical=canonical,
        procedure_estimates=proc_estimates,
    )
    benefits_note = build_benefits_note(
        check_id=check_id_str,
        routing=routing,
        canonical=canonical,
        procedure_estimates=proc_estimates,
    )
    try:
        enrollment = client.create_insverify(
            ODInsVerifyCreate(
                DateLastVerified=verified,
                VerifyType="PatientEnrollment",
                FKey=primary_pat_plan_num,
                Note=enrollment_note,
            )
        )
        benefits = client.create_insverify(
            ODInsVerifyCreate(
                DateLastVerified=verified,
                VerifyType="InsuranceBenefit",
                FKey=primary_plan_num,
                Note=benefits_note,
            )
        )
        enrollment_payload = _insverify_payload(enrollment, note_sent=enrollment_note)
        benefit_payload = _insverify_payload(benefits, note_sent=benefits_note)
        result["insverifies"] = {
            "patient_enrollment": enrollment_payload,
            "insurance_benefit": benefit_payload,
        }
        # Back-compat for callers expecting top-level keys / write_back_result.InsVerifyNum
        result["patient_enrollment"] = enrollment_payload
        result["insurance_benefit"] = benefit_payload
        result["write_back_result"] = enrollment_payload
    except Exception as exc:
        logger.warning("OpenDental InsVerifies write failed: %s", exc)
        result["insverifies"] = {"error": str(exc)}

    # 3) USER VISIBILITY: Commlog ------------------------------------------------------
    if write_commlog:
        commlog_note = build_commlog_summary(snapshot)
        try:
            resp = client.create_commlog(pat_num, commlog_note)
            result["commlog"] = {
                "pat_num": pat_num,
                "note_sent": commlog_note,
                "response": resp.model_dump(mode="json") if hasattr(resp, "model_dump") else resp,
            }
        except Exception as exc:
            logger.warning("OpenDental Commlog write failed: %s", exc)
            result["commlog"] = {"error": str(exc), "note_sent": commlog_note}

    # 4) PHASE 2: ClaimProcs InsAdjust -------------------------------------------------
    if write_insadjust:
        ins_used = _derive_used(
            canonical.get("annual_max_total"), canonical.get("annual_max_remaining")
        )
        ded_used = _derive_used(
            canonical.get("deductible_total"), canonical.get("deductible_remaining")
        )
        if ins_used is None and ded_used is None:
            result["insadjust"] = {"skipped": "insufficient_data"}
        else:
            fp = insadjust_fingerprint(
                pat_plan_num=primary_pat_plan_num,
                ins_used=ins_used,
                deductible_used=ded_used,
                on_date=verified.isoformat(),
            )
            prior_fp = last_insadjust_fingerprint(audit_rows, primary_pat_plan_num)
            if prior_fp == fp:
                result["insadjust"] = {
                    "pat_plan_num": primary_pat_plan_num,
                    "skipped": "unchanged",
                    "mode": "set",
                    "fingerprint": fp,
                }
            else:
                try:
                    resp = client.put_claimproc_insadjust(
                        primary_pat_plan_num,
                        ins_used=ins_used,
                        deductible_used=ded_used,
                        on_date=verified,
                    )
                    result["insadjust"] = {
                        "pat_plan_num": primary_pat_plan_num,
                        "ins_used": ins_used,
                        "deductible_used": ded_used,
                        "mode": "set",
                        "fingerprint": fp,
                        "response": resp,
                    }
                    _persist_audit_event(
                        patient_id,
                        INSADJUST_MUTATION_EVENT,
                        {
                            "pat_plan_num": primary_pat_plan_num,
                            "plan_num": primary_plan_num,
                            "check_id": check_id_str,
                            "fingerprint": fp,
                        },
                    )
                except Exception as exc:
                    logger.warning("OpenDental InsAdjust write failed: %s", exc)
                    result["insadjust"] = {"error": str(exc)}

    # 5) STRUCTURED GRID: Benefits (CoInsurance %, Deductible, Annual Max) -------------
    if write_benefits_grid:
        try:
            grid_result = run_opendental_benefits_grid_writeback(
                client,
                plan_num=primary_plan_num,
                canonical=canonical,
                universal_record=primary_result.get("universal_dental_record"),
                respect_manual_edits=respect_manual_edits,
                agent_benefit_nums=agent_benefit_nums,
                check_id=check_id_str,
            )
            result["benefits_grid"] = grid_result
            if grid_result.get("mutations"):
                _persist_audit_event(
                    patient_id,
                    BENEFIT_GRID_MUTATION_EVENT,
                    {
                        "plan_num": primary_plan_num,
                        "pat_num": pat_num,
                        "check_id": check_id_str,
                        "mutations": grid_result.get("mutations") or [],
                        "agent_benefit_nums": grid_result.get("agent_benefit_nums") or [],
                    },
                )
        except Exception as exc:
            logger.warning("OpenDental benefits-grid write failed: %s", exc)
            result["benefits_grid"] = {"error": str(exc)}

    return result


_WRITEBACK_STEP_KEYS = (
    "benefit_notes",
    "subscriber_note",
    "insverifies",
    "commlog",
    "insadjust",
    "benefits_grid",
)


def writeback_has_failures(result: dict[str, Any]) -> bool:
    """Return True when any isolated write-back step recorded an error."""
    for key in _WRITEBACK_STEP_KEYS:
        step = result.get(key)
        if isinstance(step, dict) and step.get("error"):
            return True
    return False


def _insverify_payload(resp: ODInsVerifyResponse, *, note_sent: str) -> dict[str, Any]:
    out = resp.model_dump(mode="json")
    out["note_sent"] = note_sent
    return out
