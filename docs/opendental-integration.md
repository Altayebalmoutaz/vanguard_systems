# OpenDental Integration (Eligibility Agent)

This integration adds `POST /eligibility-agent/eligibility/from-opendental` so the
eligibility service can pull demographics + insurance from OpenDental, run the
existing Stedi pipeline, and optionally write verification status back to OpenDental.

## API Modes

- Local: `http://localhost:30222/api/v1` (inside `opendental.exe`)
- Service: `http://localhost:30223/api/v1` (Windows service)
- Remote: `https://api.opendental.com/api/v1`

Auth header is the same in all modes:

`Authorization: ODFHIR {DeveloperKey}/{CustomerKey}`

## Route Contract

Endpoint:

`POST /eligibility-agent/eligibility/from-opendental`

Body:

```json
{
  "pat_num": 1,
  "trigger_event": "PRE_APPOINTMENT",
  "cdt_codes": ["D1110"],
  "practice_id": "vgd_mock_brooklyn",
  "rendering_provider_npi": "1104023674",
  "write_back": false
}
```

Flow:

1. `GET /patients/{PatNum}`
2. `GET /familymodules/{PatNum}/Insurance`
3. `GET /carriers/{CarrierNum}` (for each distinct carrier)
4. map to `EligibilityRequest` and call existing `run_eligibility_check_endpoint()`
5. optional write-back: `PUT /insverifies` (`VerifyType=PatientEnrollment`, `FKey=PatPlanNum`)

## Environment Variables

Add to `.env`:

```env
OPENDENTAL_BASE_URL=http://localhost:30222/api/v1
OPENDENTAL_DEVELOPER_KEY=
OPENDENTAL_CUSTOMER_KEY=
OPENDENTAL_TIMEOUT_SECONDS=15
OPENDENTAL_WRITEBACK_ENABLED=false
# Optional replay mode
# OPENDENTAL_REPLAY_DIR=tests/fixtures/opendental
```

## Replay Mode

When `OPENDENTAL_REPLAY_DIR` is set, OpenDental reads fixture JSON from disk instead
of issuing HTTP requests. This is useful for deterministic tests and demo fallback.

Capture fixtures:

```bash
python scripts/freeze_od_responses.py --pat-nums 1 2 3
```

## Demo Runner

```bash
python scripts/demo_opendental_eligibility.py --pat-nums 1 2 3 --write-back
```

The script prints: `pat_num | routing | check_id | insverify_num`.
