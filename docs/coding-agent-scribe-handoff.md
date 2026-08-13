# Vanguard Coding Agent — Scribe Team Integration (v1)

Pilot / test environment. Structured chart lines in → CDT suggestions out, for real-time dentist review in your UI.

## How it works

Synchronous request/response. No webhook. You own encounters; we only suggest codes and record what the dentist did.

```
chart lines  →  POST /v1/suggest  →  dentist reviews CDTs  →  POST /v1/decision
```

1. Scribe finishes structured lines in your UI.
2. You `POST /v1/suggest` (fresh `request_id` per encounter). Same id = same saved result.
3. We suggest a CDT per line, check it against the CDT catalog, and return it for review. Always render `recommendations[]` — even when `status` is `needs_info`.
4. Dentist approve / edit / reject in your UI.
5. At sign-off, `POST /v1/decision` with what they actually billed. Required for accuracy measurement.

Chairside coding does **not** apply payer bundling/downcodes (that is downstream RCM). `fast: true` keeps routine visits snappy; non-routine lines may still use CDT retrieval.

## Connection

| Item | Value |
| --- | --- |
| Base URL | `https://ezfi.smilesuite.ai/coding-agent` |
| Suggest | `POST /v1/suggest` |
| Health | `GET /health` (no auth) |
| OpenAPI UI | `https://ezfi.smilesuite.ai/coding-agent/docs` |
| OpenAPI JSON | `https://ezfi.smilesuite.ai/coding-agent/openapi.json` |
| Auth | `Authorization: Bearer <CODING_AGENT_API_KEY>` |
| Contract | Synchronous JSON in / JSON out (no webhook callback) |

Ask your Vanguard contact for the current **Bearer API key**. Do not commit it to source control or put it in client-side browser code.

## Quick test

```bash
# Liveness (public)
curl -fsS https://ezfi.smilesuite.ai/coding-agent/health

# Suggest (replace KEY and mint a fresh request_id UUID each new encounter)
curl -fsS https://ezfi.smilesuite.ai/coding-agent/v1/suggest \
  -H "Authorization: Bearer KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "request_id": "11111111-1111-1111-1111-111111111111",
    "practice_id": "your_practice_id",
    "patient_id": "your_patient_id",
    "provider_id": "your_provider_id",
    "encounter_datetime": "2026-08-06T14:30:00-04:00",
    "payer": { "id": "62308", "name": "Cigna" },
    "patient": { "age": 42 },
    "procedures": [
      {
        "line_id": "1",
        "tooth_numbers": ["14"],
        "surfaces": ["M", "O"],
        "findings": ["interproximal caries"],
        "planned_or_performed": "planned"
      }
    ],
    "supporting_note": "Bitewings show interproximal caries on #14 MO.",
    "attachments_present": ["bitewing_radiograph"],
    "fast": true
  }'
```

Without a valid Bearer token you should receive **HTTP 401**.

## Request fields

| Field | Required | Notes |
| --- | --- | --- |
| `schema_version` | no | Default `"1.0"` |
| `request_id` | **yes** | UUID. Idempotency key **per** `practice_id`. Retry with the same id returns the same result (`idempotent_replay: true`). |
| `practice_id` | **yes** | Your tenant id (opaque string) |
| `patient_id` | **yes** | Your patient / chart id (opaque) |
| `provider_id` | **yes** | Your provider id (opaque) |
| `encounter_datetime` | **yes** | ISO-8601 |
| `payer.id` / `payer.name` | recommended | Context for the model; chairside does not adjudicate payer policy |
| `patient.age` | recommended | Clinical context for the model |
| `procedures[]` | **yes** (≥1) | One object per chart line |
| `procedures[].line_id` | **yes** | Unique within the request |
| `procedures[].tooth_numbers` | no | e.g. `["14"]`. Needed on SRP lines to choose D4341 (4+) vs D4342 (1–3). |
| `procedures[].surfaces` | no | e.g. `["M","O"]` |
| `procedures[].quadrant` | no | `UR` \| `UL` \| `LR` \| `LL`. Additive; does not bump `schema_version`. |
| `procedures[].arch` | no | `maxillary` \| `mandibular` \| `full_mouth` |
| `procedures[].findings` | no | Clinical findings. `quadrant: UR` in findings still works if the field is omitted. |
| `procedures[].planned_or_performed` | no | `planned` \| `performed` \| `unknown` |
| `supporting_note` | no | Optional free text |
| `attachments_present` | no | Radiograph aliases: `full_mouth_series`, `fmx`, `bitewing_radiograph`, `periapical_radiograph`. `periodontal_chart` is not a radiograph. |
| `fast` | no | Prefer `true` chairside. Skips retrieval on routine visits; non-routine may still retrieve |

**Field ownership:** you mint `request_id` and own `practice_id` / `patient_id` / `provider_id`. Coding does not create encounters in your system.

## Response fields

| Field | Notes |
| --- | --- |
| `coding_run_id` | Our persisted run id |
| `status` | `pending_review` (ready for dentist) or `needs_info` (gaps to fill) |
| `recommendations[]` | One per input `line_id` |
| `recommendations[].cdt_code` | Suggested CDT or `null` |
| `recommendations[].cdt_description` | When available from reference data |
| `recommendations[].confidence` | 0.0–1.0 (calibrated when a calibration map is fit) |
| `recommendations[].autonomy` | `auto` (one-click accept), `review` (quick confirm), or `ask` (resolve gap first) |
| `recommendations[].explanation` | Short clinical rationale for the dentist |
| `recommendations[].icd10_codes` | ICD-10-CM suggestions for that line |
| `recommendations[].required_supporting_documentation` | Docs typically expected for that CDT |
| `recommendations[].missing_info[]` | `{ "code", "message" }` machine-readable gaps |
| `global_missing_info[]` | Request-level gaps |
| `warnings[]` | Non-blocking diagnostics |
| `overall_confidence` | Aggregate 0.0–1.0 |
| `idempotent_replay` | `true` if this was a cached replay of the same `request_id` |

### `missing_info.code` values

`TOOTH_MISSING` · `SURFACE_MISSING` · `FINDING_MISSING` · `RADIOGRAPH_MISSING` · `PAYER_MISSING` · `AGE_MISSING` · `PROCEDURE_EMPTY` · `SUPPORTING_NOTE_THIN` · `CDT_UNCERTAIN` · `OTHER`

## Integration flow (recommended)

1. Dentist / scribe finishes structured lines in your UI.
2. You `POST /v1/suggest` with a new `request_id`.
3. **Always render `recommendations[]`, regardless of `status`.** `needs_info` means "prompts are available", not "no usable codes" — the suggested CDTs are still there for review. Use `autonomy` (`auto` / `review` / `ask`) to style the line.
4. If `status` is `needs_info`, prompt using `missing_info[].code` + `message`. Only **blocking** gaps (`TOOTH_MISSING`, `SURFACE_MISSING`, `FINDING_MISSING`, `PROCEDURE_EMPTY`, `CDT_UNCERTAIN`) set `needs_info`; the rest (`PAYER_MISSING`, `AGE_MISSING`, `SUPPORTING_NOTE_THIN`, `RADIOGRAPH_MISSING`) are advisory and keep `status = pending_review`.
5. Keep `coding_run_id` on your side for support / audit correlation.
6. **At dentist sign-off, `POST /v1/decision`** with what the dentist actually did per line (see below). This is required for accuracy measurement.

Typical interactive latency target: under ~25s (LLM budget). Prefer `"fast": true` in the dentist chair.

## Decision write-back (`POST /v1/decision`)

Report what the dentist did with each suggested line so we can measure and improve CDT top-1 accuracy. Send one call per run at sign-off.

```bash
curl -fsS https://ezfi.smilesuite.ai/coding-agent/v1/decision \
  -H "Authorization: Bearer KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "practice_id": "your_practice_id",
    "coding_run_id": "<coding_run_id from /v1/suggest>",
    "request_id": "<the suggest request_id (optional)>",
    "decided_by": "dr_smith",
    "decisions": [
      { "line_id": "1", "action": "approved", "final_cdt": "D2392" },
      { "line_id": "2", "action": "edited", "suggested_cdt": "D2740", "final_cdt": "D2750", "edit_reason": "PFM not all-ceramic" }
    ]
  }'
```

| Field | Required | Notes |
| --- | --- | --- |
| `practice_id` | **yes** | Your tenant id |
| `coding_run_id` | **yes** | From the `/v1/suggest` response you are grading |
| `request_id` | no | The suggest `request_id`, for correlation |
| `decided_by` | no | Dentist/user id (opaque) |
| `decisions[]` | **yes** (≥1) | One object per line |
| `decisions[].line_id` | **yes** | Must match the suggested line |
| `decisions[].action` | **yes** | `approved` \| `edited` \| `rejected` \| `added` |
| `decisions[].suggested_cdt` | no | We backfill from the run if omitted |
| `decisions[].final_cdt` | no | The code actually billed (null if rejected) |
| `decisions[].edit_reason` | no | Short free text (why it changed) |

Response: `{ "coding_run_id", "recorded": <count>, "status": "recorded" }`.

## Errors

| HTTP | Meaning |
| --- | --- |
| 401 | Missing / invalid Bearer token |
| 422 | Request validation failed (bad/missing fields) |
| 500 | Server-side failure (retry with same `request_id` for idempotent safety) |

## Support

- Contract questions / schema changes: reply in the shared channel with `request_id` + `coding_run_id`.
- Do not send PHI in email/chat beyond what you already send to the API.
- This pilot is for **synthetic / non-PHI or approved test data** until your BAA / clinic go-live checklist is complete.

## Changelog

- **v1.4** — Additive `procedures[].quadrant` (`UR`/`UL`/`LR`/`LL`) and `procedures[].arch` (`maxillary`/`mandibular`/`full_mouth`). SRP tooth-count chooses D4341 vs D4342; D4921 requires a quadrant. Findings token `quadrant: UR` still works.
- **v1.3** — Recognize `full_mouth_series` / `fmx` as radiographs; ignore anatomic "buccal mucosa" / furcation sites and existing-restoration narrative on crown lines for surface gaps; deterministic guards for missing crown material, D4346-as-laser, D4921 irrigation, and same-day D0150/D0180. Perio (`D43`) is high-stakes; D4346 always gets a verifier pass.
- **v1.2** — Document live chairside flow: suggest → dentist review → decision write-back. Payer adjudication is not part of this path. `fast` skips retrieval on routine visits only.
- **v1.1** — Add `POST /v1/decision` ground-truth write-back; split blocking vs advisory gaps (advisory gaps no longer force `needs_info`); crown/negated-recall false `needs_info` fixed via CDT documentation-requirement backfill.
- **v1.0** — Initial partner API: sync suggest, line-level CDTs, gap codes, idempotency, Bearer auth.
