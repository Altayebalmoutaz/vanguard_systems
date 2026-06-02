"""Capture OpenDental API responses into local JSON fixtures.

Usage:
    python scripts/freeze_od_responses.py --pat-nums 1 2 3
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eligibility.config import get_settings
from app.integrations.opendental import OpenDentalClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze OpenDental responses into test fixtures.")
    parser.add_argument(
        "--pat-nums", nargs="+", type=int, required=True, help="OpenDental PatNum list"
    )
    parser.add_argument(
        "--output-dir",
        default="tests/fixtures/opendental",
        help="Directory where JSON fixtures are written",
    )
    args = parser.parse_args()

    settings = get_settings()
    client = OpenDentalClient.from_settings(settings)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    carrier_nums: set[int] = set()
    for pat_num in args.pat_nums:
        patient = client.get_patient(pat_num).model_dump(mode="json")
        insurance = [row.model_dump(mode="json") for row in client.get_patient_insurance(pat_num)]
        for row in insurance:
            carrier_nums.add(int(row["CarrierNum"]))

        (out_dir / f"patient_{pat_num}.json").write_text(
            json.dumps(patient, indent=2), encoding="utf-8"
        )
        (out_dir / f"familymodules_{pat_num}.json").write_text(
            json.dumps(insurance, indent=2),
            encoding="utf-8",
        )

    for carrier_num in sorted(carrier_nums):
        carrier = client.get_carrier(carrier_num).model_dump(mode="json")
        (out_dir / f"carrier_{carrier_num}.json").write_text(
            json.dumps(carrier, indent=2),
            encoding="utf-8",
        )

    print(
        f"Wrote fixtures for {len(args.pat_nums)} patients and {len(carrier_nums)} carriers to {out_dir}"
    )


if __name__ == "__main__":
    main()
