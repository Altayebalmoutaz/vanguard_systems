"""Queue latest incomplete check and place live Bland call for pipeline demo."""

from __future__ import annotations

import json
import sys

import httpx

from app.eligibility.config import get_settings
from app.eligibility.db import reset_supabase_client
from app.eligibility.voice.bland import initiate_bland_call
from app.eligibility.voice.db import (
    fetch_payer_voice_config,
    fetch_session_by_id,
    get_supabase_client,
    update_verification_session,
)
from app.eligibility.voice.worker import voice_webhook_url

BASE_CHECK_ID = "f5495700-9a03-451f-93de-72422b22d500"
API = "http://localhost:8000/eligibility-agent"


def main() -> None:
    reset_supabase_client()
    settings = get_settings()

    with httpx.Client(timeout=60.0) as client:
        queue_resp = client.post(
            f"{API}/eligibility/voice/queue",
            json={"check_id": BASE_CHECK_ID, "force": True},
        )
        queue_resp.raise_for_status()
        queued = queue_resp.json()
        print("QUEUE", json.dumps(queued, indent=2))
        session_id = queued.get("session_id")
        if not session_id:
            raise SystemExit("no session_id returned from queue")

    supabase = get_supabase_client(settings)
    session = fetch_session_by_id(supabase, session_id)
    if not session:
        raise SystemExit(f"session not found: {session_id}")

    payer = fetch_payer_voice_config(supabase, str(session.get("payer_id") or ""))
    print("MISSING_TARGETS", session.get("missing_fields_target"))
    print("PAYER_PHONE", payer.get("eligibility_phone") if payer else None)
    print("REQUESTED_BENEFITS_SCOPE:")
    from app.eligibility.voice.bland import _bland_context

    ctx = _bland_context(session, settings, payer or {})
    print(ctx.get("requested_benefits"))

    update_verification_session(
        supabase, session_id, {"status": "calling", "call_provider": "bland"}
    )
    webhook = voice_webhook_url(settings, f"bland/{session_id}")
    call_id = initiate_bland_call(session, settings, webhook_url=webhook)
    update_verification_session(supabase, session_id, {"call_sid": call_id})
    print("SESSION_ID", session_id)
    print("CALL_ID", call_id)
    print("WEBHOOK", webhook)


if __name__ == "__main__":
    main()
