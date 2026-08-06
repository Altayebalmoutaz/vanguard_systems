# Vanguard Coding Agent — Scribe Team Integration (v1)

Pilot / test environment. Use for structured chart lines → CDT suggestions for real-time dentist review in your UI.

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
| `payer.id` / `payer.name` | recommended | Improves documentation / rule matching |
| `patient.age` | recommended | Age-gated payer rules |
| `procedures[]` | **yes** (≥1) | One object per chart line |
| `procedures[].line_id` | **yes** | Unique within the request |
| `procedures[].tooth_numbers` | no | e.g. `["14"]` |
| `procedures[].surfaces` | no | e.g. `["M","O"]` |
| `procedures[].findings` | no | Clinical findings |
| `procedures[].planned_or_performed` | no | `planned` \| `performed` \| `unknown` |
| `supporting_note` | no | Optional free text |
| `attachments_present` | no | e.g. `["bitewing_radiograph"]` |
| `fast` | no | `true` skips vector retrieval (lower latency for interactive UI) |

**Field ownership:** you mint `request_id` and own `practice_id` / `patient_id` / `provider_id`. Coding does not create encounters in your system.

## Response fields

| Field | Notes |
| --- | --- |
| `coding_run_id` | Our persisted run id |
| `status` | `pending_review` (ready for dentist) or `needs_info` (gaps to fill) |
| `recommendations[]` | One per input `line_id` |
| `recommendations[].cdt_code` | Suggested CDT or `null` |
| `recommendations[].cdt_description` | When available from reference data |
| `recommendations[].confidence` | 0.0–1.0 |
| `recommendations[].explanation` | Short clinical rationale for the dentist |
| `recommendations[].icd10_codes` | ICD-10-CM suggestions for that line |
| `recommendations[].required_supporting_documentation` | Docs expected for the code / payer |
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
3. Render `recommendations[]` for dentist approve / edit.
4. If `status` is `needs_info`, prompt using `missing_info[].code` + `message`, then call again with a **new** `request_id` (or same id only if you intend idempotent replay of the prior result).
5. Keep `coding_run_id` on your side for support / audit correlation.

Typical interactive latency target: under ~25s (LLM budget). Prefer `"fast": true` in the dentist chair unless you need retrieval enrichment.

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

- **v1.0** — Initial partner API: sync suggest, line-level CDTs, gap codes, idempotency, Bearer auth.
