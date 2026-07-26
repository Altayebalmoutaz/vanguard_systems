"""Read-only Insurance Plan metadata drift detection (Track G).

Compares OD-side identifiers captured at enqueue time against the normalized 271/UDR.
Does not write InsPlan fields — fill-if-blank sync is gated separately and off by default.
"""

from __future__ import annotations

from typing import Any


def detect_insplan_drift(
    *,
    od_snapshot: dict[str, Any] | None,
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return drift findings between OD plan metadata and payer-normalized data."""
    od = od_snapshot or {}
    udr = universal_record or {}
    findings: list[dict[str, Any]] = []

    od_group = _clean(od.get("group_number") or od.get("GroupNum"))
    udr_group = _clean(udr.get("group_number"))
    if od_group and udr_group and od_group != udr_group:
        findings.append(
            {
                "field": "group_number",
                "od_value": od_group,
                "payer_value": udr_group,
                "disposition": "review",
                "message": "Group number differs between Open Dental and payer response.",
            }
        )
    elif not od_group and udr_group:
        findings.append(
            {
                "field": "group_number",
                "od_value": None,
                "payer_value": udr_group,
                "disposition": "fill_if_blank_candidate",
                "message": "Open Dental group number is empty; payer returned a group number.",
            }
        )

    od_subscriber = _clean(od.get("subscriber_id") or od.get("SubscriberID"))
    udr_subscriber = _clean(udr.get("subscriber_id"))
    if od_subscriber and udr_subscriber and od_subscriber != udr_subscriber:
        findings.append(
            {
                "field": "subscriber_id",
                "od_value": od_subscriber,
                "payer_value": udr_subscriber,
                "disposition": "review",
                "message": "Subscriber ID differs between Open Dental and payer response.",
            }
        )

    od_payer = _clean(od.get("elect_id") or od.get("primary_payer_id"))
    canon_payer = _clean(canonical.get("payer_id"))
    if od_payer and canon_payer and od_payer != canon_payer:
        findings.append(
            {
                "field": "payer_id",
                "od_value": od_payer,
                "payer_value": canon_payer,
                "disposition": "review",
                "message": "Electronic payer ID differs between OD carrier ElectID and 271 payer.",
            }
        )

    od_ordinal = od.get("ordinal")
    if od_ordinal is not None:
        try:
            ord_int = int(od_ordinal)
        except (TypeError, ValueError):
            ord_int = None
        coverage_order = str(canonical.get("coverage_order") or "").lower()
        if ord_int == 1 and coverage_order == "secondary":
            findings.append(
                {
                    "field": "ordinal",
                    "od_value": ord_int,
                    "payer_value": coverage_order,
                    "disposition": "review",
                    "message": "OD marks plan as primary but eligibility ran as secondary coverage.",
                }
            )

    plan_begin = udr.get("plan_begin_date")
    plan_end = udr.get("plan_end_date")
    if plan_end and canonical.get("is_active") is True:
        findings.append(
            {
                "field": "plan_end_date",
                "od_value": None,
                "payer_value": str(plan_end),
                "disposition": "info",
                "message": "Payer returned a plan end/termination date; confirm OD plan dates.",
            }
        )
    if plan_begin:
        findings.append(
            {
                "field": "plan_begin_date",
                "od_value": None,
                "payer_value": str(plan_begin),
                "disposition": "info",
                "message": "Payer returned a plan effective date (informational).",
            }
        )

    return findings


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
