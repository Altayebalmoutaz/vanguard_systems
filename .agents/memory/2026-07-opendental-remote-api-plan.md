# OpenDental Remote API — plan note (July 2026)

## Product decision (record)

**Full writeback from day one.** Do not default to shadow-mode / read-only OD pilot. Enable complete OD writeback on launch:

- Per connection: `writeback_enabled: true`, `writeback_full: true` (notes, commlog, subscriber note, insadjust, benefits grid per flags)
- Global: `OPENDENTAL_WRITEBACK_ENABLED=true`
- **Do not** rely on `PILOT_SHADOW_MODE=1` as the rollout path for OD

Shadow mode remains in code as an emergency kill-switch only, not the planned go-live posture.

---

## Plan corrections (vs older docs / AgentMemory)

| Topic | Correction |
|-------|------------|
| Multi-clinic OD | **Already built** — `rcm.opendental_connections`, `customer_key_ref` → env var, dashboard OD page. Old note “multi-tenant OD creds not built” is outdated. |
| Primary poll path | **v1:** `run_connection_poll` → `from_connection` → `enqueue_od_eligibility_check` → `eligibility_requests` → pipeline worker → Stedi → `maybe_enqueue_od_writeback`. Does **not** use `run_from_opendental`. |
| `run_from_opendental` gap | Still uses `OpenDentalClient.from_settings()` — only affects `POST /eligibility-agent/eligibility/from-opendental` and legacy thinking. Small fix recommended; **not** blocking poll/queue testing. |
| Database | Pilot uses **Supabase** `DATABASE_URL` (same `rcm.*` schema; migrations in `neon/migrations/`). |
| Auto-poll env | `OPENDENTAL_AUTO_POLL_ENABLED` optional. **Poll now** works via pipeline job without background poller. |
| Local API | `localhost:30222` = single-clinic fallback only. **GCP / other clinics = Remote API** `https://api.opendental.com/api/v1` + eConnector. |
| Run target | Use **`uvicorn main:app`** (full app: poller, pipeline, voice, dashboard BFF backend). Not eligibility-only sub-app for production. |

---

## Simple “plug any OD” model (keep this)

```text
Dashboard OD page → PUT connection
  → rcm.opendental_connections (no secrets in DB)
  → OPENDENTAL_DEVELOPER_KEY + OD_CUSTOMER_KEY_<SLUG> in env / Secret Manager
  → OpenDentalClient.from_connection()
  → Remote API (api.opendental.com)
```

Per new clinic: add Customer Key env var + upsert one connection row + dashboard toggles. No agent rewrite.

---

## Prerequisites (Remote API)

- Developer Key (global)
- Customer Key per clinic (~$30/clinic) with write permissions for full writeback: InsVerify, InsSub, CommLog; **Insurance** tier for benefits grid
- Clinic: eConnector running and registered
- Probe: `GET /covcats` with `Authorization: ODFHIR {DeveloperKey}/{CustomerKey}`

---

## Go-live verification ladder (full writeback)

1. **Dashboard Test connection** → `ok` + covcats
2. **Register connection** with `writeback_enabled: true`, `writeback_full: true`
3. **Poll now** (or enable `poll_enabled`) → eligibility requests appear
4. **Confirm writeback** after Stedi completes — InsVerify / notes / commlog (and grid if enabled) in OD
5. Optional: `POST .../from-opendental` with `write_back: true` for one-off pat_num tests

---

## Recommended env (full writeback)

```env
OPENDENTAL_DEVELOPER_KEY=...
OPENDENTAL_BASE_URL=https://api.opendental.com/api/v1
OD_CUSTOMER_KEY_<CLINIC>=...
OPENDENTAL_WRITEBACK_ENABLED=true
OPENDENTAL_WRITE_BENEFIT_NOTES_ENABLED=true
OPENDENTAL_WRITE_SUBSCRIBER_NOTE_ENABLED=true
OPENDENTAL_WRITE_COMMLOG_ENABLED=true
OPENDENTAL_WRITE_INSADJUST_ENABLED=true
OPENDENTAL_WRITE_BENEFITS_GRID_ENABLED=true
OPENDENTAL_AUTO_POLL_ENABLED=false   # or true when ready for background poll
OPENDENTAL_REPLAY_DIR=               # unset for live API
PILOT_SHADOW_MODE=false              # not the rollout path
```

Connection row example:

```json
{
  "display_name": "Brooklyn",
  "base_url": "https://api.opendental.com/api/v1",
  "customer_key_ref": "OD_CUSTOMER_KEY_VGD_BROOKLYN",
  "poll_enabled": false,
  "writeback_enabled": true,
  "writeback_full": true,
  "cdt_codes": "D1110"
}
```

---

## Out of scope (unchanged)

- GCP deploy mechanics (same connection model; secrets in Secret Manager)
- Stedi payer enrollments (separate from OD connectivity)
- Optional: dashboard “Add connection” UI when list empty
- Optional code: `run_from_opendental` → `from_connection` when `practice_id` set

## Update — InsHist writeback (Aug 2026)

`canonical.last_service_dates` write to OD Insurance History via
`POST /procedurelogs/InsuranceHistory`. Enabled with `writeback_full` or
`OPENDENTAL_WRITE_INSHIST_ENABLED`. Older notes that OD has no InsHist API are outdated.
