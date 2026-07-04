"""Reproduce the exact Bland send-call request for the test session and print the error body."""

from __future__ import annotations

import json

import httpx

from app.eligibility.config import get_settings
from app.eligibility.voice.bland import (
    BLAND_ANALYSIS_SCHEMA,
    _bland_context,
    _pathway_request_data,
)
from app.eligibility.voice.db import (
    fetch_payer_voice_config,
    fetch_session_by_id,
    get_supabase_client,
)

SESSION_ID = "4dd10072-1658-490d-9ff7-2ceebe7a7638"


def main() -> None:
    settings = get_settings()
    supabase = get_supabase_client(settings)
    session = fetch_session_by_id(supabase, SESSION_ID)
    payer_cfg = fetch_payer_voice_config(supabase, str(session["payer_id"]))
    ctx = _bland_context(session, settings, payer_cfg)

    payload = {
        "phone_number": str(payer_cfg["eligibility_phone"]),
        "webhook": "https://haltless-royal-enjoyably.ngrok-free.dev/eligibility-agent/eligibility/voice/bland/"
        + SESSION_ID,
        "metadata": {"session_id": SESSION_ID},
        "analysis_schema": BLAND_ANALYSIS_SCHEMA,
        "wait_for_greeting": True,
        "record": False,
        "pathway_id": settings.bland_pathway_id.strip(),
        "request_data": _pathway_request_data(ctx),
    }
    print("PAYLOAD:\n", json.dumps(payload, indent=2))

    headers = {"authorization": settings.bland_api_key.strip(), "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            settings.bland_base_url.rstrip("/") + "/v1/calls", json=payload, headers=headers
        )
    print("\nSTATUS", resp.status_code)
    print("BODY", resp.text[:3000])


if __name__ == "__main__":
    main()
