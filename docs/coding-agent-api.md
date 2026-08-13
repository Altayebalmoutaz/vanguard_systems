# Coding Agent API (v1) — Scribe integration

Solo, versioned coding agent for real-time dentist approval in the scribe UI.

**Base URL (full app):** `http://localhost:8000/coding-agent`  
**OpenAPI UI:** `http://localhost:8000/coding-agent/docs`  
**Auth:** `Authorization: Bearer <CODING_AGENT_API_KEY>` (optional when key is unset in local/dev)

## Endpoint

### `POST /v1/suggest`

Synchronous. Scribe sends structured clinical JSON; coding returns line-level CDT recommendations immediately (no callback).

#### Request

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | string | no | Default `1.0` |
| `request_id` | uuid | yes | Idempotency key (per practice) |
| `practice_id` | string | yes | Tenant |
| `patient_id` | string | yes | Scribe/external id |
| `provider_id` | string | yes | Scribe/external id |
| `encounter_datetime` | datetime | yes | ISO-8601 |
| `payer.id` / `payer.name` | string | recommended | Improves doc/rule matching |
| `patient.age` | int | recommended | Age-gated payer rules |
| `procedures[]` | array | yes (≥1) | One object per chart line |
| `procedures[].line_id` | string | yes | Unique within the request |
| `procedures[].tooth_numbers` | string[] | no | e.g. `["14"]`. Required for D4341 vs D4342 (1–3 vs 4+ teeth in the quadrant). |
| `procedures[].surfaces` | string[] | no | e.g. `["M","O"]` |
| `procedures[].quadrant` | enum | no | `UR` \| `UL` \| `LR` \| `LL`. Additive; use for SRP, irrigation, perio surgery. Aliases like `upper right` are accepted. |
| `procedures[].arch` | enum | no | `maxillary` \| `mandibular` \| `full_mouth` |
| `procedures[].findings` | string[] | no | Clinical findings. A `quadrant: UR` token is still honored if the field is omitted. |
| `procedures[].planned_or_performed` | enum | no | `planned` \| `performed` \| `unknown` |
| `supporting_note` | string | no | Optional prose |
| `attachments_present` | string[] | no | Radiograph aliases: `full_mouth_series`, `fmx`, `bitewing_radiograph`, `periapical_radiograph`, `panoramic_radiograph`. `periodontal_chart` is accepted but does not satisfy radiograph gaps. |
| `fast` | bool | no | Skip vector retrieval for lower latency |

Example: see [`tests/fixtures/coding_suggest_request.json`](../tests/fixtures/coding_suggest_request.json).

#### Response

| Field | Type | Notes |
| --- | --- | --- |
| `coding_run_id` | uuid | Persisted system-of-record id |
| `status` | enum | `pending_review` or `needs_info` |
| `recommendations[]` | array | One per `line_id` |
| `recommendations[].cdt_code` | string\|null | Recommended CDT |
| `recommendations[].confidence` | 0–1 | Per-line |
| `recommendations[].explanation` | string | Rationale for dentist |
| `recommendations[].required_supporting_documentation` | string[] | From payer rules + defaults |
| `recommendations[].missing_info[]` | `{code,message}` | Machine-readable gaps |
| `global_missing_info[]` | `{code,message}` | Request-level gaps |
| `warnings[]` | string[] | Non-blocking diagnostics |
| `idempotent_replay` | bool | `true` when same `request_id` was replayed |

##### `missing_info.code` enums

`TOOTH_MISSING`, `SURFACE_MISSING`, `FINDING_MISSING`, `RADIOGRAPH_MISSING`, `PAYER_MISSING`, `AGE_MISSING`, `PROCEDURE_EMPTY`, `SUPPORTING_NOTE_THIN`, `CDT_UNCERTAIN`, `OTHER`

## Health

`GET /health` → `{ "status": "ok", "service": "coding-agent" }` (public even when API key is set)

## Export OpenAPI / JSON Schema

With the API running:

```bash
# Full OpenAPI (includes request/response schemas)
curl -s http://127.0.0.1:8000/coding-agent/openapi.json -o docs/assets/coding-agent-openapi.json

# Or from Python without a live server:
py -3.12 -c "import json; from app.coding.main import app; print(json.dumps(app.openapi(), indent=2))" > docs/assets/coding-agent-openapi.json
```

A checked-in snapshot lives at [`docs/assets/coding-agent-openapi.json`](assets/coding-agent-openapi.json) (regenerate after contract changes).

## Env

See root `.env.example` (`CODING_AGENT_*`). Production requires `CODING_AGENT_API_KEY` when the mount is enabled.
