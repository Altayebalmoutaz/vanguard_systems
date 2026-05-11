"""Structured Stedi AAA handling for production eligibility flows.

Policy source:
- https://www.stedi.com/docs/healthcare/eligibility-troubleshooting#retry-strategy
- https://www.stedi.com/docs/healthcare/eligibility-troubleshooting#payer-aaa-errors
- https://www.stedi.com/docs/healthcare/eligibility-troubleshooting#portal-credentials

Do not branch on ``possibleResolutions`` or payer free-text; Stedi notes those strings
can change. Use AAA code, HTTP status, and structured response location/source.
"""

from __future__ import annotations

from typing import Any, Literal

StediAaaAction = Literal[
    "retry_connectivity",
    "fix_input",
    "enrollment_or_portal_credentials",
    "verify_subscriber",
    "human_review",
]

CONNECTIVITY_AAA_CODES = frozenset({"42", "80"})
PROBE_CONNECTIVITY_AAA_CODES = frozenset({"79"})
SUBSCRIBER_VERIFY_AAA_CODES = frozenset({"65", "67", "72", "73", "75"})
ENROLLMENT_AAA_CODES = frozenset({"41"})


def iter_aaa_errors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return AAA-like errors from known Stedi JSON response locations."""
    out: list[dict[str, Any]] = []

    def add(items: Any, source: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                out.append({**item, "_source": source})

    add(payload.get("errors"), "payer")
    payer = payload.get("payer")
    if isinstance(payer, dict):
        add(payer.get("aaaErrors"), "payer")
    provider = payload.get("provider")
    if isinstance(provider, dict):
        add(provider.get("aaaErrors"), "provider")
    subscriber = payload.get("subscriber")
    if isinstance(subscriber, dict):
        add(subscriber.get("aaaErrors"), "subscriber")
    dependents = payload.get("dependents")
    if isinstance(dependents, list):
        for i, dep in enumerate(dependents):
            if isinstance(dep, dict):
                add(dep.get("aaaErrors"), f"dependent[{i}]")
    return out


def aaa_codes(payload: dict[str, Any]) -> set[str]:
    return {str(e.get("code") or "").strip() for e in iter_aaa_errors(payload) if e.get("code") is not None}


def classify_aaa_code(code: str, *, http_status: int | None) -> StediAaaAction:
    c = str(code).strip()
    if c in CONNECTIVITY_AAA_CODES:
        return "retry_connectivity"
    if c in PROBE_CONNECTIVITY_AAA_CODES:
        return "fix_input" if http_status and http_status >= 400 else "retry_connectivity"
    if c in ENROLLMENT_AAA_CODES:
        return "enrollment_or_portal_credentials"
    if c in SUBSCRIBER_VERIFY_AAA_CODES:
        return "verify_subscriber"
    return "human_review"


def classify_aaa_response(payload: dict[str, Any], *, http_status: int | None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for err in iter_aaa_errors(payload):
        code = str(err.get("code") or "").strip()
        if not code:
            continue
        action = classify_aaa_code(code, http_status=http_status)
        key = (code, str(err.get("_source") or ""))
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            {
                "code": code,
                "action": action,
                "source": err.get("_source"),
                "location": err.get("location"),
                "description": err.get("description"),
            }
        )
    return actions


def should_retry_for_aaa(payload: dict[str, Any], *, http_status: int | None) -> bool:
    if http_status is not None and http_status >= 400:
        return False
    return any(a["action"] == "retry_connectivity" for a in classify_aaa_response(payload, http_status=http_status))

