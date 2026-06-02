"""Smoke-test /eligibility/from-opendental against the live OD Local API.

Picks a demo patient with a populated ElectID, runs the route in-process via the
FastAPI TestClient, and prints a structured summary of the response.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from fastapi.testclient import TestClient

from app.eligibility.main import app

PAT_NUMS = [8, 16, 15]  # Patty PPO, Jane Smith (BCBS-CA), John Smith (Metlife)


def _short_dump(d: Any, max_chars: int = 600) -> str:
    s = json.dumps(d, indent=2, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + "\n  ... (truncated)"


def smoke(pat_num: int) -> dict[str, Any]:
    client = TestClient(app, raise_server_exceptions=False)
    payload = {
        "pat_num": pat_num,
        "trigger_event": "PRE_APPOINTMENT",
        "cdt_codes": ["D1110"],
        "practice_id": "vgd_mock_brooklyn",
        "rendering_provider_npi": "1104023674",
        "write_back": False,
    }
    print(
        f"\n==================== POST /eligibility/from-opendental (PatNum={pat_num}) ===================="
    )
    print(f"Request body: {json.dumps(payload)}")
    resp = client.post("/eligibility/from-opendental", json=payload)
    print(f"HTTP status: {resp.status_code}")
    text = resp.text or ""
    print(f"Response Content-Type: {resp.headers.get('content-type')!r}")
    try:
        body = resp.json()
        print("Response body (JSON):")
        print(_short_dump(body, max_chars=2000))
    except Exception:
        body = {"_raw_text": text}
        print(f"Response body (raw text, {len(text)} chars):")
        print(text[:2000])
    return body


def main() -> int:
    for pat_num in PAT_NUMS:
        try:
            body = smoke(pat_num)
        except Exception as e:
            print(f"PatNum {pat_num} raised: {type(e).__name__}: {e}")
            continue
        primary = (body or {}).get("primary") or {}
        routing = primary.get("routing") or {}
        opendental = (body or {}).get("opendental") or {}
        print("\n--- summary ---")
        print(f"  primary.status:    {primary.get('status')}")
        print(f"  routing.status:    {routing.get('status')}")
        print(f"  routing.action:    {routing.get('action')}")
        print(f"  opendental:        {opendental}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
