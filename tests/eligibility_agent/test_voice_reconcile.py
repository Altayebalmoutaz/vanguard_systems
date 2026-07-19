"""Unit tests for voice reconciliation merge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import app.eligibility.db as elig_db
from app.eligibility.voice import bland
from app.eligibility.voice import reconcile as rec
from app.eligibility.voice import worker
from app.eligibility.voice.bland import _build_task_prompt
from app.eligibility.voice.reconcile import merge_voice_extraction


def _base_canonical() -> dict:
    return {
        "payer_id": "84103",
        "checked_at": datetime.now(UTC),
        "is_active": True,
        "is_covered": None,
        "missing_fields": ["annual_max_remaining", "deductible_remaining"],
        "response_complete": False,
        "procedure_details": [{"cdt_code": "D2740", "procedure_covered": None}],
        "integrity_warnings": [],
        "normalization_version": "1.0",
    }


def test_merge_voice_extraction_fills_fields() -> None:
    extracted = {
        "annual_max_remaining": 1500.0,
        "deductible_remaining": 50.0,
        "is_covered": True,
        "call_reference": "REF-123",
        "procedure_details": [{"cdt_code": "D2740", "procedure_covered": True}],
    }
    patched = merge_voice_extraction(
        _base_canonical(),
        extracted,
        session_id="sess-1",
        call_reference="REF-123",
    )
    assert patched["annual_max_remaining"] == 1500.0
    assert patched["deductible_remaining"] == 50.0
    assert patched["is_covered"] is True
    assert patched["voice_verification"]["session_id"] == "sess-1"
    assert patched["procedure_details"][0]["procedure_covered"] is True
    assert patched["response_complete"] is True
    assert patched["missing_fields"] == []


def test_voice_recovery_complete() -> None:
    from app.eligibility.voice.reconcile import voice_recovery_complete

    patched = merge_voice_extraction(
        _base_canonical(),
        {
            "annual_max_remaining": 1500.0,
            "deductible_remaining": 50.0,
            "is_covered": True,
        },
        session_id="sess-1",
    )
    assert voice_recovery_complete(patched) is True
    inactive = merge_voice_extraction(
        {**_base_canonical(), "is_active": False},
        {"annual_max_remaining": 1500.0},
        session_id="sess-2",
    )
    assert voice_recovery_complete(inactive) is False


# --- practice_id threading (webhook 500 regression) -------------------------


def _bland_settings(**overrides) -> SimpleNamespace:
    base = dict(
        bland_api_key="key",
        bland_base_url="https://api.bland.ai",
        bland_pathway_id="pw-123",
        bland_pathway_version="",
        bland_use_pathway=False,
        bland_model="base",
        bland_voice="june",
        bland_temperature=0.7,
        bland_interruption_threshold=120,
        bland_record=False,
        provider_npi="1999999984",
        provider_name="One",
        provider_tax_id="123456789",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_complete_reconciliation_threads_practice_id(monkeypatch) -> None:
    """The reconcile chain must pass the session's practice_id into every PHI write.

    This is the regression for the webhook HTTP 500 (missing practice_id on the
    Supabase Postgres / RLS path).
    """
    session = {
        "id": "sess-1",
        "practice_id": "vgd_mock_brooklyn",
        "eligibility_check_id": "22222222-2222-4222-8222-000000000001",
        "request_id": None,
        "extracted_fields": None,
    }
    base_check = {
        "id": "22222222-2222-4222-8222-000000000001",
        "patient_id": "00000000-0000-4000-8000-000000000003",
        "payer_id": "62308",
        "practice_id": "vgd_mock_brooklyn",
        "is_active": True,
        "is_covered": None,
    }
    calls: dict = {"updates": []}

    monkeypatch.setattr(rec, "get_supabase", lambda s=None: object())
    monkeypatch.setattr(
        rec,
        "fetch_session_by_id",
        lambda supabase, sid, practice_id=None, settings=None: session,
    )
    monkeypatch.setattr(
        elig_db,
        "get_eligibility_check_by_id",
        lambda supabase, cid, practice_id=None, settings=None: {
            **base_check,
            "_pid": practice_id,
        },
    )
    monkeypatch.setattr(
        rec,
        "merge_voice_extraction",
        lambda base, extracted, session_id=None, call_reference=None: {
            "routing_status": "PENDING_VOICE_REVIEW",
            "missing_fields": ["is_covered"],
            "response_complete": False,
            "is_active": True,
            "payer_id": "62308",
        },
    )
    monkeypatch.setattr(
        rec, "route", lambda canonical, supabase: {"status": "PENDING_VOICE_REVIEW"}
    )
    monkeypatch.setattr(
        rec,
        "canonical_to_row",
        lambda patient_id, canonical, **kwargs: {
            "patient_id": str(patient_id),
            "payer_id": "62308",
        },
    )

    def _fake_insert(supabase, row, practice_id=None, settings=None):
        calls["insert_pid"] = practice_id
        calls["row_pid"] = row.get("practice_id")
        return uuid.UUID("11111111-1111-4111-8111-111111111111")

    monkeypatch.setattr(rec, "insert_eligibility_check", _fake_insert)

    def _fake_update(supabase, sid, values, practice_id=None, settings=None):
        calls["updates"].append(practice_id)

    monkeypatch.setattr(rec, "update_verification_session", _fake_update)

    settings = _bland_settings(voice_auto_approve_when_complete=False)
    result = rec.complete_voice_session_reconciliation(
        "sess-1",
        transcript="hello",
        extracted={"is_covered": True},
        settings=settings,
        practice_id=None,
    )

    assert result["status"] == "pending_review"
    assert calls["insert_pid"] == "vgd_mock_brooklyn"
    assert calls["row_pid"] == "vgd_mock_brooklyn"
    assert "vgd_mock_brooklyn" in calls["updates"]


# --- humanized persona + prompt/pathway toggle ------------------------------


def test_build_task_prompt_is_human_and_uses_real_member() -> None:
    ctx = {
        "member_id": "U3141592653",
        "dob": "1996-05-05",
        "payer_name": "Cigna",
        "npi": "1999999984",
        "requested_benefits": "verify whether the requested dental services are covered",
        "cdt_codes": ["D1110"],
    }
    prompt = _build_task_prompt(ctx)
    assert "ONE question at a time" in prompt
    assert "John Doe" not in prompt
    assert "2653" in prompt  # real member id tail, not a placeholder
    assert "1996-05-05" in prompt
    assert "Cigna" in prompt


def _patch_bland_io(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(bland, "get_supabase_client", lambda settings=None: object())
    monkeypatch.setattr(
        bland,
        "fetch_payer_voice_config",
        lambda supabase, payer_id: {
            "eligibility_phone": "+12082749734",
            "display_name": "Cigna",
        },
    )
    def _fetch_request(supabase, req_id, *, practice_id=None, settings=None):
        captured["request_practice_id"] = practice_id
        captured["request_settings"] = settings
        return {
            "subscriber_id": "U3141592653",
            "dob": "1996-05-05",
            "first_name": "Jaguar",
            "last_name": "Dent",
            "plan_id": "",
            "provider_name": "One",
        }

    monkeypatch.setattr(bland, "fetch_eligibility_request", _fetch_request)

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"call_id": "call-1"}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(bland.httpx, "Client", _FakeClient)


def _voice_session() -> dict:
    return {
        "id": "sess-1",
        "payer_id": "62308",
        "practice_id": "vgd_mock_brooklyn",
        "request_id": "req-1",
        "missing_fields_target": ["is_covered"],
        "cdt_codes": ["D1110"],
    }


def test_initiate_bland_call_prompt_mode(monkeypatch) -> None:
    captured: dict = {}
    _patch_bland_io(monkeypatch, captured)

    call_id = bland.initiate_bland_call(
        _voice_session(),
        _bland_settings(bland_use_pathway=False),
        webhook_url="https://ezfi.smilesuite.ai/eligibility-agent/eligibility/voice/bland/sess-1",
    )
    assert call_id == "call-1"
    payload = captured["payload"]
    assert "task" in payload
    assert "pathway_id" not in payload
    assert payload["voice"] == "june"
    assert payload["model"] == "base"
    assert payload["temperature"] == 0.7
    assert payload["interruption_threshold"] == 120
    assert "John Doe" not in payload["task"]
    assert "2653" in payload["task"]
    assert captured["request_practice_id"] == "vgd_mock_brooklyn"
    assert captured["request_settings"] is not None


def test_initiate_bland_call_pathway_mode(monkeypatch) -> None:
    captured: dict = {}
    _patch_bland_io(monkeypatch, captured)

    bland.initiate_bland_call(
        _voice_session(),
        _bland_settings(bland_use_pathway=True),
        webhook_url="https://ezfi.smilesuite.ai/eligibility-agent/eligibility/voice/bland/sess-1",
    )
    payload = captured["payload"]
    assert payload["pathway_id"] == "pw-123"
    assert "request_data" in payload
    assert "task" not in payload
    # Real patient data is still passed as pathway variables.
    assert payload["request_data"]["patient_name"] == "Jaguar Dent"
    assert payload["request_data"]["member_id"] == "U3141592653"


def test_run_voice_sweep_threads_practice_id_through_neon_writes(monkeypatch) -> None:
    settings = _bland_settings(
        voice_verification_worker_enabled=True,
        voice_verification_enabled=True,
        voice_verification_batch_size=5,
        voice_call_provider="bland",
        voice_demo_auto_complete=False,
        voice_demo_transcript="",
        twilio_webhook_base_url="https://example.test/eligibility-agent",
    )
    session = _voice_session()
    calls: dict[str, list] = {"updates": [], "events": []}

    monkeypatch.setattr(worker, "voice_infra_ready", lambda settings: True)
    monkeypatch.setattr(worker, "bland_configured", lambda settings: True)
    monkeypatch.setattr(worker, "get_supabase_client", lambda settings=None: object())

    def _fetch_queued(supabase, *, limit, settings=None):
        calls["queue_settings"] = [settings]
        return [session]

    monkeypatch.setattr(worker, "fetch_queued_sessions", _fetch_queued)
    monkeypatch.setattr(
        worker,
        "get_eligibility_agent_settings",
        lambda supabase, *, practice_id=None, settings=None: {
            "voice_verification_enabled": True
        },
    )

    def _update(supabase, session_id, values, *, practice_id=None, settings=None):
        assert practice_id == "vgd_mock_brooklyn"
        assert settings is not None
        calls["updates"].append(values)

    monkeypatch.setattr(worker, "update_verification_session", _update)
    monkeypatch.setattr(worker, "initiate_bland_call", lambda *args, **kwargs: "call-1")

    def _event(
        supabase,
        request_id,
        event_type,
        detail=None,
        *,
        practice_id=None,
        settings=None,
    ):
        assert practice_id == "vgd_mock_brooklyn"
        assert settings is not None
        calls["events"].append(event_type)

    monkeypatch.setattr(worker, "insert_eligibility_request_event", _event)

    result = worker.run_voice_sweep(settings)

    assert result == {
        "started": 1,
        "errors": 0,
        "considered": 1,
        "skipped_disabled": 0,
    }
    assert calls["queue_settings"] == [settings]
    assert calls["updates"] == [
        {"status": "calling", "call_provider": "bland"},
        {"call_sid": "call-1"},
    ]
    assert calls["events"] == ["voice_verification_calling"]
