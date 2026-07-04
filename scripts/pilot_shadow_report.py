#!/usr/bin/env python3
"""Print shadow pilot ROI summary for ops / design partner reporting."""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings
from app.db.connection import NeonNotConfiguredError
from app.pilot.shadow_store import get_shadow_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow pilot ROI report")
    parser.add_argument("--practice-id", required=True, help="Tenant practice_id")
    parser.add_argument("--days", type=int, default=7, help="Lookback window (1–90)")
    args = parser.parse_args()

    settings = get_settings()
    try:
        summary = get_shadow_summary(
            settings,
            practice_id=args.practice_id,
            days=args.days,
        )
    except NeonNotConfiguredError:
        print("NEON_DATABASE_URL is not configured", file=sys.stderr)
        return 1

    summary["shadow_mode_active"] = settings.pilot_shadow_mode
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
