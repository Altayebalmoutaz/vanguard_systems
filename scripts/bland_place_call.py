"""Place a live Bland pathway call for the test session."""

from __future__ import annotations

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

SESSION_ID = "4dd10072-1658-490d-9ff7-2ceebe7a7638"


def main() -> None:
    reset_supabase_client()
    settings = get_settings()
    supabase = get_supabase_client(settings)
    session = fetch_session_by_id(supabase, SESSION_ID)
    if not session:
        raise SystemExit("session not found")
    payer = fetch_payer_voice_config(supabase, "84103")
    print("payer_phone", payer.get("eligibility_phone") if payer else None)

    update_verification_session(
        supabase, SESSION_ID, {"status": "calling", "call_provider": "bland"}
    )
    webhook = voice_webhook_url(settings, f"bland/{SESSION_ID}")
    call_id = initiate_bland_call(session, settings, webhook_url=webhook)
    update_verification_session(supabase, SESSION_ID, {"call_sid": call_id})
    print("CALL_ID", call_id)


if __name__ == "__main__":
    main()
