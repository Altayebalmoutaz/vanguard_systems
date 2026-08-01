"""Format and push detailed eligibility results back to Open Dental.

Write-back order (each step independently flag-gated and fault-isolated):
1. InsSubs.BenefitNotes - primary structured, deterministic eligibility snapshot
1b. InsSubs.SubscNote - one-line summary, renders bold-red on the insurance grid
2. InsVerifies (PatientEnrollment + InsuranceBenefit) - audit timestamp + note
3. Commlog - human-readable summary for front-desk visibility
4. ProcedureLogs InsuranceHistory (InsHist) - last service dates for frequency
5. ClaimProcs InsAdjust - optional Phase 2 financial sync (used amounts)
6. Benefits grid (POST/PUT /benefits) - structured CoInsurance %, Deductible & Annual Max rows
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast

from app.integrations.opendental.benefit_diff import (
    classify_coinsurance_change,
    classify_monetary_change,
    classify_quantity_change,
    summarize_dispositions,
)
from app.integrations.opendental.benefit_provenance import (
    BENEFIT_GRID_MUTATION_EVENT,
    INSADJUST_MUTATION_EVENT,
    BenefitGridGuard,
    benefit_row_fingerprint,
    collect_agent_benefit_nums,
    insadjust_fingerprint,
    last_insadjust_fingerprint,
)
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.fee_schedule_intel import detect_fee_schedule_alerts
from app.integrations.opendental.insplan_drift import detect_insplan_drift
from app.integrations.opendental.models import (
    ODBenefit,
    ODBenefitCreate,
    ODBenefitUpdate,
    ODInsHistCreate,
    ODInsHistPrefName,
    ODInsVerifyCreate,
    ODInsVerifyResponse,
)
from app.integrations.opendental.reverify import material_change_alert_items
from app.integrations.opendental.review_queue import (
    FEE_ALERT_EVENT_TYPE,
    INSPLAN_DRIFT_EVENT_TYPE,
    REVERIFY_ALERT_EVENT_TYPE,
    extract_review_items_from_grid_actions,
    persist_benefits_snapshot,
    persist_review_items,
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
    # Finer UDR categories (when present) write their own CovCats; CovCatNum dedupe avoids double-write.
    "ENDO": ("Endodontics",),
    "PERIO": ("Periodontics",),
    "MAXILLOFACIAL": ("MaxillofacialProsth",),
}

# Primary EbenefitCat per coarse category for frequency / waiting rows (first match wins).
_CATEGORY_PRIMARY_EBENEFIT: dict[str, str] = {
    "DIAGNOSTIC": "Diagnostic",
    "PREVENTIVE": "RoutinePreventive",
    "BASIC": "Restorative",
    "MAJOR": "Crowns",
    "ORTHO": "Orthodontics",
    "ENDO": "Endodontics",
    "PERIO": "Periodontics",
    "MAXILLOFACIAL": "MaxillofacialProsth",
    "DENTAL": "General",
}

_MISSING_TOOTH_EXCLUSION_CATS: tuple[str, ...] = ("Prosthodontics", "MaxillofacialProsth")

# OpenDental Insurance History PrefNames (ProcedureLogs POST InsuranceHistory).
_CDT_TO_INSHIST_PREF: dict[str, str] = {
    "D0120": "InsHistExamCodes",
    "D0140": "InsHistExamCodes",
    "D0150": "InsHistExamCodes",
    "D0160": "InsHistExamCodes",
    "D0180": "InsHistExamCodes",
    "D1110": "InsHistProphyCodes",
    "D1120": "InsHistProphyCodes",
    "D0270": "InsHistBWCodes",
    "D0272": "InsHistBWCodes",
    "D0273": "InsHistBWCodes",
    "D0274": "InsHistBWCodes",
    "D0330": "InsHistPanoCodes",
    "D4910": "InsHistPerioMaintCodes",
    "D4355": "InsHistDebridementCodes",
}

# Coarse canonical categories → InsHist pref when CDT is missing/unmapped.
_CATEGORY_TO_INSHIST_PREF: dict[str, str] = {
    "PREVENTIVE": "InsHistProphyCodes",
    "DIAGNOSTIC": "InsHistExamCodes",
}

_INSHIST_EMPTY_DATES = frozenset({"", "no history", "not set", "n/a", "none"})

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
        key = (
            row.get("cdt_code") or row.get("category") or f"rule_{row.get('source_benefit_index')}"
        )
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
        key = (
            row.get("cdt_code") or row.get("category") or f"wait_{row.get('source_benefit_index')}"
        )
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
class ProcedureEstimateLine:
    """One CDT row aligned with Treatment Plan Ins Est / Pat columns."""

    cdt_code: str
    allowed_amount: float | None = None
    insurance_pays: float | None = None
    patient_responsibility: float | None = None
    procedure_covered: bool | None = None
    waiting_period_end: str | None = None


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
    checked_cdt_codes: list[str] = field(default_factory=list)
    prior_auth_required: bool | None = None
    last_service_dates: list[str] = field(default_factory=list)
    age_limits: list[str] = field(default_factory=list)
    downgrade_notes: list[str] = field(default_factory=list)
    deductible_individual: float | None = None
    deductible_family: float | None = None
    annual_max_individual: float | None = None
    annual_max_family: float | None = None
    procedure_estimates: list[ProcedureEstimateLine] = field(default_factory=list)
    coverage_active: bool | None = None


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
    checked_cdts: list[str] = []
    checked_seen: set[str] = set()
    estimate_lines: list[ProcedureEstimateLine] = []
    plan_coverage = _to_float(canonical.get("coverage_percent"))
    total_patient = 0.0
    saw_patient = False
    for row in procedure_estimates:
        cdt = str(row.get("cdt_code") or "").strip().upper()
        if not cdt:
            continue
        if cdt not in checked_seen:
            checked_seen.add(cdt)
            checked_cdts.append(cdt)
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
        covered_raw = row.get("procedure_covered")
        covered: bool | None
        if covered_raw is None:
            covered = None
        else:
            covered = bool(covered_raw)
        wait_end = row.get("waiting_period_end")
        estimate_lines.append(
            ProcedureEstimateLine(
                cdt_code=cdt,
                allowed_amount=allowed,
                insurance_pays=ins,
                patient_responsibility=pat_val,
                procedure_covered=covered,
                waiting_period_end=str(wait_end).strip() if wait_end else None,
            )
        )

    breakdown = canonical.get("dental_benefit_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = {}
    last_service_lines: list[str] = []
    for row in canonical.get("last_service_dates") or []:
        if not isinstance(row, dict):
            continue
        cdt = str(row.get("cdt_code") or "").strip().upper()
        svc = str(row.get("service_date") or "").strip()
        if not svc:
            continue
        last_service_lines.append(f"{cdt or 'visit'}: {svc}" if cdt else svc)
    age_lines: list[str] = []
    for row in breakdown.get("age_limits") or []:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if desc:
            age_lines.append(desc)
    downgrade_lines: list[str] = []
    for row in breakdown.get("downgrades") or []:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or "").strip()
        if desc:
            downgrade_lines.append(desc)
    prior_auth = canonical.get("prior_auth_required")
    if prior_auth is not None:
        prior_auth = bool(prior_auth)
    active_raw = canonical.get("is_active")
    coverage_active = None if active_raw is None else bool(active_raw)

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
        checked_cdt_codes=checked_cdts,
        prior_auth_required=prior_auth,
        last_service_dates=last_service_lines[:12],
        age_limits=age_lines[:12],
        downgrade_notes=downgrade_lines[:12],
        deductible_individual=_to_float(canonical.get("deductible_individual")),
        deductible_family=_to_float(canonical.get("deductible_family")),
        annual_max_individual=_to_float(canonical.get("annual_max_individual")),
        annual_max_family=_to_float(canonical.get("annual_max_family")),
        procedure_estimates=estimate_lines,
        coverage_active=coverage_active,
    )


def _plan_line(snapshot: CanonicalBenefitSnapshot) -> str:
    if snapshot.plan_name and snapshot.carrier_name:
        return f"{snapshot.plan_name} - {snapshot.carrier_name}"
    return snapshot.plan_name or snapshot.carrier_name or "n/a"


def _estimate_status(line: ProcedureEstimateLine) -> str:
    if line.waiting_period_end:
        return f"Waiting until {line.waiting_period_end}"
    if line.procedure_covered is False:
        return "Not covered"
    if line.procedure_covered is True:
        return "Covered"
    return "Reviewed"


def _pad(text: str, width: int) -> str:
    return text.ljust(width)[:width]


def format_benefit_notes(snapshot: CanonicalBenefitSnapshot) -> str:
    """Readable BenefitNotes block oriented around Treatment Plan Ins Est / Pat."""
    lines: list[str] = []
    lines.append("Eligibility verification summary")
    lines.append(f"Verified: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Plan: {_plan_line(snapshot)}")
    if snapshot.coverage_active is not None:
        lines.append(f"Coverage: {'Active' if snapshot.coverage_active else 'Inactive'}")
    lines.append(f"Routing status: {snapshot.routing_status}")
    if snapshot.check_id:
        lines.append(f"Check ID: {snapshot.check_id}")
    lines.append("")

    lines.append("Treatment Plan estimates (Ins Est / Pat)")
    lines.append("-" * 56)
    if snapshot.procedure_estimates:
        lines.append(
            f"{_pad('Code', 8)}{_pad('Allowed', 12)}{_pad('Ins Est', 12)}"
            f"{_pad('Pat Est', 12)}Status"
        )
        for row in snapshot.procedure_estimates:
            lines.append(
                f"{_pad(row.cdt_code, 8)}"
                f"{_pad(_money(row.allowed_amount), 12)}"
                f"{_pad(_money(row.insurance_pays), 12)}"
                f"{_pad(_money(row.patient_responsibility), 12)}"
                f"{_estimate_status(row)}"
            )
        if snapshot.patient_estimated_responsibility is not None:
            lines.append("")
            lines.append(
                f"Total estimated patient portion: "
                f"{_money(snapshot.patient_estimated_responsibility)}"
            )
    else:
        lines.append("No procedure estimates for this check.")
        lines.append(
            "After benefits grid / InsAdjust writeback, open Treatment Plan "
            "to review Ins Est and Pat columns."
        )
    lines.append("")

    lines.append("Plan accumulators")
    lines.append("-" * 56)
    lines.append(
        f"Deductible: {_money(snapshot.deductible_total)} total / "
        f"{_money(snapshot.deductible_remaining)} remaining"
    )
    if snapshot.deductible_individual is not None or snapshot.deductible_family is not None:
        lines.append(
            f"  Individual {_money(snapshot.deductible_individual)} | "
            f"Family {_money(snapshot.deductible_family)}"
        )
    lines.append(
        f"Annual maximum: {_money(snapshot.annual_max_total)} total / "
        f"{_money(snapshot.annual_max_remaining)} remaining"
    )
    if snapshot.annual_max_individual is not None or snapshot.annual_max_family is not None:
        lines.append(
            f"  Individual {_money(snapshot.annual_max_individual)} | "
            f"Family {_money(snapshot.annual_max_family)}"
        )
    if snapshot.copay is not None:
        lines.append(f"Copay: {_money(snapshot.copay)}")
    lines.append("")

    lines.append("Coverage by code")
    lines.append("-" * 56)
    if snapshot.coverage_percent_by_cdt:
        for cdt in sorted(snapshot.coverage_percent_by_cdt):
            lines.append(f"  {cdt}: {_pct(snapshot.coverage_percent_by_cdt[cdt])}")
    else:
        lines.append("  n/a")
    lines.append("")

    lines.append("Plan rules")
    lines.append("-" * 56)
    if snapshot.frequency_limits:
        lines.append("Frequency:")
        for label in sorted(snapshot.frequency_limits):
            lines.append(f"  - {label}: {snapshot.frequency_limits[label]}")
    else:
        lines.append("Frequency: n/a")
    if snapshot.waiting_periods:
        lines.append("Waiting periods:")
        for label in sorted(snapshot.waiting_periods):
            lines.append(f"  - {label}: {snapshot.waiting_periods[label]}")
    else:
        lines.append("Waiting periods: n/a")
    lines.append(f"Missing tooth clause: {snapshot.missing_tooth_clause or 'n/a'}")
    if snapshot.prior_auth_required is None:
        lines.append("Prior authorization: n/a")
    else:
        lines.append(
            f"Prior authorization: {'required' if snapshot.prior_auth_required else 'not required'}"
        )
    if snapshot.last_service_dates:
        lines.append("Last service dates:")
        for item in snapshot.last_service_dates:
            lines.append(f"  - {item}")
    if snapshot.age_limits:
        lines.append("Age limits:")
        for item in snapshot.age_limits:
            lines.append(f"  - {item}")
    if snapshot.downgrade_notes:
        lines.append("Downgrades / alternate benefits:")
        for item in snapshot.downgrade_notes:
            lines.append(f"  - {item}")
    lines.append("")
    lines.append(
        "Note: Structured benefits and usage writeback update native Treatment Plan "
        "Ins Est / Pat when full writeback is enabled."
    )
    lines.append(VERIFIED_BY_EZFI)

    return _truncate("\n".join(lines)).encode("ascii", "replace").decode("ascii")


def build_subscriber_note(snapshot: CanonicalBenefitSnapshot, canonical: dict[str, Any]) -> str:
    """Quiet, formal InsSub.SubscNote (OD still renders SubscNote in bold red)."""
    del canonical  # coverage_active already captured on the snapshot
    when = snapshot.timestamp.strftime("%Y-%m-%d")
    parts: list[str] = [f"Eligibility verified {when}"]
    if snapshot.coverage_active is True:
        parts.append("Coverage active")
    elif snapshot.coverage_active is False:
        parts.append("Coverage inactive")
    if snapshot.deductible_remaining is not None:
        parts.append(f"Deductible remaining {_money(snapshot.deductible_remaining)}")
    if snapshot.annual_max_remaining is not None:
        parts.append(f"Annual max remaining {_money(snapshot.annual_max_remaining)}")
    if snapshot.patient_estimated_responsibility is not None:
        parts.append(
            f"Est. patient portion {_money(snapshot.patient_estimated_responsibility)}"
        )
    summary = ". ".join(parts) + "."
    return _truncate(summary).encode("ascii", "replace").decode("ascii")


def build_commlog_summary(snapshot: CanonicalBenefitSnapshot) -> str:
    """Concise one-glance summary for the front desk (ASCII only)."""
    parts = [
        f"Eligibility {snapshot.routing_status}",
        f"plan {_plan_line(snapshot)}",
        f"deductible remaining {_money(snapshot.deductible_remaining)}",
        f"annual max remaining {_money(snapshot.annual_max_remaining)}",
    ]
    if snapshot.checked_cdt_codes:
        parts.append("checked " + ",".join(snapshot.checked_cdt_codes))
    if snapshot.patient_estimated_responsibility is not None:
        parts.append(f"est patient {_money(snapshot.patient_estimated_responsibility)}")
    summary = (
        f"[{VERIFIED_BY_EZFI}] "
        + "; ".join(parts)
        + f". Verified {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}."
    )
    return _truncate(summary).encode("ascii", "replace").decode("ascii")


def _normalize_inshist_proc_date(value: Any) -> str | None:
    """Return yyyy-MM-dd or None when the value is empty / not a usable date."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    if not text or text.lower() in _INSHIST_EMPTY_DATES:
        return None
    # Accept ISO datetime or date prefixes.
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text and len(text) > 10:
        text = text.split(" ", 1)[0]
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _inshist_pref_for_row(row: dict[str, Any]) -> str | None:
    cdt = str(row.get("cdt_code") or "").strip().upper()
    if cdt in _CDT_TO_INSHIST_PREF:
        return _CDT_TO_INSHIST_PREF[cdt]
    category = str(row.get("category") or "").strip().upper()
    return _CATEGORY_TO_INSHIST_PREF.get(category)


def build_inshist_targets(canonical: dict[str, Any]) -> list[dict[str, str]]:
    """Map canonical last_service_dates to OD InsHist PrefName + ProcDate targets.

    One target per PrefName (latest date wins when multiple rows map to the same Hist category).
    """
    by_pref: dict[str, str] = {}
    for row in canonical.get("last_service_dates") or []:
        if not isinstance(row, dict):
            continue
        pref = _inshist_pref_for_row(row)
        proc_date = _normalize_inshist_proc_date(row.get("service_date"))
        if not pref or not proc_date:
            continue
        prior = by_pref.get(pref)
        if prior is None or proc_date > prior:
            by_pref[pref] = proc_date
    return [
        {"ins_hist_pref_name": pref, "proc_date": proc_date}
        for pref, proc_date in sorted(by_pref.items())
    ]


def run_opendental_inshist_writeback(
    client: OpenDentalClient,
    *,
    pat_num: int,
    ins_sub_num: int,
    canonical: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write last service dates into OD Insurance History (Family Module Hist)."""
    targets = build_inshist_targets(canonical)
    if not targets:
        return {"skipped": "no_mappable_last_service_dates", "actions": []}

    existing_by_pref: dict[str, str] = {}
    try:
        for row in client.get_insurance_history(pat_num, ins_sub_num):
            pref = (row.insHistPrefName or "").strip()
            existing_date = _normalize_inshist_proc_date(row.procDate)
            if pref and existing_date:
                existing_by_pref[pref] = existing_date
    except Exception as exc:
        logger.warning("OpenDental GET InsuranceHistory failed: %s", exc)
        return {"error": str(exc), "actions": [], "targets": targets}

    actions: list[dict[str, Any]] = []
    for target in targets:
        pref = target["ins_hist_pref_name"]
        proc_date = target["proc_date"]
        existing = existing_by_pref.get(pref)
        if existing == proc_date:
            actions.append(
                {
                    "pref": pref,
                    "proc_date": proc_date,
                    "action": "skipped_unchanged",
                    "existing": existing,
                }
            )
            continue
        if dry_run:
            actions.append(
                {
                    "pref": pref,
                    "proc_date": proc_date,
                    "action": "proposed",
                    "existing": existing,
                    "dry_run": True,
                }
            )
            continue
        try:
            resp = client.create_insurance_history(
                ODInsHistCreate(
                    PatNum=pat_num,
                    InsSubNum=ins_sub_num,
                    insHistPrefName=cast(ODInsHistPrefName, pref),
                    ProcDate=proc_date,
                )
            )
            actions.append(
                {
                    "pref": pref,
                    "proc_date": proc_date,
                    "action": "created" if existing is None else "updated",
                    "existing": existing,
                    "response": resp,
                }
            )
        except Exception as exc:
            logger.warning("OpenDental InsHist write failed (%s): %s", pref, exc)
            actions.append(
                {
                    "pref": pref,
                    "proc_date": proc_date,
                    "action": "error",
                    "existing": existing,
                    "error": str(exc),
                }
            )

    errors = [a for a in actions if a.get("action") == "error"]
    out: dict[str, Any] = {
        "pat_num": pat_num,
        "ins_sub_num": ins_sub_num,
        "targets": targets,
        "actions": actions,
    }
    if errors:
        out["error"] = str(errors[0].get("error") or "inshist_write_failed")
    return out


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


def _covcat_for_category(category: Any, ebenefit_to_covcat: dict[str, int]) -> int | None:
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
                time_period = "CalendarYear" if months >= 12 and months % 12 == 0 else "Months"
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
                "label": str(row.get("description") or "waiting_period").strip()
                or "waiting_period",
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


def _merge_limitation_sources(
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prefer canonical dental_benefit_breakdown; fall back to UDR lists when richer."""
    breakdown = dict(_breakdown_dict(canonical))
    record = universal_record or {}

    def _as_dict_list(rows: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                item = dict(row)
                cat = item.get("category")
                if hasattr(cat, "value"):
                    item["category"] = cat.value
                elif cat is not None:
                    item["category"] = str(cat)
                out.append(item)
        return out

    canon_freq = breakdown.get("frequency_limitations") or []
    udr_freq = _as_dict_list(record.get("frequency_limitations"))
    if len(udr_freq) > len(canon_freq or []):
        breakdown["frequency_limitations"] = udr_freq

    canon_wait = breakdown.get("waiting_periods") or []
    udr_wait = _as_dict_list(record.get("waiting_periods"))
    if len(udr_wait) > len(canon_wait or []):
        breakdown["waiting_periods"] = udr_wait

    if not isinstance(breakdown.get("missing_tooth_clause"), dict):
        udr_mtc = record.get("missing_tooth_clause")
        if isinstance(udr_mtc, dict):
            breakdown["missing_tooth_clause"] = udr_mtc

    return breakdown


def _ortho_lifetime_max(
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
) -> float | None:
    breakdown = _breakdown_dict(canonical)
    direct = _to_float(breakdown.get("ortho_lifetime_max"))
    if direct is not None:
        return direct
    record = universal_record or {}
    financial = record.get("financial") if isinstance(record.get("financial"), dict) else {}
    ortho_dp = financial.get("ortho_lifetime_max") if isinstance(financial, dict) else None
    if isinstance(ortho_dp, dict):
        return _to_float(ortho_dp.get("value"))
    ortho = record.get("ortho") if isinstance(record.get("ortho"), dict) else {}
    if isinstance(ortho, dict):
        lt = ortho.get("lifetime_max")
        if isinstance(lt, dict):
            return _to_float(lt.get("value"))
    return None


def _plan_clause_notes(
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
) -> list[str]:
    """Free-text plan clauses (downgrade / alternate benefit / age) for notes only."""
    notes: list[str] = []
    breakdown = _breakdown_dict(canonical)
    for key in ("limitation_notes", "plan_notes", "special_clauses"):
        raw = breakdown.get(key)
        if isinstance(raw, list):
            for item in raw:
                text = str(item).strip()
                if text:
                    notes.append(text)
        elif isinstance(raw, str) and raw.strip():
            notes.append(raw.strip())
    for key in ("downgrades", "age_limits"):
        raw = breakdown.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    text = str(item.get("description") or "").strip()
                else:
                    text = str(item).strip()
                if text:
                    notes.append(text)
    record = universal_record or {}
    for key in ("limitation_notes", "waiting_period_notes", "downgrades", "age_limits"):
        raw = record.get(key)
        if isinstance(raw, list):
            for item in raw:
                text = (
                    str(item).strip()
                    if not isinstance(item, dict)
                    else str(item.get("description") or "").strip()
                )
                if text:
                    notes.append(text)
    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for note in notes:
        if note in seen:
            continue
        seen.add(note)
        unique.append(note)
    return unique[:12]


def build_benefit_grid_targets(
    *,
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Translate normalized eligibility into structured OD benefit-grid targets.

    Returns a dict with:
      - coverage: list of {ebenefit_cats, percent, label} CoInsurance targets
      - annual_max / deductible / ortho_lifetime_max / copay
      - active_coverage: bool | None
      - frequency_limitations / waiting_periods / exclusions
      - plan_clauses: notes-only special clause strings
    Values absent from the 271 are left as None and simply not written.
    """
    coverage: list[dict[str, Any]] = []
    record = universal_record or {}
    breakdown = _merge_limitation_sources(canonical, universal_record)
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

    copay = _to_float(canonical.get("copay"))
    if copay is not None and copay <= 0:
        copay = None

    active: bool | None = bool(canonical.get("is_active")) if "is_active" in canonical else None

    return {
        "coverage": coverage,
        "annual_max": _to_float(canonical.get("annual_max_total")),
        "deductible": _to_float(canonical.get("deductible_total")),
        "ortho_lifetime_max": _ortho_lifetime_max(canonical, universal_record),
        "copay": copay,
        "active_coverage": active,
        "frequency_limitations": _build_frequency_grid_targets(breakdown),
        "waiting_periods": _build_waiting_grid_targets(breakdown),
        "exclusions": _build_exclusion_grid_targets(breakdown),
        "plan_clauses": _plan_clause_notes(canonical, universal_record),
    }


def snapshot_od_benefits(rows: list[ODBenefit]) -> list[dict[str, Any]]:
    """Serialize current OD benefit rows for audit / rollback comparison."""
    return [benefit_row_fingerprint(row) for row in rows]


def run_opendental_benefits_grid_writeback(
    client: OpenDentalClient,
    *,
    plan_num: int,
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
    respect_manual_edits: bool = True,
    agent_benefit_nums: set[int] | None = None,
    check_id: str | None = None,
    dry_run: bool = False,
    confidence_gating: bool = False,
) -> dict[str, Any]:
    """Upsert structured benefit-grid rows (Edit Benefits + Other Benefits).

    Idempotent: existing rows (matched by BenefitType + CovCatNum) are PUT-updated only when
    the value changed; missing rows are POST-created. Each row is fault-isolated so one failure
    never aborts the rest. Mutates plan-level benefits shared by all subscribers on the plan,
    which mirrors OpenDental's own "Import Benefits" behavior from a 271.

    When ``dry_run`` is True (Track A shadow-compare), no POST/PUT is issued — proposed
    actions are returned with disposition metadata. When ``confidence_gating`` is True
    (Track C), large deltas are ``skipped_needs_review`` instead of auto-applied.
    """
    try:
        covcats = client.get_covcats()
        existing = client.get_benefits(plan_num)
    except Exception as exc:
        logger.warning("OpenDental benefits-grid fetch failed: %s", exc)
        return {"error": f"fetch_failed: {exc}"}

    pre_snapshot = snapshot_od_benefits(existing)

    ebenefit_to_covcat: dict[str, int] = {}
    for c in covcats:
        if c.EbenefitCat and c.CovCatNum is not None:
            ebenefit_to_covcat.setdefault(c.EbenefitCat, c.CovCatNum)
    general_num = ebenefit_to_covcat.get("General")
    ortho_num = ebenefit_to_covcat.get("Orthodontics")

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

    def _gate_or_skip(
        decision: Any,
        *,
        target: str,
        benefit_type: str,
        extra: dict[str, Any] | None = None,
        agent_owned: bool = False,
    ) -> bool:
        """Return True when the caller should skip the write (review/block/unchanged)."""
        payload = {
            "target": target,
            "type": benefit_type,
            "disposition": decision.disposition,
            "reason": decision.reason,
            "previous": decision.previous,
            "proposed": decision.proposed,
            "delta": decision.delta,
            **(extra or {}),
        }
        if decision.disposition == "unchanged":
            actions.append({**payload, "action": "unchanged"})
            return True
        # Force-overwrite mode (respect_manual_edits=False) applies large deltas;
        # otherwise material changes are queued for review (Track C).
        needs_review = (
            decision.disposition == "review"
            and confidence_gating
            and not dry_run
            and respect_manual_edits
        )
        _ = agent_owned  # reserved for future provenance-aware thresholds
        if needs_review:
            actions.append({**payload, "action": "skipped_needs_review"})
            return True
        if decision.disposition == "block":
            actions.append({**payload, "action": "skipped_blocked"})
            return True
        if dry_run:
            action = "proposed_create" if decision.previous is None else "proposed_update"
            actions.append({**payload, "action": action, "disposition": "auto"})
            return True
        return False

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
        decision = classify_coinsurance_change(
            previous=existing_row.Percent if existing_row is not None else None,
            proposed=percent,
        )
        try:
            if existing_row is not None and _skip_human_edit(
                existing_row, target=label, benefit_type="CoInsurance"
            ):
                return
            agent_owned = bool(
                existing_row is not None
                and getattr(existing_row, "BenefitNum", None) in guard.agent_benefit_nums
            )
            if _gate_or_skip(
                decision,
                target=label,
                benefit_type="CoInsurance",
                extra={"cov_cat_num": cov_cat_num, "percent": percent},
                agent_owned=agent_owned,
            ):
                return
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
                            "disposition": "auto",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            else:
                client.update_benefit(existing_row.BenefitNum, ODBenefitUpdate(Percent=percent))
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": "CoInsurance",
                            "cov_cat_num": cov_cat_num,
                            "percent": percent,
                            "action": "updated",
                            "disposition": "auto",
                            "benefit_num": existing_row.BenefitNum,
                            "previous_percent": existing_row.Percent,
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

    def _upsert_monetary(
        benefit_type: str,
        amount: float,
        label: str,
        *,
        cov_cat_num: int | None = None,
        coverage_level: str = "Individual",
        time_period: str = "CalendarYear",
    ) -> None:
        cat_num = general_num if cov_cat_num is None else cov_cat_num
        if cat_num is None:
            actions.append(
                {"target": label, "type": benefit_type, "action": "skipped_no_general_covcat"}
            )
            return
        existing_row = (
            _find_limitations_monetary(cat_num)
            if benefit_type == "Limitations"
            else _find(benefit_type, cat_num)
        )
        decision = classify_monetary_change(
            previous=existing_row.MonetaryAmt if existing_row is not None else None,
            proposed=amount,
            field=f"{benefit_type}.MonetaryAmt",
        )
        try:
            if existing_row is not None and _skip_human_edit(
                existing_row, target=label, benefit_type=benefit_type
            ):
                return
            agent_owned = bool(
                existing_row is not None
                and getattr(existing_row, "BenefitNum", None) in guard.agent_benefit_nums
            )
            if _gate_or_skip(
                decision,
                target=label,
                benefit_type=benefit_type,
                extra={"cov_cat_num": cat_num, "amount": amount},
                agent_owned=agent_owned,
            ):
                return
            if existing_row is None:
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType=benefit_type,  # type: ignore[arg-type]
                        CoverageLevel=coverage_level,  # type: ignore[arg-type]
                        CovCatNum=cat_num,
                        MonetaryAmt=amount,
                        TimePeriod=time_period,
                    )
                )
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": benefit_type,
                            "cov_cat_num": cat_num,
                            "amount": amount,
                            "action": "created",
                            "disposition": "auto",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            else:
                client.update_benefit(existing_row.BenefitNum, ODBenefitUpdate(MonetaryAmt=amount))
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": benefit_type,
                            "cov_cat_num": cat_num,
                            "amount": amount,
                            "action": "updated",
                            "disposition": "auto",
                            "benefit_num": existing_row.BenefitNum,
                            "previous_amount": existing_row.MonetaryAmt,
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
                decision = classify_quantity_change(
                    previous=None, proposed=quantity, field="Limitations.Quantity"
                )
                if _gate_or_skip(
                    decision,
                    target=label,
                    benefit_type="Limitations",
                    extra={
                        "cov_cat_num": cov_cat_num,
                        "quantity": quantity,
                        "quantity_qualifier": quantity_qualifier,
                    },
                ):
                    return
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
                    guard.record(
                        {
                            "target": label,
                            "type": "Limitations",
                            "cov_cat_num": cov_cat_num,
                            "quantity": quantity,
                            "quantity_qualifier": quantity_qualifier,
                            "action": "created",
                            "disposition": "auto",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
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
                        "disposition": "unchanged",
                        "benefit_num": existing_row.BenefitNum,
                    }
                )
        except Exception as exc:
            logger.warning(
                "OpenDental frequency Limitations upsert failed (cat %s): %s", cov_cat_num, exc
            )
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
                decision = classify_quantity_change(
                    previous=None, proposed=months, field="WaitingPeriod.Quantity"
                )
                if _gate_or_skip(
                    decision,
                    target=label,
                    benefit_type="WaitingPeriod",
                    extra={"cov_cat_num": cov_cat_num, "months": months},
                ):
                    return
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
                    guard.record(
                        {
                            "target": label,
                            "type": "WaitingPeriod",
                            "cov_cat_num": cov_cat_num,
                            "months": months,
                            "action": "created",
                            "disposition": "auto",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            else:
                if _skip_human_edit(existing_row, target=label, benefit_type="WaitingPeriod"):
                    return
                decision = classify_quantity_change(
                    previous=existing_row.Quantity,
                    proposed=months,
                    field="WaitingPeriod.Quantity",
                )
                agent_owned = existing_row.BenefitNum in guard.agent_benefit_nums
                if _gate_or_skip(
                    decision,
                    target=label,
                    benefit_type="WaitingPeriod",
                    extra={
                        "cov_cat_num": cov_cat_num,
                        "months": months,
                        "benefit_num": existing_row.BenefitNum,
                    },
                    agent_owned=agent_owned,
                ):
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
                            "disposition": "auto",
                            "benefit_num": existing_row.BenefitNum,
                            "previous_months": existing_row.Quantity,
                        },
                        benefit_num=existing_row.BenefitNum,
                    )
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
                if dry_run:
                    actions.append(
                        {
                            "target": label,
                            "type": "Exclusions",
                            "cov_cat_num": cov_cat_num,
                            "action": "proposed_create",
                            "disposition": "auto",
                        }
                    )
                    return
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType="Exclusions",
                        CoverageLevel="None",
                        CovCatNum=cov_cat_num,
                    )
                )
                actions.append(
                    guard.record(
                        {
                            "target": label,
                            "type": "Exclusions",
                            "cov_cat_num": cov_cat_num,
                            "action": "created",
                            "disposition": "auto",
                            "benefit_num": created.BenefitNum,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            else:
                actions.append(
                    {
                        "target": label,
                        "type": "Exclusions",
                        "cov_cat_num": cov_cat_num,
                        "action": "unchanged",
                        "disposition": "unchanged",
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

    def _upsert_active_coverage(is_active: bool) -> None:
        if general_num is None:
            actions.append(
                {
                    "target": "active_coverage",
                    "type": "ActiveCoverage",
                    "action": "skipped_no_general_covcat",
                }
            )
            return
        existing_row = _find("ActiveCoverage", general_num)
        try:
            if existing_row is None and is_active:
                if dry_run:
                    actions.append(
                        {
                            "target": "active_coverage",
                            "type": "ActiveCoverage",
                            "action": "proposed_create",
                            "disposition": "auto",
                            "cov_cat_num": general_num,
                        }
                    )
                    return
                created = client.create_benefit(
                    ODBenefitCreate(
                        PlanNum=plan_num,
                        BenefitType="ActiveCoverage",
                        CoverageLevel="None",
                        CovCatNum=general_num,
                        TimePeriod="CalendarYear",
                    )
                )
                actions.append(
                    guard.record(
                        {
                            "target": "active_coverage",
                            "type": "ActiveCoverage",
                            "action": "created",
                            "disposition": "auto",
                            "benefit_num": created.BenefitNum,
                            "cov_cat_num": general_num,
                        },
                        benefit_num=created.BenefitNum,
                    )
                )
            elif existing_row is not None:
                actions.append(
                    {
                        "target": "active_coverage",
                        "type": "ActiveCoverage",
                        "action": "unchanged" if is_active else "present_inactive_flag",
                        "disposition": "unchanged",
                        "benefit_num": existing_row.BenefitNum,
                    }
                )
            else:
                actions.append(
                    {
                        "target": "active_coverage",
                        "type": "ActiveCoverage",
                        "action": "skipped_inactive",
                        "disposition": "unchanged",
                    }
                )
        except Exception as exc:
            logger.warning("OpenDental ActiveCoverage upsert failed: %s", exc)
            actions.append(
                {"target": "active_coverage", "type": "ActiveCoverage", "error": str(exc)}
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

    if targets.get("active_coverage") is not None:
        _upsert_active_coverage(bool(targets["active_coverage"]))

    if targets["annual_max"] is not None:
        _upsert_monetary("Limitations", targets["annual_max"], "annual_max")
    if targets["deductible"] is not None:
        _upsert_monetary("Deductible", targets["deductible"], "general_deductible")
    if targets.get("ortho_lifetime_max") is not None and ortho_num is not None:
        _upsert_monetary(
            "Limitations",
            float(targets["ortho_lifetime_max"]),
            "ortho_lifetime_max",
            cov_cat_num=ortho_num,
            coverage_level="Individual",
            time_period="Lifetime",
        )
    elif targets.get("ortho_lifetime_max") is not None and ortho_num is None:
        actions.append(
            {
                "target": "ortho_lifetime_max",
                "type": "Limitations",
                "action": "skipped_unresolved_covcat",
            }
        )
    if targets.get("copay") is not None:
        _upsert_monetary(
            "CoPayment",
            float(targets["copay"]),
            "copay",
            coverage_level="None",
        )

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
        "dry_run": dry_run,
        "confidence_gating": confidence_gating,
        "pre_snapshot": pre_snapshot,
        "disposition_summary": summarize_dispositions(actions),
        "plan_clauses": targets.get("plan_clauses") or [],
        "review_items": extract_review_items_from_grid_actions(actions),
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
    write_inshist: bool = False,
    write_insadjust: bool = False,
    write_benefits_grid: bool = False,
    respect_manual_edits: bool = True,
    dry_run_financial: bool = False,
    od_snapshot: dict[str, Any] | None = None,
    coverage_order: str = "primary",
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
      4. Insurance History / InsHist (last service dates for frequency)
      5. ClaimProcs InsAdjust (Phase 2 financial sync, opt-in; dry-run when shadow-compare)
      6. Benefits grid (opt-in; dry-run + confidence gating supported)
    """
    verified = verified_on or date.today()
    routing = primary_result.get("routing") or {}
    canonical = primary_result.get("canonical") or {}
    proc_estimates = primary_result.get("procedure_estimates") or []
    universal_record = primary_result.get("universal_dental_record")
    raw_check = check_id if check_id is not None else primary_result.get("check_id")
    check_id_str = str(raw_check) if raw_check else None

    confidence_gating = False
    try:
        from app.eligibility.config import get_settings as _get_elig_settings

        confidence_gating = bool(
            _get_elig_settings().opendental_write_benefits_grid_confidence_gating
        )
    except Exception:
        confidence_gating = False

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
    # Append notes-only plan clauses (downgrade / alternate benefit) into BenefitNotes via snapshot.
    plan_clauses = _plan_clause_notes(
        canonical, universal_record if isinstance(universal_record, dict) else None
    )

    result: dict[str, Any] = {
        "coverage_order": coverage_order,
        "dry_run_financial": dry_run_financial,
        "benefit_notes": None,
        "subscriber_note": None,
        "insverifies": None,
        "commlog": None,
        "inshist": None,
        "insadjust": None,
        "benefits_grid": None,
        "fee_schedule_alerts": None,
        "insplan_drift": None,
        "review_queue": None,
    }

    # 1) PRIMARY: InsSubs.BenefitNotes -------------------------------------------------
    benefit_notes_text = format_benefit_notes(snapshot)
    if plan_clauses:
        clause_block = "\nPlan Clauses:\n" + "\n".join(f" - {c}" for c in plan_clauses)
        benefit_notes_text = _truncate(benefit_notes_text.rstrip() + "\n" + clause_block)
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

    # 3b) FREQUENCY HISTORY: Insurance History (InsHist) -------------------------------
    if write_inshist:
        try:
            result["inshist"] = run_opendental_inshist_writeback(
                client,
                pat_num=pat_num,
                ins_sub_num=primary_ins_sub_num,
                canonical=canonical,
                dry_run=dry_run_financial,
            )
        except Exception as exc:
            logger.warning("OpenDental InsHist write failed: %s", exc)
            result["inshist"] = {"error": str(exc)}

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
            elif dry_run_financial:
                result["insadjust"] = {
                    "pat_plan_num": primary_pat_plan_num,
                    "ins_used": ins_used,
                    "deductible_used": ded_used,
                    "mode": "proposed",
                    "dry_run": True,
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

    # 5) STRUCTURED GRID: Benefits (Edit Benefits + Other Benefits) -------------------
    if write_benefits_grid:
        try:
            if not dry_run_financial:
                # Snapshot current OD benefits before mutation (Track C rollback support).
                try:
                    persist_benefits_snapshot(
                        patient_id=patient_id,
                        check_id=check_id_str,
                        plan_num=primary_plan_num,
                        snapshot=snapshot_od_benefits(client.get_benefits(primary_plan_num)),
                    )
                except Exception as snap_exc:
                    logger.warning("OpenDental pre-write snapshot failed: %s", snap_exc)

            grid_result = run_opendental_benefits_grid_writeback(
                client,
                plan_num=primary_plan_num,
                canonical=canonical,
                universal_record=universal_record if isinstance(universal_record, dict) else None,
                respect_manual_edits=respect_manual_edits,
                agent_benefit_nums=agent_benefit_nums,
                check_id=check_id_str,
                dry_run=dry_run_financial,
                confidence_gating=confidence_gating,
            )
            result["benefits_grid"] = grid_result
            if grid_result.get("mutations") and not dry_run_financial:
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
            review_items = list(grid_result.get("review_items") or [])
            if review_items:
                persist_review_items(
                    patient_id=patient_id,
                    check_id=check_id_str,
                    plan_num=primary_plan_num,
                    items=review_items,
                )
                result["review_queue"] = {"items": review_items, "count": len(review_items)}
        except Exception as exc:
            logger.warning("OpenDental benefits-grid write failed: %s", exc)
            result["benefits_grid"] = {"error": str(exc)}

    # 6) Track E — fee schedule / network alerts (never mutate FeeSched) --------------
    try:
        fee_alerts = detect_fee_schedule_alerts(
            canonical=canonical,
            universal_record=universal_record if isinstance(universal_record, dict) else None,
        )
        result["fee_schedule_alerts"] = fee_alerts
        if fee_alerts:
            persist_review_items(
                patient_id=patient_id,
                check_id=check_id_str,
                plan_num=primary_plan_num,
                items=fee_alerts,
                event_type=FEE_ALERT_EVENT_TYPE,
            )
    except Exception as exc:
        logger.warning("Fee schedule intel failed: %s", exc)
        result["fee_schedule_alerts"] = {"error": str(exc)}

    # 7) Track G — read-only InsPlan metadata drift ----------------------------------
    try:
        drift = detect_insplan_drift(
            od_snapshot=od_snapshot,
            canonical=canonical,
            universal_record=universal_record if isinstance(universal_record, dict) else None,
        )
        result["insplan_drift"] = drift
        if drift:
            persist_review_items(
                patient_id=patient_id,
                check_id=check_id_str,
                plan_num=primary_plan_num,
                items=drift,
                event_type=INSPLAN_DRIFT_EVENT_TYPE,
            )
    except Exception as exc:
        logger.warning("InsPlan drift detection failed: %s", exc)
        result["insplan_drift"] = {"error": str(exc)}

    # Track F — change-only reverify alerts (especially useful in shadow-compare dry-run)
    try:
        change_items = material_change_alert_items(
            benefits_grid=result.get("benefits_grid")
            if isinstance(result.get("benefits_grid"), dict)
            else None,
            insadjust=result.get("insadjust")
            if isinstance(result.get("insadjust"), dict)
            else None,
            insplan_drift=result.get("insplan_drift")
            if isinstance(result.get("insplan_drift"), list)
            else None,
        )
        if change_items and dry_run_financial:
            persist_review_items(
                patient_id=patient_id,
                check_id=check_id_str,
                plan_num=primary_plan_num,
                items=change_items,
                event_type=REVERIFY_ALERT_EVENT_TYPE,
            )
            result["reverify_alerts"] = {"items": change_items, "count": len(change_items)}
    except Exception as exc:
        logger.warning("Reverify change alerts failed: %s", exc)

    return result


_WRITEBACK_STEP_KEYS = (
    "benefit_notes",
    "subscriber_note",
    "insverifies",
    "commlog",
    "inshist",
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
