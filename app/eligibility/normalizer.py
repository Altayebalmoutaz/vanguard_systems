"""Layer 3 — 271 JSON → canonical schema (all mapping lives here)."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from app.eligibility.benefit_block import procedure_identifier_from_benefit
from app.eligibility.layer3_llm_enrich import apply_layer3_numeric_consistency, enrich_with_llm
from app.eligibility.stedi_errors import classify_aaa_response

logger = logging.getLogger(__name__)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _benefit_amount_if_present(b: dict[str, Any]) -> float | None:
    """Only parse amount when the Stedi/271 JSON key exists (legitimate 0 vs absent field)."""
    if "benefitAmount" not in b:
        return None
    return _to_float(b.get("benefitAmount"))


def _benefit_percent_if_present(b: dict[str, Any]) -> float | None:
    if "benefitPercent" not in b:
        return None
    return _to_float(b.get("benefitPercent"))


def _benefits_list(raw: dict) -> list[dict[str, Any]]:
    bi = raw.get("benefitsInformation")
    if isinstance(bi, list):
        return [x for x in bi if isinstance(x, dict)]
    return []


def _plan_status_list(raw: dict) -> list[dict[str, Any]]:
    ps = raw.get("planStatus")
    if isinstance(ps, list):
        return [x for x in ps if isinstance(x, dict)]
    return []


def _normalize_aaa_item(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "field": item.get("field"),
        "code": item.get("code"),
        "description": item.get("description"),
        "followup_action": item.get("followupAction"),
        "location": item.get("location"),
        "possible_resolutions": item.get("possibleResolutions"),
    }


def extract_payer_aaa_errors(raw_271: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Collect payer / Stedi AAA-style errors from a 271 JSON payload.

    Sources: top-level ``errors``, ``subscriber.aaaErrors``, each ``dependents[].aaaErrors``.
    Deduplicates by (code, field, description prefix); first occurrence wins (source kept).
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []

    def add(items: Any, source: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description") or "")
            key = (
                str(item.get("code") or ""),
                str(item.get("field") or ""),
                desc[:240],
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(_normalize_aaa_item(item, source=source))

    add(raw_271.get("errors"), "payer")

    payer = raw_271.get("payer")
    if isinstance(payer, dict):
        add(payer.get("aaaErrors"), "payer")

    provider = raw_271.get("provider")
    if isinstance(provider, dict):
        add(provider.get("aaaErrors"), "provider")

    sub = raw_271.get("subscriber")
    if isinstance(sub, dict):
        add(sub.get("aaaErrors"), "subscriber")

    deps = raw_271.get("dependents")
    if isinstance(deps, list):
        for i, dep in enumerate(deps):
            if isinstance(dep, dict):
                add(dep.get("aaaErrors"), f"dependent[{i}]")

    return out


def extract_stedi_warnings(raw_271: dict[str, Any]) -> list[dict[str, Any]]:
    warnings = raw_271.get("warnings")
    if not isinstance(warnings, list):
        return []
    out: list[dict[str, Any]] = []
    for item in warnings:
        if isinstance(item, dict):
            out.append(
                {
                    "code": item.get("code"),
                    "description": item.get("description"),
                }
            )
    return out


def _additional_info_strings(benefit: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("additionalInformation", "benefitsAdditionalInformation"):
        block = benefit.get(key)
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    desc = item.get("description") or item.get("planNetworkDescription")
                    if desc:
                        out.append(str(desc).lower())
    return out


def _benefit_row_implies_remaining(benefit: dict[str, Any], full_text: str) -> bool:
    """True when the EB row is a *remaining* slice (name text or X12 time qualifier 29 / 'Remaining')."""
    if "remaining" in full_text or "balance" in full_text:
        return True
    tq = str(benefit.get("timeQualifier") or "").strip().lower()
    if "remaining" in tq:
        return True
    # X12 TM02: 29 = Remaining (common on Stedi 271 JSON)
    if str(benefit.get("timeQualifierCode") or "").strip() == "29":
        return True
    # Phrases seen across payers when separate from the word "remaining"
    if any(
        p in full_text
        for p in (
            "outstanding deductible",
            "deductible outstanding",
            "remaining deductible",
            "deductible remaining",
            "yet to meet",
            "left to meet",
            "unmet deductible",
            "deductible unmet",
            "not satisfied",
            "not yet satisfied",
        )
    ):
        return True
    if "deductible" in full_text and "unmet" in full_text:
        return True
    return False


def _benefit_row_implies_met_amount(full_text: str) -> bool:
    """Whether free text suggests this EB*C row is *satisfied / applied* deductible, not remaining total."""
    if "satisfied" in full_text or "satisfied." in full_text:
        return True
    if "met toward" in full_text or "amount met" in full_text or "amt met" in full_text:
        return True
    if "deductible met" in full_text or "ded met" in full_text:
        return True
    # "met" as a whole word in running text (not substring of "unmet"/"unlimited")
    tokens = re.findall(r"[a-z']+", full_text.lower())
    if any(t == "met" for t in tokens):
        if "unmet" in full_text:
            return False
        return True
    return False


def _infer_in_network_from_benefits(benefits: list[dict[str, Any]]) -> bool | None:
    inn: set[bool] = set()
    for b in benefits:
        code_w = str(b.get("inPlanNetworkIndicatorCode") or "").strip().upper()
        ind = str(b.get("inPlanNetworkIndicator") or "").strip().upper()
        if code_w not in ("", "W") and ind and ind not in ("NOT APPLICABLE", "W"):
            if code_w == "Y" or ind.startswith("Y"):
                inn.add(True)
            elif code_w == "N" or ind.startswith("N"):
                inn.add(False)
        texts = [
            *_additional_info_strings(b),
            str(b.get("planNetworkDescription") or "").lower(),
            str(b.get("name") or "").lower(),
        ]
        blob = " ".join(texts)
        if "in network" in blob or " inn " in f" {blob} " or "in-network" in blob:
            inn.add(True)
        if "out of network" in blob or "oon" in blob or "out-of-network" in blob:
            inn.add(False)
    if True in inn and False not in inn:
        return True
    if False in inn and True not in inn:
        return False
    return None


def _infer_in_network_from_plan_status(plan_status: list[dict[str, Any]]) -> bool | None:
    """
    Infer INN/OON from plan rows. Out-of-network hints in ``planDetails`` win over generic Active Coverage.
    Active coverage without explicit network language returns ``None`` (unknown — fee path may come from provider directory).
    """
    for p in plan_status:
        status = str(p.get("status") or "").upper()
        desc = str(p.get("planDetails") or p.get("serviceTypeCodes") or "").lower()
        if "inactive" in status.lower() or "inactive coverage" in desc:
            return False
        if "out of network" in desc or "out-of-network" in desc or " oon " in f" {desc} ":
            return False
        if "in network" in desc or "in-network" in desc:
            return True
        if "ACTIVE" in status or status.strip() in ("1", "ACTIVE COVERAGE"):
            return None
    return None


def _merge_in_network(benefits: list[dict[str, Any]], plan_status: list[dict[str, Any]]) -> bool | None:
    a = _infer_in_network_from_benefits(benefits)
    b = _infer_in_network_from_plan_status(plan_status)
    if a is not None:
        return a
    return b


def _is_active_subscriber(raw: dict) -> tuple[bool | None, str | None]:
    errs: list[str] = []
    sub = raw.get("subscriber") or {}
    if isinstance(sub, dict):
        st = sub.get("subscriberStatus") or sub.get("status")
        if st:
            s = str(st).upper()
            if "TERMIN" in s or "INACTIVE" in s:
                return False, str(st)
    for p in _plan_status_list(raw):
        st = str(p.get("status") or "").upper()
        if "ACTIVE COVERAGE" in st or st == "1":
            return True, None
        if "INACTIVE" in st or "TERMIN" in st:
            errs.append(st)
    if errs:
        return False, ";".join(errs)
    return None, None


def _service_type_codes_from(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("serviceTypeCodes", "serviceTypes"):
        v = row.get(key)
        if isinstance(v, list):
            for x in v:
                out.add(str(x).strip())
        elif v is not None:
            out.add(str(v).strip())
    return out


def _max_positive_or_max_floats(vals: list[float]) -> float | None:
    if not vals:
        return None
    pos = [v for v in vals if v > 0]
    return max(pos) if pos else max(vals)


def _collect_annual_max_stc35(benefits: list[dict[str, Any]]) -> dict[str, float | None]:
    """
    Dental annual / plan limitation max from EB* **F** rows tied to STC **35**.

    EB* **G** is out-of-pocket stop-loss (see :func:`_collect_oop_stop_loss_stc35`), not
    the same as benefit limitation ``F`` per X12 / Stedi semantics.

    Many payers interleave ``F`` rows for other categories (e.g. STC 38 ortho) with
    ``benefitAmount`` ``0`` *before* the dental aggregate rows. The generic
    :func:`_collect_financials` loop would lock ``annual_max_total`` to ``0`` on the
    first such row. Here we only consider STC **35** so contract (TM 25) / remaining
    (TM 29) pairs match Stedi/Anthem-style 271 layouts.
    """
    totals: list[float] = []
    used_vals: list[float] = []
    remainings: list[float] = []

    for b in benefits:
        if "35" not in _service_type_codes_from(b):
            continue
        code = str(b.get("code") or "").strip().upper()
        if code != "F":
            continue
        amt = _benefit_amount_if_present(b)
        if amt is None:
            continue
        full_text = _benefit_text_blob(b)
        implies_rem = _benefit_row_implies_remaining(b, full_text)
        tq_code = str(b.get("timeQualifierCode") or "").strip()
        tq = str(b.get("timeQualifier") or "").strip().lower()

        if implies_rem or tq_code == "29":
            remainings.append(amt)
        elif tq_code == "25" or tq == "contract":
            totals.append(amt)
        elif "used" in full_text or "met" in full_text:
            used_vals.append(amt)
        else:
            totals.append(amt)

    return {
        "annual_max_total": _max_positive_or_max_floats(totals),
        "annual_max_used": _max_positive_or_max_floats(used_vals),
        "annual_max_remaining_direct": _max_positive_or_max_floats(remainings),
    }


def _collect_oop_stop_loss_stc35(benefits: list[dict[str, Any]]) -> dict[str, float | None]:
    """Out-of-pocket maximum (stop-loss) from EB* **G** rows tied to STC **35**."""
    totals: list[float] = []
    used_vals: list[float] = []
    remainings: list[float] = []

    for b in benefits:
        if "35" not in _service_type_codes_from(b):
            continue
        if str(b.get("code") or "").strip().upper() != "G":
            continue
        amt = _benefit_amount_if_present(b)
        if amt is None:
            continue
        full_text = _benefit_text_blob(b)
        implies_rem = _benefit_row_implies_remaining(b, full_text)
        tq_code = str(b.get("timeQualifierCode") or "").strip()
        tq = str(b.get("timeQualifier") or "").strip().lower()

        if implies_rem or tq_code == "29":
            remainings.append(amt)
        elif tq_code == "25" or tq == "contract":
            totals.append(amt)
        elif "used" in full_text or "met" in full_text:
            used_vals.append(amt)
        else:
            totals.append(amt)

    return {
        "out_of_pocket_max_total": _max_positive_or_max_floats(totals),
        "out_of_pocket_max_used": _max_positive_or_max_floats(used_vals),
        "out_of_pocket_max_remaining_direct": _max_positive_or_max_floats(remainings),
    }


def _merge_financials_with_stc35_max(
    fin: dict[str, Any], stc35: dict[str, float | None]
) -> None:
    """Override annual max fields when STC 35 dental rows provide clearer values."""
    for key in ("annual_max_total", "annual_max_used", "annual_max_remaining_direct"):
        v = stc35.get(key)
        if v is not None:
            fin[key] = v


def _merge_financials_with_stc35_oop(
    fin: dict[str, Any], stc35_oop: dict[str, float | None]
) -> None:
    """Override OOP stop-loss fields when STC 35 dental EB*G rows provide clearer values."""
    for key in (
        "out_of_pocket_max_total",
        "out_of_pocket_max_used",
        "out_of_pocket_max_remaining_direct",
    ):
        v = stc35_oop.get(key)
        if v is not None:
            fin[key] = v


def _network_preference_rank(benefit: dict[str, Any]) -> int:
    """Lower is preferred when choosing among duplicate EB rows (Y > N > W > other)."""
    code = str(benefit.get("inPlanNetworkIndicatorCode") or "").strip().upper()
    if code == "Y":
        return 0
    if code == "N":
        return 1
    if code == "W":
        return 2
    return 3


def _patient_coinsurance_pct_from_a_row(benefit: dict[str, Any]) -> float | None:
    """EB*A benefitPercent as patient share 0–100 (same convention as :func:`_collect_financials`)."""
    pct = _benefit_percent_if_present(benefit)
    if pct is None:
        return None
    patient_share = pct if pct <= 1.0 else pct / 100.0
    return round(patient_share * 100.0, 4)


def _best_code_a_row_for_stc(benefits: list[dict[str, Any]], stc: str) -> dict[str, Any] | None:
    """Pick one co-insurance row per STC, preferring in-network Y when multiple exist."""
    candidates: list[dict[str, Any]] = []
    for b in benefits:
        if str(b.get("code") or "").strip().upper() != "A":
            continue
        if stc not in _service_type_codes_from(b):
            continue
        candidates.append(b)
    if not candidates:
        return None
    candidates.sort(key=_network_preference_rank)
    return candidates[0]


def _collect_dental_coinsurance_by_stc(benefits: list[dict[str, Any]]) -> dict[str, float]:
    """
    Per–service-type patient coinsurance % for common dental STCs (diagnostic/restorative/crowns/ortho).

    Does not replace top-level ``coinsurance`` (first useful EB*A in :func:`_collect_financials`);
    adds category-level detail for displays and future tiered estimates.
    """
    out: dict[str, float] = {}
    for stc in ("23", "25", "35", "36", "38"):
        row = _best_code_a_row_for_stc(benefits, stc)
        if row is None:
            continue
        p = _patient_coinsurance_pct_from_a_row(row)
        if p is not None:
            out[stc] = p
    return out


def _collect_ortho_lifetime_max_stc38(benefits: list[dict[str, Any]]) -> float | None:
    """Orthodontic lifetime max: EB*F rows with STC 38 (max positive amount wins ties)."""
    amounts: list[float] = []
    for b in benefits:
        if "38" not in _service_type_codes_from(b):
            continue
        if str(b.get("code") or "").strip().upper() != "F":
            continue
        amt = _benefit_amount_if_present(b)
        if amt is None:
            continue
        amounts.append(float(amt))
    if not amounts:
        return None
    pos = [a for a in amounts if a > 0]
    return max(pos) if pos else max(amounts)


_LIMITATION_NOTE_KEYWORDS = ("frequency", "limit", "waiting", "missing tooth", "missing teeth")


def _collect_dental_limitation_notes(benefits: list[dict[str, Any]]) -> list[str]:
    """Surface plan strings often buried in additionalInformation (frequency, waiting, etc.)."""
    seen: set[str] = set()
    out: list[str] = []
    for b in benefits:
        block = b.get("additionalInformation")
        if not isinstance(block, list):
            continue
        for item in block:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description") or "").strip()
            if not desc:
                continue
            low = desc.lower()
            if not any(k in low for k in _LIMITATION_NOTE_KEYWORDS):
                continue
            label = str(b.get("name") or "Benefit").strip()
            line = f"[{label}] {desc}"
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out


def _calendar_date_from_yyyymmdd(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _benefit_text_blob(benefit: dict[str, Any]) -> str:
    """
    Concatenated lowercased text for network overrides and EB financial classification.

    Includes service type labels when present. Omits ``planCoverage`` when it is only the
    literal ``MET`` — that value is commonly a plan/product tag (not satisfied amount).
    """
    name = str(benefit.get("name") or "").lower()
    pnd = str(benefit.get("planNetworkDescription") or "").lower()
    extra = " ".join(_additional_info_strings(benefit))
    stypes = benefit.get("serviceTypes")
    svc_blob = ""
    if isinstance(stypes, list):
        svc_blob = " ".join(str(x).lower() for x in stypes if x)
    pcs = str(benefit.get("planCoverage") or "")
    if pcs.strip().upper() == "MET":
        pcs = ""
    else:
        pcs = pcs.lower()
    parts = [name, pnd, extra, svc_blob, pcs]
    return " ".join(x for x in parts if x).strip()


def _text_network_override(benefit: dict[str, Any]) -> str | None:
    blob = _benefit_text_blob(benefit)
    inn = "in network" in blob or "in-network" in blob
    oon = "out of network" in blob or "out-of-network" in blob or " oon " in f" {blob} "
    if inn and oon:
        return None
    if inn:
        return "in_network"
    if oon:
        return "out_of_network"
    return None


def _network_bucket_for_benefit(benefit: dict[str, Any]) -> tuple[str, str | None]:
    structured_code = str(benefit.get("inPlanNetworkIndicatorCode") or "").strip().upper()
    structured = {
        "Y": "in_network",
        "N": "out_of_network",
        "W": "both",
    }.get(structured_code, "unknown")
    text_override = _text_network_override(benefit)
    if text_override and text_override != structured:
        return text_override, f"free_text_network_override:{structured or 'unknown'}->{text_override}"
    return structured, None


def _coverage_level_for_benefit(benefit: dict[str, Any]) -> str | None:
    raw = benefit.get("coverageLevelCode") or benefit.get("coverageLevel")
    if raw is None:
        return None
    val = str(raw).strip().upper()
    if not val:
        return None
    if val in ("IND", "INDIVIDUAL"):
        return "IND"
    if val in ("FAM", "FAMILY"):
        return "FAM"
    return val


def _time_period_for_benefit(benefit: dict[str, Any]) -> str:
    tq_code = str(benefit.get("timeQualifierCode") or "").strip()
    tq = str(benefit.get("timeQualifier") or "").strip().lower()
    if tq_code == "29" or "remaining" in tq:
        return "remaining"
    if tq_code == "23" or "calendar" in tq:
        return "calendar_year"
    if tq_code == "25" or "contract" in tq:
        return "contract"
    return "unknown"


def _is_dental_calculator_benefit(benefit: dict[str, Any]) -> bool:
    stc = _service_type_codes_from(benefit)
    if "35" in stc:
        return True
    blob = _benefit_text_blob(benefit)
    if "dental" in blob or "oral" in blob:
        return True
    # Some payers omit STC on global EB financial rows; keep common EB codes as calculator candidates.
    return not stc and str(benefit.get("code") or "").strip().upper() in (
        "A",
        "B",
        "C",
        "F",
        "G",
        "J",
        "Y",
    )


def _empty_calculator_bucket() -> dict[str, Any]:
    return {
        "remaining_deductible": None,
        "deductible_total": None,
        "deductible_met": None,
        "coinsurance_percent": None,
        "copay_amount": None,
        "annual_max_remaining": None,
        "annual_max_total": None,
        "annual_max_used": None,
        "out_of_pocket_max_remaining": None,
        "out_of_pocket_max_total": None,
        "out_of_pocket_max_used": None,
        "spend_down_total": None,
        "spend_down_met": None,
        "spend_down_remaining": None,
        "cost_containment_total": None,
        "cost_containment_met": None,
        "cost_containment_remaining": None,
        "coverage_levels": [],
        "time_periods": [],
        "prior_auth_required": None,
        "source_benefit_indexes": [],
        "limitations_notes": [],
    }


def _append_unique(target: list[Any], value: Any) -> None:
    if value is not None and value not in target:
        target.append(value)


def _structured_prior_auth_required(benefit: dict[str, Any]) -> bool | None:
    for key in ("priorAuthorizationRequired", "priorAuthRequired", "preAuthorizationRequired"):
        v = benefit.get(key)
        if isinstance(v, bool):
            return v
    # Stedi eligibility JSON documents ``authOrCertIndicator`` (Y / N / U) per benefit row.
    stedi_auth = benefit.get("authOrCertIndicator")
    if stedi_auth is not None:
        val_stedi = str(stedi_auth).strip().upper()
        if val_stedi == "Y":
            return True
        if val_stedi == "N":
            return False
        if val_stedi == "U":
            return None
    raw = (
        benefit.get("authorizationOrCertificationIndicator")
        or benefit.get("authorizationOrCertificationCode")
        or benefit.get("priorAuthorizationIndicator")
    )
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in ("y", "yes", "required", "prior auth required", "preauth required"):
        return True
    if val in ("n", "no", "not required", "none"):
        return False
    return None


def _free_text_prior_auth_override(benefit: dict[str, Any]) -> bool | None:
    blob = _benefit_text_blob(benefit)
    has_auth = (
        "prior auth" in blob
        or "preauth" in blob
        or "pre-auth" in blob
        or "preauthorization" in blob
        or "pre-authorization" in blob
        or "precert" in blob
    )
    if not has_auth:
        return None
    if (
        "not required" in blob
        or "no prior auth" in blob
        or "without prior auth" in blob
        or "preauth not required" in blob
        or "pre-authorization not required" in blob
    ):
        return False
    if "required" in blob or "needed" in blob or "must" in blob:
        return True
    return None


def _benefit_date_value(benefit: dict[str, Any], *keys: str) -> date | None:
    bdi = benefit.get("benefitsDateInformation")
    if not isinstance(bdi, dict):
        return None
    for key in keys:
        parsed = _calendar_date_from_yyyymmdd(bdi.get(key))
        if parsed is not None:
            return parsed
    return None


def _service_delivery_frequency_rules(benefit: dict[str, Any], idx: int) -> list[dict[str, Any]]:
    delivery = benefit.get("benefitsServiceDelivery")
    if not isinstance(delivery, list):
        return []
    out: list[dict[str, Any]] = []
    for item in delivery:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(v) for v in item.values() if v is not None)
        low = text.lower()
        if not any(k in low for k in ("visit", "month", "year", "frequency", "limit", "per")):
            continue
        out.append(
            {
                "source_benefit_index": idx,
                "description": text.strip(),
                "raw": item,
            }
        )
    return out


def _quantity_based_frequency_rules(benefit: dict[str, Any], idx: int) -> list[dict[str, Any]]:
    """Stedi documents ``benefitQuantity`` + ``quantityQualifier`` on each ``benefitsInformation`` row."""
    q = benefit.get("benefitQuantity")
    if q is None or (isinstance(q, str) and not q.strip()):
        return []
    qq = benefit.get("quantityQualifier") or benefit.get("quantityQualifierCode")
    if qq is None or (isinstance(qq, str) and not str(qq).strip()):
        return []
    desc = f"{str(q).strip()} {str(qq).strip()} (benefit quantity)"
    raw = {
        "benefitQuantity": q,
        "quantityQualifier": benefit.get("quantityQualifier"),
        "quantityQualifierCode": benefit.get("quantityQualifierCode"),
    }
    return [{"source_benefit_index": idx, "description": desc, "raw": raw}]


def _classify_stedi_x12_payload(raw_271: dict[str, Any]) -> tuple[str | None, list[str]]:
    """
    Stedi may return raw X12 in ``x12``: usually a 271, sometimes a 999 implementation acknowledgment.
    """
    x12 = raw_271.get("x12")
    if not isinstance(x12, str) or not x12.strip():
        return None, []
    text = x12.strip()
    warnings: list[str] = []
    # Segment ST begins a transaction set; 999 = implementation ack (often sparse JSON alongside).
    if re.search(r"(?:^|[~])ST\*999\*", text):
        warnings.append("stedi_x12_payload:implementation_ack_999_not_271")
        return "999", warnings
    if re.search(r"(?:^|[~])ST\*271\*", text):
        return "271", warnings
    return None, warnings


def _collect_carve_outs(benefit: dict[str, Any], idx: int) -> list[dict[str, Any]]:
    entities = benefit.get("benefitsRelatedEntities")
    if not isinstance(entities, list):
        return []
    out: list[dict[str, Any]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        identifier = str(entity.get("entityIdentifier") or entity.get("entityIdentifierCode") or "").strip()
        if identifier.lower() != "third-party administrator":
            continue
        out.append(
            {
                "source_benefit_index": idx,
                "entity_identifier": identifier,
                "entity_identification_value": entity.get("entityIdentificationValue"),
                "follow_up_required": True,
                "reason": "third_party_administrator_carve_out",
            }
        )
    return out


def _classify_aaa_actions(payer_aaa_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = {"errors": [{**err, "followupAction": err.get("followup_action")} for err in payer_aaa_errors]}
    return classify_aaa_response(raw, http_status=200)


def _collect_dental_calculator_ready(
    benefits: list[dict[str, Any]], payer_aaa_errors: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    buckets = {
        "in_network": _empty_calculator_bucket(),
        "out_of_network": _empty_calculator_bucket(),
        "both": _empty_calculator_bucket(),
        "unknown": _empty_calculator_bucket(),
    }
    frequency_rules: list[dict[str, Any]] = []
    latest_visits: list[dict[str, Any]] = []
    carve_outs: list[dict[str, Any]] = []
    free_text_overrides: list[dict[str, Any]] = []
    warnings: list[str] = []

    for idx, benefit in enumerate(benefits):
        if not _is_dental_calculator_benefit(benefit):
            continue

        bucket_name, network_override = _network_bucket_for_benefit(benefit)
        bucket = buckets[bucket_name]
        _append_unique(bucket["source_benefit_indexes"], idx)
        if network_override:
            free_text_overrides.append({"source_benefit_index": idx, "field": "network_status", "override": network_override})
            warnings.append(network_override)

        coverage_level = _coverage_level_for_benefit(benefit)
        _append_unique(bucket["coverage_levels"], coverage_level)
        _append_unique(bucket["time_periods"], _time_period_for_benefit(benefit))

        code = str(benefit.get("code") or "").strip().upper()
        amt = _benefit_amount_if_present(benefit)
        pct = _benefit_percent_if_present(benefit)
        full_text = _benefit_text_blob(benefit)
        implies_rem = _benefit_row_implies_remaining(benefit, full_text)

        if code == "C" and amt is not None:
            if implies_rem:
                bucket["remaining_deductible"] = amt
            elif _benefit_row_implies_met_amount(full_text):
                bucket["deductible_met"] = amt
            else:
                bucket["deductible_total"] = amt
        elif code == "F" and amt is not None:
            if implies_rem:
                bucket["annual_max_remaining"] = amt
            elif "used" in full_text or "met" in full_text:
                bucket["annual_max_used"] = amt
            else:
                bucket["annual_max_total"] = amt
        elif code == "G" and amt is not None:
            if implies_rem:
                bucket["out_of_pocket_max_remaining"] = amt
            elif "used" in full_text or "met" in full_text:
                bucket["out_of_pocket_max_used"] = amt
            else:
                bucket["out_of_pocket_max_total"] = amt
        elif code == "Y" and amt is not None:
            if implies_rem:
                bucket["spend_down_remaining"] = amt
            elif _benefit_row_implies_met_amount(full_text):
                bucket["spend_down_met"] = amt
            else:
                bucket["spend_down_total"] = amt
        elif code == "J" and amt is not None:
            if implies_rem:
                bucket["cost_containment_remaining"] = amt
            elif _benefit_row_implies_met_amount(full_text):
                bucket["cost_containment_met"] = amt
            else:
                bucket["cost_containment_total"] = amt
        elif code == "A" and pct is not None:
            patient_share = pct if pct <= 1.0 else pct / 100.0
            bucket["coinsurance_percent"] = round(patient_share * 100.0, 4)
        elif code == "B" and amt is not None:
            bucket["copay_amount"] = amt

        prior_structured = _structured_prior_auth_required(benefit)
        prior_text = _free_text_prior_auth_override(benefit)
        prior_final = prior_text if prior_text is not None else prior_structured
        if prior_final is not None:
            bucket["prior_auth_required"] = prior_final
        if prior_text is not None and prior_text != prior_structured:
            marker = f"free_text_prior_auth_override:{prior_structured}->{prior_text}"
            free_text_overrides.append({"source_benefit_index": idx, "field": "prior_auth_required", "override": marker})
            warnings.append(marker)

        for note in _additional_info_strings(benefit):
            if any(k in note for k in _LIMITATION_NOTE_KEYWORDS) or "prior auth" in note or "preauth" in note:
                _append_unique(bucket["limitations_notes"], note)

        delivery_freq = _service_delivery_frequency_rules(benefit, idx)
        frequency_rules.extend(delivery_freq)
        if not delivery_freq:
            frequency_rules.extend(_quantity_based_frequency_rules(benefit, idx))
        visit_date = _benefit_date_value(benefit, "latestVisitOrConsultation", "lastVisit", "lastService")
        if visit_date is not None:
            latest_visits.append({"source_benefit_index": idx, "latest_visit_or_consultation": visit_date})
        carve_outs.extend(_collect_carve_outs(benefit, idx))

    return (
        {
            "network_status": buckets,
            "frequency_rules": frequency_rules,
            "latest_visit_or_consultation": latest_visits,
            "carve_outs": carve_outs,
            "aaa_actions": _classify_aaa_actions(payer_aaa_errors),
            "free_text_overrides": free_text_overrides,
        },
        warnings,
    )


def _collect_deductible_stc35(benefits: list[dict[str, Any]]) -> dict[str, float | None]:
    """
    Dental-plan deductible rows only (STC 35). Avoids locking totals to unrelated medical EB*C rows.
    """
    ded_total: float | None = None
    ded_met: float | None = None
    ded_rem_direct: float | None = None

    for b in benefits:
        if "35" not in _service_type_codes_from(b):
            continue
        if str(b.get("code") or "").strip().upper() != "C":
            continue
        amt = _benefit_amount_if_present(b)
        if amt is None:
            continue
        full_text = _benefit_text_blob(b)
        implies_rem = _benefit_row_implies_remaining(b, full_text)
        if implies_rem:
            ded_rem_direct = amt
        elif _benefit_row_implies_met_amount(full_text):
            ded_met = amt
        else:
            if ded_total is None:
                ded_total = amt

    return {
        "deductible_total": ded_total,
        "deductible_met": ded_met,
        "deductible_remaining_direct": ded_rem_direct,
    }


def _merge_financials_with_stc35_deductible(
    fin: dict[str, Any], stc35_ded: dict[str, float | None]
) -> None:
    """
    When any EB*C row exists for STC 35, use that slice only for deductible fields.

    Replaces generic :func:`_collect_financials` values so medical deductible rows do not
    override dental plan amounts.
    """
    if not any(stc35_ded.get(k) is not None for k in stc35_ded):
        return
    fin["deductible_total"] = stc35_ded.get("deductible_total")
    fin["deductible_met"] = stc35_ded.get("deductible_met")
    fin["deductible_remaining_direct"] = stc35_ded.get("deductible_remaining_direct")


def _collect_financials(benefits: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract deductible / max / OOP stop-loss / coinsurance / copay / spend-down / cost containment."""
    ded_total: float | None = None
    ded_met: float | None = None
    ded_rem_direct: float | None = None
    max_total: float | None = None
    max_used: float | None = None
    max_rem_direct: float | None = None
    oop_total: float | None = None
    oop_used: float | None = None
    oop_rem_direct: float | None = None
    spend_down_total: float | None = None
    spend_down_met: float | None = None
    spend_down_rem_direct: float | None = None
    cc_total: float | None = None
    cc_met: float | None = None
    cc_rem_direct: float | None = None
    coinsurance_patient_pct: float | None = None
    copay: float | None = None
    coverage_percent: float | None = None

    for b in benefits:
        code = str(b.get("code") or "").strip().upper()
        amt = _benefit_amount_if_present(b)
        pct = _benefit_percent_if_present(b)
        full_text = _benefit_text_blob(b)
        implies_rem = _benefit_row_implies_remaining(b, full_text)

        if code == "C" and amt is not None:
            if implies_rem:
                ded_rem_direct = amt
            elif _benefit_row_implies_met_amount(full_text):
                ded_met = amt
            else:
                if ded_total is None:
                    ded_total = amt

        if code == "F" and amt is not None:
            if implies_rem:
                max_rem_direct = amt
            elif "used" in full_text or "met" in full_text:
                max_used = amt
            else:
                if max_total is None:
                    max_total = amt

        if code == "G" and amt is not None:
            if implies_rem:
                oop_rem_direct = amt
            elif "used" in full_text or "met" in full_text:
                oop_used = amt
            else:
                if oop_total is None:
                    oop_total = amt

        if code == "Y" and amt is not None:
            if implies_rem:
                spend_down_rem_direct = amt
            elif _benefit_row_implies_met_amount(full_text):
                spend_down_met = amt
            else:
                if spend_down_total is None:
                    spend_down_total = amt

        if code == "J" and amt is not None:
            if implies_rem:
                cc_rem_direct = amt
            elif _benefit_row_implies_met_amount(full_text):
                cc_met = amt
            else:
                if cc_total is None:
                    cc_total = amt

        if code == "A" and pct is not None:
            patient_share = pct if pct <= 1.0 else pct / 100.0
            coinsurance_patient_pct = round(patient_share * 100.0, 4)
            coverage_percent = max(0.0, min(100.0, (1.0 - patient_share) * 100.0))

        if code == "B" and amt is not None:
            copay = amt

    return {
        "deductible_total": ded_total,
        "deductible_met": ded_met,
        "deductible_remaining_direct": ded_rem_direct,
        "annual_max_total": max_total,
        "annual_max_used": max_used,
        "annual_max_remaining_direct": max_rem_direct,
        "out_of_pocket_max_total": oop_total,
        "out_of_pocket_max_used": oop_used,
        "out_of_pocket_max_remaining_direct": oop_rem_direct,
        "spend_down_total": spend_down_total,
        "spend_down_met": spend_down_met,
        "spend_down_remaining_direct": spend_down_rem_direct,
        "cost_containment_total": cc_total,
        "cost_containment_met": cc_met,
        "cost_containment_remaining_direct": cc_rem_direct,
        "coinsurance": coinsurance_patient_pct,
        "copay": copay,
        "coverage_percent": coverage_percent,
    }


def _derive_remaining(
    total: float | None,
    met: float | None,
    direct: float | None,
    *,
    conflict_field: str = "deductible_remaining",
) -> tuple[float | None, str | None]:
    if total is not None and met is not None:
        derived = max(0.0, total - met)
        if direct is not None and abs(derived - direct) > 0.02:
            return derived, f"{conflict_field} conflict: derived={derived:.2f} payer={direct:.2f}"
        return derived, None
    if direct is not None:
        return direct, None
    # Explicit payer total of 0 with no separate met/remaining rows → remaining is 0 (not "absent").
    if total is not None and met is None and direct is None and total == 0.0:
        return 0.0, None
    return None, None


def _derive_max_remaining(
    total: float | None, used: float | None, direct: float | None
) -> tuple[float | None, str | None]:
    if total is not None and used is not None:
        derived = max(0.0, total - used)
        if direct is not None and abs(derived - direct) > 0.02:
            return derived, f"annual_max_remaining conflict: derived={derived:.2f} payer={direct:.2f}"
        return derived, None
    if direct is not None:
        return direct, None
    if total is not None and used is None and direct is None and total == 0.0:
        return 0.0, None
    return None, None


def _derive_oop_max_remaining(
    total: float | None, used: float | None, direct: float | None
) -> tuple[float | None, str | None]:
    if total is not None and used is not None:
        derived = max(0.0, total - used)
        if direct is not None and abs(derived - direct) > 0.02:
            return (
                derived,
                f"out_of_pocket_max_remaining conflict: derived={derived:.2f} payer={direct:.2f}",
            )
        return derived, None
    if direct is not None:
        return direct, None
    if total is not None and used is None and direct is None and total == 0.0:
        return 0.0, None
    return None, None


def _waiting_category_for_cdt(cdt: str) -> str | None:
    c = cdt.strip().upper()
    if len(c) < 2 or not c.startswith("D"):
        return None
    rest = c[1:]
    if not rest.isdigit():
        return None
    prefix = int(rest[0]) if rest[0].isdigit() else 0
    if prefix == 1:
        return "basic"
    if prefix == 8:
        return "ortho"
    if prefix == 4:
        return "perio"
    if prefix in (2, 3):
        return "major"
    return None


def _plan_level_dental_active(benefits: list[dict[str, Any]], raw_271: dict[str, Any]) -> bool:
    """True when plan or benefits clearly indicate active dental (STC 35) coverage."""
    for p in _plan_status_list(raw_271):
        st = str(p.get("status") or "").upper()
        if "INACTIVE" in st or "TERMIN" in st:
            continue
        if "35" in _service_type_codes_from(p):
            return True
        details = str(p.get("planDetails") or "").lower()
        if "dental" in details or "oral" in details:
            return True

    for b in benefits:
        code = str(b.get("code") or "").strip().upper()
        name = str(b.get("name") or "").lower()
        extra = " ".join(_additional_info_strings(b))
        blob = f"{name} {extra}".lower()
        stc = _service_type_codes_from(b)
        if "35" not in stc and "dental" not in blob and "oral" not in blob:
            continue
        if code in ("N", "I") or "not covered" in blob:
            continue
        if code == "1" or "active" in blob or "coverage" in blob:
            return True
    return False


def _procedure_covered_for_code(
    benefits: list[dict[str, Any]], cdt: str, raw_271: dict[str, Any]
) -> tuple[bool | None, str | None, date | None, str | None]:
    """Return procedure_covered, non_covered_reason, waiting_period_end, waiting_category."""
    cdt_u = cdt.strip().upper()
    for b in benefits:
        pid = procedure_identifier_from_benefit(b)
        if pid != cdt_u:
            continue
        name = str(b.get("name") or "").lower()
        code = str(b.get("code") or "").strip().upper()
        if "not covered" in name or code == "N" or code == "I":
            return False, name or "not_covered", None, _waiting_category_for_cdt(cdt_u)
        if "waiting" in name:
            end: date | None = None
            bdi = b.get("benefitsDateInformation") or {}
            if isinstance(bdi, dict):
                end_s = bdi.get("end") or bdi.get("planEnd")
                if isinstance(end_s, str) and len(end_s) >= 8:
                    try:
                        end = datetime.strptime(end_s[:8], "%Y%m%d").date()
                    except ValueError:
                        end = None
            return True, None, end, _waiting_category_for_cdt(cdt_u)
        if code == "1" or "active" in name:
            return True, None, None, _waiting_category_for_cdt(cdt_u)
    # plan-level waiting in any benefit
    global_wait: date | None = None
    for b in benefits:
        bdi = b.get("benefitsDateInformation") or {}
        if isinstance(bdi, dict):
            end_s = bdi.get("waitingPeriodEnd") or bdi.get("end")
            if isinstance(end_s, str) and len(end_s) >= 8:
                with contextlib.suppress(ValueError):
                    global_wait = datetime.strptime(end_s[:8], "%Y%m%d").date()
    if len(cdt_u) >= 2 and cdt_u.startswith("D") and _plan_level_dental_active(benefits, raw_271):
        return True, None, global_wait, _waiting_category_for_cdt(cdt_u)
    return None, None, global_wait, _waiting_category_for_cdt(cdt_u)


def normalize(raw_271: dict[str, Any], coverage_order: str) -> dict[str, Any]:
    """
    Map raw Stedi 271 JSON to canonical Vanguard schema.
    Caller may attach ``_request_procedure_codes: list[str]`` on raw_271 for per-CDT rows.
    """
    if coverage_order not in ("primary", "secondary"):
        raise ValueError("coverage_order must be 'primary' or 'secondary'")

    payer_id = str(
        raw_271.get("_trading_partner_service_id")
        or raw_271.get("payer", {}).get("payorIdentification")
        or raw_271.get("payer", {}).get("payerIdentification")
        or raw_271.get("tradingPartnerServiceId")
        or ""
    )

    benefits = _benefits_list(raw_271)
    plan_status = _plan_status_list(raw_271)

    is_active, inactive_reason = _is_active_subscriber(raw_271)
    in_network = _merge_in_network(benefits, plan_status)

    fin = _collect_financials(benefits)
    stc35_ded = _collect_deductible_stc35(benefits)
    _merge_financials_with_stc35_deductible(fin, stc35_ded)
    stc35_max = _collect_annual_max_stc35(benefits)
    if any(stc35_max.get(k) is not None for k in stc35_max):
        _merge_financials_with_stc35_max(fin, stc35_max)
    stc35_oop = _collect_oop_stop_loss_stc35(benefits)
    if any(stc35_oop.get(k) is not None for k in stc35_oop):
        _merge_financials_with_stc35_oop(fin, stc35_oop)

    dental_benefit_breakdown: dict[str, Any] = {
        "coinsurance_patient_pct_by_stc": _collect_dental_coinsurance_by_stc(benefits),
        "ortho_lifetime_max": _collect_ortho_lifetime_max_stc38(benefits),
        "limitation_notes": _collect_dental_limitation_notes(benefits),
    }
    ded_rem, ded_warn = _derive_remaining(
        fin["deductible_total"],
        fin["deductible_met"],
        fin["deductible_remaining_direct"],
    )
    max_rem, max_warn = _derive_max_remaining(
        fin["annual_max_total"],
        fin["annual_max_used"],
        fin["annual_max_remaining_direct"],
    )
    oop_rem, oop_warn = _derive_oop_max_remaining(
        fin["out_of_pocket_max_total"],
        fin["out_of_pocket_max_used"],
        fin["out_of_pocket_max_remaining_direct"],
    )
    sd_rem, sd_warn = _derive_remaining(
        fin["spend_down_total"],
        fin["spend_down_met"],
        fin["spend_down_remaining_direct"],
        conflict_field="spend_down_remaining",
    )
    cc_rem, cc_warn = _derive_remaining(
        fin["cost_containment_total"],
        fin["cost_containment_met"],
        fin["cost_containment_remaining_direct"],
        conflict_field="cost_containment_remaining",
    )

    warnings: list[str] = []
    if ded_warn:
        warnings.append(ded_warn)
    if max_warn:
        warnings.append(max_warn)
    if oop_warn:
        warnings.append(oop_warn)
    if sd_warn:
        warnings.append(sd_warn)
    if cc_warn:
        warnings.append(cc_warn)

    x12_kind, x12_warnings = _classify_stedi_x12_payload(raw_271)
    warnings.extend(x12_warnings)

    req_codes = raw_271.get("_request_procedure_codes")
    if not isinstance(req_codes, list):
        req_codes = []
    req_codes = [str(c).strip().upper() for c in req_codes if c and str(c).strip()]

    procedure_details: list[dict[str, Any]] = []
    any_covered = False
    any_not_covered = False
    for cdt in req_codes:
        pc, reason, wend, wcat = _procedure_covered_for_code(benefits, cdt, raw_271)
        if pc is True:
            any_covered = True
        if pc is False:
            any_not_covered = True
        procedure_details.append(
            {
                "cdt_code": cdt,
                "procedure_covered": pc,
                "waiting_period_end": wend,
                "waiting_period_category": wcat,
                "non_covered_reason": reason,
            }
        )

    is_covered: bool | None
    if any_not_covered and not any_covered:
        is_covered = False
    elif any_covered:
        is_covered = True
    else:
        is_covered = None

    if is_active is False:
        is_covered = False
        for row in procedure_details:
            row["procedure_covered"] = False

    checked_at = datetime.now(UTC)

    raw_stored = {k: v for k, v in raw_271.items() if not str(k).startswith("_")}

    payer_aaa_errors = extract_payer_aaa_errors(raw_271)
    stedi_warnings = extract_stedi_warnings(raw_271)
    stedi_aaa_actions = classify_aaa_response(raw_271, http_status=200)
    dental_calculator_ready, calculator_warnings = _collect_dental_calculator_ready(benefits, payer_aaa_errors)
    warnings.extend(calculator_warnings)

    canonical: dict[str, Any] = {
        "payer_id": payer_id or None,
        "checked_at": checked_at,
        "coverage_order": coverage_order,
        "is_active": is_active,
        "inactive_reason": inactive_reason,
        "is_covered": is_covered,
        "in_network": in_network,
        "coverage_percent": fin["coverage_percent"],
        "copay": fin["copay"],
        "coinsurance": fin["coinsurance"],
        "deductible_total": fin["deductible_total"],
        "deductible_met": fin["deductible_met"],
        "deductible_remaining": ded_rem,
        "annual_max_total": fin["annual_max_total"],
        "annual_max_used": fin["annual_max_used"],
        "annual_max_remaining": max_rem,
        "out_of_pocket_max_total": fin["out_of_pocket_max_total"],
        "out_of_pocket_max_used": fin["out_of_pocket_max_used"],
        "out_of_pocket_max_remaining": oop_rem,
        "spend_down_total": fin["spend_down_total"],
        "spend_down_met": fin["spend_down_met"],
        "spend_down_remaining": sd_rem,
        "cost_containment_total": fin["cost_containment_total"],
        "cost_containment_met": fin["cost_containment_met"],
        "cost_containment_remaining": cc_rem,
        "procedure_details": procedure_details,
        "has_secondary": bool(raw_271.get("_has_secondary")),
        "secondary_payer_id": raw_271.get("_secondary_payer_id"),
        "raw_response": raw_stored,
        "payer_aaa_errors": payer_aaa_errors,
        "stedi_aaa_actions": stedi_aaa_actions,
        "stedi_warnings": stedi_warnings,
        "response_complete": False,
        "missing_fields": [],
        "normalization_version": "1.0",
        "normalization_warnings": warnings,
        "dental_benefit_breakdown": dental_benefit_breakdown,
        "dental_calculator_ready": dental_calculator_ready,
        "stedi_x12_transaction_kind": x12_kind,
    }
    apply_layer3_numeric_consistency(canonical)
    enrich_with_llm(
        canonical,
        payer_id or "",
        req_codes,
    )
    return canonical
