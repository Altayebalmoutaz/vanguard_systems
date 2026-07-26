"""Submit Jaguar Dent eligibility check to production and report voice queue."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

REQUEST_ID = "61943097-eebe-4b85-9c32-2014174ecd61"
BASE = "https://ezfi.smilesuite.ai/eligibility-agent"
DEMO = Path("examples/eligibility_jaguar_cigna_one_demo.json")


def load_deploy_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in Path("deploy/.env.production").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        vals[k] = v.strip().strip('"').strip("'")
    return vals


def main() -> None:
    env = load_deploy_env()
    api_key = (env.get("ELIGIBILITY_AGENT_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("ELIGIBILITY_AGENT_API_KEY missing in deploy/.env.production")

    body = json.loads(DEMO.read_text(encoding="utf-8"))
    body["eligibility_request_id"] = REQUEST_ID

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print("POST", f"{BASE}/eligibility/check")
    print("patient=Jaguar Dent payer=62308 request_id=", REQUEST_ID)
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(f"{BASE}/eligibility/check", json=body, headers=headers)
        print("http_status", resp.status_code)
        try:
            data = resp.json()
        except Exception as exc:
            print(resp.text[:1000])
            raise SystemExit(1) from exc

    Path("examples/_jaguar_live_run.latest.json").write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )

    if resp.status_code >= 400:
        print(json.dumps(data, indent=2, default=str)[:2000])
        raise SystemExit(1)

    primary = data.get("primary") or {}
    routing = primary.get("routing") or {}
    canonical = primary.get("canonical") or {}
    detail = routing.get("detail") or {}
    print("cached", data.get("cached"))
    print("check_id", primary.get("check_id"))
    print("routing_status", routing.get("status"))
    print("routing_action", routing.get("action"))
    print("missing_fields", canonical.get("missing_fields"))
    print("is_active", canonical.get("is_active"), "is_covered", canonical.get("is_covered"))
    print("voice_escalation_eligible", detail.get("voice_escalation_eligible"))
    print("voice_skip_reason", detail.get("voice_skip_reason"))
    print("voice_verification", data.get("voice_verification"))

    voice = data.get("voice_verification") or {}
    check_id = primary.get("check_id")
    if not voice.get("queued") and check_id:
        print("auto-queue missed; forcing voice queue…")
        qbody = {
            "check_id": check_id,
            "request_id": REQUEST_ID,
            "force": True,
        }
        with httpx.Client(timeout=60.0) as client:
            qresp = client.post(
                f"{BASE}/eligibility/voice/queue",
                json=qbody,
                headers=headers,
            )
            print("queue_http", qresp.status_code)
            print("queue_body", qresp.text[:1500])
            voice = (
                qresp.json()
                if qresp.headers.get("content-type", "").startswith("application/json")
                else {}
            )

    session_id = voice.get("session_id")
    print("session_id", session_id)
    if not session_id:
        print("No voice session_id — stopping")
        return

    # Poll session briefly for worker pickup
    from supabase import create_client

    sb = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    for i in range(12):
        row = (
            sb.schema("rcm")
            .table("payer_verification_sessions")
            .select(
                "id,status,call_sid,call_provider,failure_code,failure_message,missing_fields_target"
            )
            .eq("id", session_id)
            .limit(1)
            .execute()
        )
        rows = row.data or []
        if rows:
            s = rows[0]
            print(
                f"poll[{i}] status={s.get('status')} provider={s.get('call_provider')} "
                f"call_sid={s.get('call_sid')} fail={s.get('failure_code')} {s.get('failure_message')}"
            )
            if s.get("status") in {"calling", "pending_review", "approved", "failed", "completed"}:
                if s.get("status") == "calling" and s.get("call_sid"):
                    break
                if s.get("status") != "queued":
                    break
        time.sleep(5)


if __name__ == "__main__":
    main()
