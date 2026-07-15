"""Create a scrubbed Layer 3 eligibility fixture from Stedi-shaped 271 JSON.

Usage:
    python scripts/freeze_eligibility_fixture.py saved_271.json --fixture-name delta_baseline

The output format is consumed by tests/eligibility_agent/test_normalizer_fixtures.py.
The script intentionally does not auto-fill expected normalizer values; reviewers should
choose those fields deliberately after inspecting the canonical output.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_DIR = Path("tests/fixtures/eligibility_271")
DEFAULT_COVERAGE_ORDER = "primary"
IDENTITY_REDACTIONS = {
    "address": "[REDACTED_ADDRESS]",
    "address1": "[REDACTED_ADDRESS]",
    "address2": "[REDACTED_ADDRESS]",
    "city": "[REDACTED_CITY]",
    "dateofbirth": "19000101",
    "dob": "19000101",
    "email": "[REDACTED_EMAIL]",
    "firstname": "TEST",
    "gender": "U",
    "lastname": "SUBSCRIBER",
    "memberid": "REDACTED_MEMBER_ID",
    "middlename": "REDACTED",
    "patientid": "REDACTED_PATIENT_ID",
    "phone": "[REDACTED_PHONE]",
    "postalcode": "00000",
    "ssn": "[REDACTED_SSN]",
    "subscriberid": "REDACTED_SUBSCRIBER_ID",
    "zip": "00000",
}


def _canonical_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug:
        raise ValueError("fixture name must contain at least one letter or number")
    return slug


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("input JSON must be an object")
    return data


def _extract_raw_271(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept either a raw Stedi 271 object or a common wrapper containing one."""
    for key in ("raw_271", "raw_response", "rawResponse", "response"):
        value = payload.get(key)
        if isinstance(value, dict):
            return deepcopy(value)
    return deepcopy(payload)


def _scrub_identity(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for key, item in value.items():
            canonical = _canonical_key(str(key))
            if canonical in IDENTITY_REDACTIONS:
                scrubbed[key] = IDENTITY_REDACTIONS[canonical]
            else:
                scrubbed[key] = _scrub_identity(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub_identity(item) for item in value]
    return value


def scrub_raw_271(raw_271: dict[str, Any]) -> dict[str, Any]:
    """Redact direct patient/subscriber identifiers while preserving benefit semantics."""
    scrubbed = _scrub_identity(raw_271)
    if not isinstance(scrubbed, dict):
        raise TypeError("scrubbed 271 payload must remain an object")
    return scrubbed


def infer_trading_partner_service_id(raw_271: dict[str, Any]) -> str | None:
    payer = raw_271.get("payer")
    if not isinstance(payer, dict):
        payer = {}
    candidates = (
        raw_271.get("_trading_partner_service_id"),
        raw_271.get("tradingPartnerServiceId"),
        payer.get("payorIdentification"),
        payer.get("payerIdentification"),
    )
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def build_fixture(
    payload: dict[str, Any],
    *,
    fixture_name: str,
    trading_partner_service_id: str | None,
    coverage_order: str,
    request_procedure_codes: list[str],
) -> dict[str, Any]:
    raw_271 = scrub_raw_271(_extract_raw_271(payload))
    tpsid = trading_partner_service_id or infer_trading_partner_service_id(raw_271)
    if not tpsid:
        raise ValueError(
            "trading partner service id was not found; pass --trading-partner-service-id"
        )

    return {
        "fixture_name": fixture_name,
        "trading_partner_service_id": tpsid,
        "coverage_order": coverage_order,
        "request_procedure_codes": request_procedure_codes,
        "raw_271": raw_271,
        "expected": {},
    }


def write_fixture(fixture: dict[str, Any], output_dir: Path, *, overwrite: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{fixture['fixture_name']}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass --overwrite to replace it")
    path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze a scrubbed Stedi-shaped 271 payload into a Layer 3 fixture."
    )
    parser.add_argument("input_json", type=Path, help="Path to raw Stedi-shaped 271 JSON")
    parser.add_argument(
        "--fixture-name",
        help="Output fixture name; defaults to the input file stem after slugification",
    )
    parser.add_argument(
        "--trading-partner-service-id",
        help="Override payer/trading partner id when it cannot be inferred from the payload",
    )
    parser.add_argument(
        "--coverage-order",
        choices=("primary", "secondary"),
        default=DEFAULT_COVERAGE_ORDER,
        help="Coverage order stored in the fixture",
    )
    parser.add_argument(
        "--request-procedure-codes",
        nargs="*",
        default=[],
        help="CDT codes to attach as _request_procedure_codes during fixture tests",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where the fixture JSON will be written",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing fixture")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fixture_name = _slug(args.fixture_name or args.input_json.stem)
    payload = _read_json(args.input_json)
    fixture = build_fixture(
        payload,
        fixture_name=fixture_name,
        trading_partner_service_id=args.trading_partner_service_id,
        coverage_order=args.coverage_order,
        request_procedure_codes=[
            code.strip().upper() for code in args.request_procedure_codes if code.strip()
        ],
    )
    path = write_fixture(fixture, args.output_dir, overwrite=args.overwrite)
    print(f"Wrote scrubbed eligibility fixture to {path}")
    print("Fill the fixture's expected block deliberately before relying on it as a golden.")


if __name__ == "__main__":
    main()
