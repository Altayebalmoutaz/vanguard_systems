"""Fee schedule / network mismatch detection (Track E) — alert only, never auto-assign."""

from __future__ import annotations

from typing import Any


def detect_fee_schedule_alerts(
    *,
    canonical: dict[str, Any],
    universal_record: dict[str, Any] | None,
    has_contracted_fee_schedule: bool | None = None,
) -> list[dict[str, Any]]:
    """Return staff-facing alerts when network/fee configuration looks wrong.

    Never mutates Open Dental fee schedules — detect and alert only.
    """
    alerts: list[dict[str, Any]] = []
    record = universal_record or {}
    network = str(record.get("network_status") or "").lower()
    in_network = canonical.get("in_network")
    if in_network is None:
        in_network = canonical.get("in_network_for_fees")

    if network in ("out_of_network",) or in_network is False:
        alerts.append(
            {
                "code": "out_of_network",
                "severity": "warning",
                "message": (
                    "Payer reports out-of-network (or unknown network). "
                    "Confirm fee schedule and write-off settings before presenting estimates."
                ),
            }
        )

    if network == "in_network" or in_network is True:
        if has_contracted_fee_schedule is False:
            alerts.append(
                {
                    "code": "missing_contracted_fees",
                    "severity": "warning",
                    "message": (
                        "In-network plan but no contracted fee schedule rows found for this payer. "
                        "Estimates may use UCR/billed amounts incorrectly."
                    ),
                }
            )
        elif has_contracted_fee_schedule is None and network == "unknown":
            alerts.append(
                {
                    "code": "network_unknown",
                    "severity": "info",
                    "message": "Network status unknown; verify fee schedule assignment on the OD plan.",
                }
            )

    if network == "unknown" and in_network is None:
        alerts.append(
            {
                "code": "network_unknown",
                "severity": "info",
                "message": "Could not determine in- vs out-of-network from the 271; staff should confirm.",
            }
        )

    # Deduplicate by code
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for alert in alerts:
        code = str(alert.get("code") or "")
        if code in seen:
            continue
        seen.add(code)
        unique.append(alert)
    return unique
