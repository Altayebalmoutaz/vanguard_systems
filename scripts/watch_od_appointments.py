"""Testing-only watcher: poll OpenDental for appointments and auto-fire the
eligibility agent's /from-opendental route for each new appointment.

This mimics the eventual production trigger (OD activity -> eligibility run) with
a dumb polling loop. It is NOT production-hardened: no persistence, in-memory
de-dupe only, single-threaded.

Prereqs (same as the manual demo):
  - OpenDental.exe running with Local API at OPENDENTAL_BASE_URL
  - FastAPI up:  uvicorn app.main:app --port 8000
  - .env has OPENDENTAL_DEVELOPER_KEY / OPENDENTAL_CUSTOMER_KEY (+ Supabase/Stedi)

Examples:
  # Watch today's appointments, poll every 15s, hit local agent
  python scripts/watch_od_appointments.py

  # One pass only (no loop), specific date, custom CDT codes
  python scripts/watch_od_appointments.py --once --date 2026-05-29 --cdt D1110 D0120
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import httpx

# Allow running as a plain script (python scripts/watch_od_appointments.py) by
# ensuring the repo root is importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.eligibility.config import get_settings  # noqa: E402
from app.integrations.opendental.poller import fetch_appointments  # noqa: E402
from app.integrations.opendental.poller import od_headers as _od_headers  # noqa: E402


def run_eligibility(
    *,
    agent_base_url: str,
    pat_num: int,
    cdt_codes: list[str],
    trigger_event: str,
    write_back: bool,
    timeout: float,
) -> None:
    payload = {
        "pat_num": pat_num,
        "trigger_event": trigger_event,
        "cdt_codes": cdt_codes,
        "write_back": write_back,
    }
    url = f"{agent_base_url.rstrip('/')}/eligibility-agent/eligibility/from-opendental"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
    except Exception as exc:
        print(f"  [agent] PatNum {pat_num} request failed: {type(exc).__name__}: {exc}")
        return
    if resp.status_code >= 400:
        print(f"  [agent] PatNum {pat_num} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return
    out = resp.json()
    primary = out.get("primary") or {}
    routing = primary.get("routing") or {}
    print(
        f"  [agent] PatNum {pat_num} -> status={routing.get('status', '-')} "
        f"action={routing.get('action', '-')} check_id={primary.get('check_id', '-')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch OpenDental appointments and run eligibility")
    parser.add_argument("--agent-base-url", default="http://127.0.0.1:8000", help="Eligibility agent base URL")
    parser.add_argument("--date", default=date.today().isoformat(), help="Appointment date to watch (YYYY-MM-DD)")
    parser.add_argument("--interval", type=float, default=15.0, help="Polling interval in seconds")
    parser.add_argument("--cdt", nargs="+", default=["D1110"], help="CDT codes to send")
    parser.add_argument("--trigger-event", default="PRE_APPOINTMENT", help="Trigger event label")
    parser.add_argument("--write-back", action="store_true", help="Request write-back to OpenDental")
    parser.add_argument("--once", action="store_true", help="Run a single poll pass and exit")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.opendental_developer_key or not settings.opendental_customer_key:
        print("ERROR: OPENDENTAL_DEVELOPER_KEY / OPENDENTAL_CUSTOMER_KEY not set in .env")
        return 1

    headers = _od_headers(settings.opendental_developer_key, settings.opendental_customer_key)
    od_base = settings.opendental_base_url
    od_timeout = settings.opendental_timeout_seconds

    print(f"Watching OD appointments on {args.date} (poll every {args.interval}s)")
    print(f"  OD Local API: {od_base}")
    print(f"  Agent:        {args.agent_base_url}")
    print(f"  CDT codes:    {args.cdt}  write_back={args.write_back}")
    print("-" * 72)

    seen_apt: set[int] = set()
    first_pass = True

    while True:
        appointments = fetch_appointments(
            base_url=od_base, headers=headers, on_date=args.date, timeout=od_timeout
        )
        new_apts = [a for a in appointments if a.get("AptNum") not in seen_apt]

        for apt in new_apts:
            apt_num = apt.get("AptNum")
            pat_num = apt.get("PatNum")
            seen_apt.add(apt_num)
            if not pat_num:
                continue
            # On the very first pass, just baseline existing appointments unless --once.
            if first_pass and not args.once:
                continue
            print(f"[apt {apt_num}] PatNum={pat_num} {apt.get('AptStatus', '')} {apt.get('AptDateTime', '')}")
            run_eligibility(
                agent_base_url=args.agent_base_url,
                pat_num=int(pat_num),
                cdt_codes=args.cdt,
                trigger_event=args.trigger_event,
                write_back=args.write_back,
                timeout=60.0,
            )

        if first_pass and not args.once:
            print(f"Baselined {len(seen_apt)} existing appointment(s); now watching for new ones...")
        first_pass = False

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
