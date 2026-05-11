"""
Bridge TR3-style 271 fixture records (tests/fixtures/eligibility/271_fixtures.json)
to Stedi-shaped JSON for ``app.eligibility.normalizer.normalize``.

Fixtures are synthetic X12 005010X279A1 interpretations—not verbatim Stedi API payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# tests/eligibility_agent/fixture_bridge.py → parents[1] is ``tests/``
FIXTURE_JSON_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "eligibility" / "271_fixtures.json"


def load_all_fixtures() -> list[dict[str, Any]]:
    """Load the bundled fixture array."""
    raw = json.loads(FIXTURE_JSON_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("271_fixtures.json must be a JSON array")
    return [x for x in raw if isinstance(x, dict)]


def fixtures_by_id() -> dict[str, dict[str, Any]]:
    """fixture_id → fixture dict."""
    out: dict[str, dict[str, Any]] = {}
    for row in load_all_fixtures():
        fid = str(row.get("fixture_id") or "").strip()
        if fid:
            out[fid] = row
    return out


def _additional_implies_remaining(benefit: dict[str, Any]) -> bool:
    infos = benefit.get("additional_info") or []
    if not isinstance(infos, list):
        return False
    blob = " ".join(str(x).lower() for x in infos if x)
    return "remaining" in blob


def _time_qual_to_labels(q: Any) -> tuple[str | None, str | None]:
    """Return timeQualifierCode and optional human timeQualifier text."""
    if q is None:
        return None, None
    qs = str(q).strip()
    if not qs:
        return None, None
    # Matches patterns consumed by normalizer._benefit_row_implies_remaining / _collect_* helpers.
    label_map = {
        "23": "Calendar Year",
        "29": "Remaining",
        "25": "Contract",
        "26": "Episode",
        "27": "Visit",
        "28": "Lifetime",
    }
    return qs, label_map.get(qs)


def _benefit_row_to_stedi(b: dict[str, Any]) -> dict[str, Any]:
    code = str(b.get("benefit_type_code") or "").strip().upper()
    name = str(b.get("benefit_type_label") or "").strip() or code
    row: dict[str, Any] = {"code": code, "name": name}

    stcs = b.get("service_type_codes")
    if isinstance(stcs, list) and stcs:
        row["serviceTypeCodes"] = [str(x).strip() for x in stcs if x is not None]

    lbls = b.get("service_type_labels")
    if isinstance(lbls, list) and lbls:
        row["serviceTypes"] = [str(x).strip() for x in lbls if x is not None]

    cov = b.get("coverage_level_code")
    if cov is not None and str(cov).strip():
        row["coverageLevelCode"] = str(cov).strip().upper()

    tqc, tq = _time_qual_to_labels(b.get("time_period_qualifier"))
    if tqc:
        row["timeQualifierCode"] = tqc
    if tq:
        row["timeQualifier"] = tq

    amt = b.get("monetary_amount")
    if amt is not None:
        row["benefitAmount"] = amt

    pct = b.get("percent")
    if pct is not None:
        row["benefitPercent"] = pct

    inn = b.get("in_network")
    if inn is True:
        row["inPlanNetworkIndicatorCode"] = "Y"
        row["inPlanNetworkIndicator"] = "Yes"
    elif inn is False:
        row["inPlanNetworkIndicatorCode"] = "N"
        row["inPlanNetworkIndicator"] = "No"

    infos = b.get("additional_info")
    if isinstance(infos, list) and infos:
        row["additionalInformation"] = [{"description": str(x)} for x in infos if x]

    auth = b.get("authorization_required")
    if isinstance(auth, bool):
        row["priorAuthorizationRequired"] = auth

    qq = b.get("quantity_qualifier")
    qt = b.get("quantity")
    if qq is not None and qt is not None:
        row["benefitsServiceDelivery"] = [{"quantityQualifier": str(qq), "quantity": qt}]
        row.setdefault(
            "additionalInformation",
            [],
        )
        desc = f"quantity_qualifier={qq} quantity={qt}"
        ai = row["additionalInformation"]
        if isinstance(ai, list):
            ai.append({"description": desc})

    return row


def _plan_status_rows(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    ps = (fixture.get("plan_status") or "").strip().lower()
    net = (fixture.get("network_status") or "").strip().upper()

    if ps == "inactive":
        return [
            {
                "status": "Inactive Coverage",
                "serviceTypeCodes": ["30"],
                "planDetails": "Coverage terminated inactive plan status",
            },
            {
                "status": "Inactive Coverage",
                "serviceTypeCodes": ["35"],
                "planDetails": "Dental inactive terminated",
            },
        ]

    if ps == "rejected":
        # AAA-driven rejection — omit fabricated Active Coverage rows.
        return []

    net_details: str | None = None
    if net == "INN":
        net_details = "in network preferred"
    elif net == "OON":
        net_details = "out of network plan details"
    elif net == "INN_OON_SPLIT":
        # Neutral copy: phrases containing both "in network" and "out of network" skew plan inference.
        net_details = "PPO tiered network benefits"

    health_row: dict[str, Any] = {"status": "Active Coverage", "serviceTypeCodes": ["30"], "statusCode": "1"}
    dental_row: dict[str, Any] = {"status": "Active Coverage", "serviceTypeCodes": ["35"], "statusCode": "1"}
    if net_details:
        health_row["planDetails"] = net_details
        dental_row["planDetails"] = net_details

    return [health_row, dental_row]


def _subscriber_to_stedi(fixture: dict[str, Any]) -> dict[str, Any]:
    sub = fixture.get("subscriber") or {}
    if not isinstance(sub, dict):
        return {}

    ps = (fixture.get("plan_status") or "").strip().lower()
    if ps == "inactive":
        subscriber_status = "Terminated"
    elif ps == "rejected":
        subscriber_status = "Active"
    else:
        subscriber_status = "Active"

    out: dict[str, Any] = {
        "subscriberStatus": subscriber_status,
        "memberId": sub.get("member_id"),
        "firstName": sub.get("first_name"),
        "lastName": sub.get("last_name"),
        "dateOfBirth": sub.get("dob"),
        "gender": sub.get("gender"),
        "groupNumber": sub.get("group_number"),
    }
    return out


def _dependent_to_stedi(dep: dict[str, Any]) -> dict[str, Any]:
    return {
        "firstName": dep.get("first_name"),
        "lastName": dep.get("last_name"),
        "gender": dep.get("gender"),
        "relationShipCode": dep.get("relationship"),
        "dateOfBirth": dep.get("dob"),
    }


def _attach_aaa_from_fixture(raw: dict[str, Any], fixture: dict[str, Any]) -> None:
    segs = fixture.get("aaa_rejection_segments") or []
    if not isinstance(segs, list) or not segs:
        return
    errs: list[dict[str, Any]] = []
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        code = str(seg.get("reject_reason_code") or "").strip()
        desc = str(seg.get("reject_reason_label") or "").strip()
        errs.append({"field": "AAA", "code": code or "UNKNOWN", "description": desc or "Unknown AAA rejection"})
    if errs:
        raw["errors"] = errs


def fixture_to_stedi_raw(fixture: dict[str, Any]) -> dict[str, Any]:
    """
    Convert one bundled fixture record to Stedi 271-shaped JSON for ``normalize``.
    """
    payer_id = str(fixture.get("payer_id") or "").strip()
    payer_name = str(fixture.get("payer_name") or "").strip()

    raw: dict[str, Any] = {
        "payer": {"payorIdentification": payer_id or None, "name": payer_name or None},
        "subscriber": _subscriber_to_stedi(fixture),
        "planStatus": _plan_status_rows(fixture),
        "benefitsInformation": [_benefit_row_to_stedi(b) for b in (fixture.get("benefits") or []) if isinstance(b, dict)],
    }

    dep = fixture.get("dependent")
    if isinstance(dep, dict) and dep.get("first_name"):
        raw["dependents"] = [_dependent_to_stedi(dep)]

    cob = fixture.get("coordination_of_benefits")
    if isinstance(cob, dict) and cob.get("is_cob"):
        raw["_has_secondary"] = True
        pid = cob.get("primary_payer_id") or cob.get("secondary_payer_id")
        if pid:
            raw["_secondary_payer_id"] = str(pid)

    _attach_aaa_from_fixture(raw, fixture)
    return raw


def semantic_expectations(fixture: dict[str, Any]) -> dict[str, Any]:
    """
    Minimal assertions aligned with fixture semantic rows (STC 35 dental slice).

    Keys are optional — tests skip asserts when a key is absent.
    """
    out: dict[str, Any] = {}
    flags = fixture.get("normalization_flags")
    if not isinstance(flags, dict):
        flags = {}

    aaa = fixture.get("aaa_rejection_segments") or []
    if isinstance(aaa, list) and aaa:
        codes = [str(x.get("reject_reason_code") or "").strip() for x in aaa if isinstance(x, dict)]
        codes = [c for c in codes if c]
        if codes:
            out["_aaa_codes_expected"] = codes

    if flags.get("is_rejected"):
        out["_reject_fixture"] = True
        out["_expect_min_payer_aaa_errors"] = len(out.get("_aaa_codes_expected") or []) > 0
        return out

    ps = (fixture.get("plan_status") or "").strip().lower()
    if ps == "inactive":
        out["is_active"] = False
        out["is_covered"] = False
        return out

    out["is_active"] = True

    ns = (fixture.get("network_status") or "").strip().upper()
    benefits_all = [b for b in (fixture.get("benefits") or []) if isinstance(b, dict)]

    def _stc35_network_flags() -> list[bool]:
        flags: list[bool] = []
        for b in benefits_all:
            if "35" not in (b.get("service_type_codes") or []):
                continue
            inn = b.get("in_network")
            if isinstance(inn, bool):
                flags.append(inn)
        return flags

    stc35_net = _stc35_network_flags()
    if ns == "INN":
        out["in_network"] = True
    elif ns == "OON":
        # Fixture aggregate can say OON while EB rows still carry INN indicators (non-PPO quirks).
        if True not in stc35_net:
            out["in_network"] = False

    dep = fixture.get("dependent")
    wanted_cov = "DEP" if isinstance(dep, dict) and dep.get("first_name") else "IND"

    benefits = benefits_all
    stc35 = [
        b
        for b in benefits
        if "35" in (b.get("service_type_codes") or []) and str(b.get("coverage_level_code") or "").upper() == wanted_cov
    ]
    if not stc35:
        stc35 = [b for b in benefits if "35" in (b.get("service_type_codes") or [])]

    def tp(b: dict[str, Any]) -> str:
        return str(b.get("time_period_qualifier") or "").strip()

    # Align with normalizer STC35 helpers: first calendar-year C wins deductible_total;
    # last remaining-style C wins deductible_remaining; F-only drives annual max, G-only OOP stop-loss.
    d_tot: float | None = None
    d_rem_seq: list[float] = []
    f_cal_vals: list[float] = []
    f_rem_seq: list[float] = []
    g_cal_vals: list[float] = []
    g_rem_seq: list[float] = []

    for b in stc35:
        code = str(b.get("benefit_type_code") or "").strip().upper()
        amt = b.get("monetary_amount")
        if amt is None:
            continue
        try:
            fa = float(amt)
        except (TypeError, ValueError):
            continue

        if code == "C":
            if tp(b) == "29" or _additional_implies_remaining(b):
                d_rem_seq.append(fa)
            elif tp(b) == "23" and d_tot is None:
                d_tot = fa

        add_infos = b.get("additional_info") or []
        blob = ""
        if isinstance(add_infos, list):
            blob = " ".join(str(x).lower() for x in add_infos)
        is_rem = tp(b) == "29" or "remaining" in blob

        if code == "F":
            if is_rem:
                f_rem_seq.append(fa)
            elif tp(b) == "23":
                f_cal_vals.append(fa)
        elif code == "G":
            if is_rem:
                g_rem_seq.append(fa)
            elif tp(b) == "23":
                g_cal_vals.append(fa)

    if d_tot is not None:
        out["deductible_total"] = d_tot
    if d_rem_seq:
        out["deductible_remaining"] = d_rem_seq[-1]
    if f_cal_vals:
        out["annual_max_total"] = max(f_cal_vals)
    if f_rem_seq:
        pos = [x for x in f_rem_seq if x > 0]
        out["annual_max_remaining"] = max(pos) if pos else max(f_rem_seq)
    if g_cal_vals:
        out["out_of_pocket_max_total"] = max(g_cal_vals)
    if g_rem_seq:
        pos = [x for x in g_rem_seq if x > 0]
        out["out_of_pocket_max_remaining"] = max(pos) if pos else max(g_rem_seq)

    stc35_a_inn = [
        b
        for b in stc35
        if str(b.get("benefit_type_code") or "").strip().upper() == "A" and b.get("percent") is not None and b.get("in_network") is True
    ]
    if stc35_a_inn:
        p = float(stc35_a_inn[0]["percent"])  # type: ignore[index]
        patient_pct = p * 100.0 if p <= 1.0 else p
        out["coinsurance_patient_pct_stc35_inn"] = round(float(patient_pct), 4)
    else:
        stc35_a = [
            b
            for b in stc35
            if str(b.get("benefit_type_code") or "").strip().upper() == "A" and b.get("percent") is not None
        ]
        if len(stc35_a) == 1:
            p = float(stc35_a[0]["percent"])  # type: ignore[index]
            patient_pct = p * 100.0 if p <= 1.0 else p
            out["coinsurance_patient_pct_stc35_inn"] = round(float(patient_pct), 4)

    return out


def fixture_doc_hint(fixture_id: str) -> str:
    """Human-readable pointer for assertion failures."""
    return (
        f"fixture_id={fixture_id}; bundled corpus "
        f"tests/fixtures/eligibility/271_fixtures.json (see README.md in same folder)"
    )
