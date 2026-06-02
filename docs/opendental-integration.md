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
5. optional write-back (when `write_back=true` and `OPENDENTAL_WRITEBACK_ENABLED=true`)

## Write-back targets and order

The OD REST API cannot write the structured Benefits grid (per-category coverage %, deductible,
annual-max rows) — that grid is derived UI, populated only by OD's clearinghouse 271 Import. The
agent therefore writes to the supported endpoints, in this order, each step independently
flag-gated and fault-isolated (one failure never aborts the rest):

1. **`PUT /inssubs/{InsSubNum}`** — `BenefitNotes` field (primary persistent storage). Holds a
   deterministic, ASCII-only eligibility snapshot. Gate: `OPENDENTAL_WRITE_BENEFIT_NOTES_ENABLED`.
1b. **`PUT /inssubs/{InsSubNum}`** — `SubscNote` field. A one-line summary that renders **bold red
   directly on the insurance grid** (the most visible note spot). Persists reliably (unlike the
   InsVerify `Note`). Gate: `OPENDENTAL_WRITE_SUBSCRIBER_NOTE_ENABLED` (on by default; uses the same
   `InsSubs PUT` permission as `BenefitNotes`).
2. **`PUT /insverifies`** — `PatientEnrollment` (`FKey=PatPlanNum`) + `InsuranceBenefit`
   (`FKey=PlanNum`); sets the "Eligibility/Benefits Last Verified" dates + a Note (audit trail).
3. **`POST /commlogs`** — human-readable summary for the front desk (visibility only).
   Gate: `OPENDENTAL_WRITE_COMMLOG_ENABLED`.
4. **`PUT /claimprocs/InsAdjust`** — Phase 2 only; pushes deductible/annual-max *used* so the
   plan's remaining balances update. Off by default (`OPENDENTAL_WRITE_INSADJUST_ENABLED`) because
   it alters financial running totals.
5. **`POST` / `PUT /benefits`** — structured **Benefits grid** (the per-category coverage %,
   General Deductible, and Annual Max rows shown in the *Benefit Information* area of the Edit
   Insurance Plan window). Off by default (`OPENDENTAL_WRITE_BENEFITS_GRID_ENABLED`) because it
   mutates plan-level benefits shared by every subscriber on the plan — the same effect as OD's
   own "Import Benefits" from a 271.

### Benefits grid write-back (step 5)

This is the structured alternative to the free-text `BenefitNotes` field: it writes real
`benefit` rows so the values appear in the Benefit Information grid itself (not behind the
*Notes* button).

- `GET /covcats` resolves `EbenefitCat → CovCatNum` live (numbers differ per database).
- `GET /benefits?PlanNum={PlanNum}` loads existing rows; the write is an **idempotent upsert**:
  an existing row whose value already matches is left **unchanged**, a changed value is **PUT**,
  and a missing row is **POST**-created. Each row is fault-isolated.
- Mapping from the normalized 271 (`universal_dental_record.categories` + `canonical`):
  - `CoInsurance` `Percent` per category bucket — `DIAGNOSTIC → Diagnostic/X-Ray/Preventive`,
    `BASIC → Restorative/Endo/Perio/OralSurgery/Adjunctive`, `MAJOR → Crowns/Prosth/MaxProsth`,
    `ORTHO → Orthodontics`. `Percent = 100 - patient_coinsurance_pct`.
  - `Limitations` `MonetaryAmt = annual_max_total` and `Deductible` `MonetaryAmt = deductible_total`,
    both against the **General** category (`EbenefitCat="General"`). Written only when present in
    the 271.

> **Required OD permission.** `Benefits POST/PUT/DELETE` belong to the **Insurance** API
> permission tier (see Open Dental's [API Permissions](https://opendental.com/site/apipermissions.html)),
> which must be enabled on your Customer Key in the Open Dental Developer Portal (a paid API
> module). If the key lacks it, these calls return `401 "Not Authorized"` while `GET /benefits`
> and the other write-back steps still succeed; the grid step then logs per-row errors and is
> skipped without aborting the rest. `BenefitNotes`, `InsVerifies`, and `CommLog` use permissions
> a standard key already has.

The route response includes `opendental.write_back_notes` with per-step results
(`benefit_notes`, `subscriber_note`, `insverifies`, `commlog`, `insadjust`, `benefits_grid`) and
`note_sent` text. The `InsVerify` `Note` comes back empty from OD even on success (date is the
reliable signal), but `BenefitNotes` and `SubscNote` persist; read `note_sent` for full content.

### BenefitNotes format (deterministic, ASCII only)

```
[ELIGIBILITY SNAPSHOT | STEDI]
Date: YYYY-MM-DD HH:MM
Plan: PPO - Carrier Name
Status: CLEARED

Deductible:
 - Total: $X
 - Remaining: $X

Annual Max:
 - Total: $X
 - Remaining: $X

Coverage:
 - D1110: 100%
 - D2740: 50%

Frequency:
 - n/a

Estimates:
 - Patient estimated responsibility: $XXX

Source: Stedi
Agent: eligibility-agent-v1
```

Fields not reliably present in the normalized 271 (frequency limits, deductible total in some
payers, plan type/name) render as `n/a` rather than being fabricated.

## Automatic poller (no manual script)

When `OPENDENTAL_AUTO_POLL_ENABLED=true`, the FastAPI app starts an in-process background task on
startup that polls `GET /appointments` across the configured date window and runs the same
`from-opendental` flow (with write-back) for each new patient. It replaces manually running
`scripts/watch_od_appointments.py`. The loop is idempotent — at most one eligibility run per
patient per day — enforced by an in-memory set plus a Supabase `eligibility_checks` timestamp
check (survives restarts). Stedi/OD failures are logged and never stop polling. The task is
cancelled cleanly on shutdown.

## Environment Variables

Add to `.env`:

```env
OPENDENTAL_BASE_URL=http://localhost:30222/api/v1
OPENDENTAL_DEVELOPER_KEY=
OPENDENTAL_CUSTOMER_KEY=
OPENDENTAL_TIMEOUT_SECONDS=15

# Master write-back switch + per-target gates
OPENDENTAL_WRITEBACK_ENABLED=false
OPENDENTAL_WRITE_BENEFIT_NOTES_ENABLED=true
OPENDENTAL_WRITE_SUBSCRIBER_NOTE_ENABLED=true
OPENDENTAL_WRITE_COMMLOG_ENABLED=true
OPENDENTAL_WRITE_INSADJUST_ENABLED=false
# Structured Benefits grid (needs the "Insurance" API permission on the Customer Key)
OPENDENTAL_WRITE_BENEFITS_GRID_ENABLED=false

# Automatic appointment poller (replaces manual watch_od_appointments.py)
OPENDENTAL_AUTO_POLL_ENABLED=false
OPENDENTAL_AUTO_POLL_INTERVAL_SECONDS=60
OPENDENTAL_AUTO_POLL_DATE_WINDOW_DAYS=0
OPENDENTAL_AUTO_POLL_CDT_CODES=D1110

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
