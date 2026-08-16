# Host-filled scribe sample (`02-sample-suggest-request_1`)

Ready-to-POST payload for `POST /v1/suggest`. Clinical lines are unchanged from the scribe sample. Only host-owned envelope fields were filled. `extraction_report` is **not** included — it must never go on the wire.

**Canonical JSON:** [`docs/fixtures/coding-suggest-scribe-sample-02b-host-filled.json`](fixtures/coding-suggest-scribe-sample-02b-host-filled.json)

## What was filled

| Field | Source sample | Host-filled value | Why |
|---|---|---|---|
| `request_id` | `<<HOST:request_id>>` | `a1b2c3d4-e5f6-4789-a012-3456789abcde` | Required UUID. Idempotency key; mint a **new** UUID per live encounter. |
| `practice_id` | `<<HOST:practice_id>>` | `vgd_mock_brooklyn` | Required. Pilot tenant. |
| `patient_id` | `<<HOST:patient_id>>` | `pat_scribe_sample_02b` | Required. Opaque chart id (not a name). |
| `provider_id` | `<<HOST:provider_id>>` | `prov_scribe_sample_02b` | Required. Opaque rendering-provider id. |
| `encounter_datetime` | `<<HOST:encounter_datetime>>` | `2026-08-11T09:00:00-05:00` | Required ISO-8601 with offset. Date from the sample’s own host hint. |
| `payer.id` | `<<HOST:payer.id>>` | `62308` | Pilot Cigna trading-partner id (seeded for `vgd_mock_brooklyn`). |
| `payer.name` | `<<HOST:payer.name>>` | `Cigna` | Pilot plan name. Not in the spoken note; host-injected. |

Unchanged from the sample: `patient.age` (62), all 14 procedure lines (A–N), `supporting_note`, `attachments_present`, `fast: false`.

A full copy of the original envelope (filled `suggest_request`, `extraction_report` kept for scribe internals) is at `Downloads/Telegram Desktop/02-sample-suggest-request_1-host-filled.json`. **Do not POST `extraction_report`.**

## Not sent to the API

- Any `<<HOST:…>>` sentinel
- `extraction_report` (scribe-internal)

## Live call

```bash
curl -fsS https://ezfi.smilesuite.ai/coding-agent/v1/suggest \
  -H "Authorization: Bearer KEY" \
  -H "Content-Type: application/json" \
  --data-binary @docs/fixtures/coding-suggest-scribe-sample-02b-host-filled.json
```

Use a fresh `request_id` for each live run if you need a new result instead of an idempotent replay.

## Live run (2026-08-13, fully host-filled including payer)

HTTP 200 · `coding_run_id` `b4d454a3-2718-48ee-b291-f2be3cb0cf04` · model `overall_confidence` **0.57** (this is not accuracy — four `null` lines at 0.0 pull the average down).

`PAYER_MISSING` is gone. Encounter `status` is still **`needs_info`** because A/D/F/M have no CDT.

| Line | Code | Autonomy | Verdict |
|---|---|---|---|
| A exam | `null` | ask | Correct — exclusive with D0180 |
| B FMX | **D0210** | review | Correct |
| C perio eval | **D0180** | review | Correct |
| D rinse | `null` | ask | Correct — no CDT |
| E OHI | **D1330** | review | Correct |
| F PPE | `null` | ask | Correct — no CDT |
| G #15 crown | **D2740** | ask | Acceptable default; material not spoken |
| H #30 porcelain crown | **D2740** | ask | Correct code (porcelain was spoken) |
| I–L SRP quads | **D4341** ×4 | review | Correct for quadrant charting |
| M laser | `null` | ask | Correct — not D4346 |
| N irrigation | **D4921** | review | Correct |

**Line-level accuracy: 14/14 acceptable for chairside** (13/14 if G is counted as a guess rather than a unique gold code).

