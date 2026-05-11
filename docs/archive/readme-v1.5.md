# Medical Agent System - Workflow Guide (v1.5)

This document describes the current end-to-end workflow of the system, including:

- **FastAPI backend layout** (routers, main HTTP routes, separate CDT service)
- Coding workflow
- Prior authorization workflow
- Claim draft/review/submit workflow
- Denial management workflow (OpenRouter intelligence + deterministic control)
- Supabase data usage by stage
- Suggested test flow via Swagger
- **Target product capabilities** (eligibility, ML scrubbing, clinic dashboards, HITL at scale, EHR/PM, analytics) and how far the current code is from each

---

## 1) System Overview

The platform follows a staged Revenue Cycle Management (RCM) pipeline:

1. Clinical input is processed by the coding agent.
2. Coding output is evaluated by prior authorization logic.
3. Claim payload is assembled into a draft (human review/edit first).
4. Biller submits reviewed draft to clearinghouse adapter.
5. Denial workflow processes ERA outcomes and prescribes next actions.

Design principle:

- Use LLMs for interpretation and summarization.
- Use deterministic logic for operational decisions.
- Keep human-in-the-loop before claim submission and when conflict signals appear.

---

## 1a) FastAPI backend — layout and HTTP routes

**Main application:** `uvicorn main:app` loads `app.main:create_app()` (shim: repo root `main.py` re-exports `app`).

| Area | Module | Routes / behavior |
|------|--------|-------------------|
| Health | `app.api.routes.health` | `GET /` — app name + message; `GET /health` — `{ "status": "ok" }` |
| Legacy agent | `app.api.routes.legacy` | `POST /run-agent` — original shape; **only** `prior_auth` is wired (delegates to async `run_agent`) |
| Dental coding (Supabase) | `app.api.routes.coding` | `POST /run-coding-agent` — load `encounters` by id, run synchronous `run_coding_agent`, persist `agent_decisions` (`decision_service.run_agent_for_encounter`) |
| Coding decisions UI API | `app.api.routes.dashboard` | `GET /decisions` — list decisions joined with encounter note (`get_decisions_for_dashboard`) |
| Human review | `app.api.routes.review` | `POST /review-decision` — approve/reject decision, optional override → `decision_feedback`; approves set encounter `status` to `coded` |
| Async agent harness | `app.api.routes.agents` | `POST /agents/run` — `agent_id` + `payload`; registered workflows: `prior_auth` (stub eligibility/auth + tool trace), `coding_agent_demo` (ping / list tools / log event) |
| RCM agents (sync) | `app.api.routes.rcm` | All prefixed **`/agents`**: `POST /prior-auth/run`, `POST /denial/run`, `POST /rcm/pipeline` (coding → prior auth), `POST /rcm/full-pipeline` (coding → prior auth → **claim draft only**), `POST /claim/submit-draft` (reviewed payload → mock submit) |
| Mercury-style dashboard | `app.dashboard_backend` | **`/dashboard/*`**: `POST /patient_input` (runs `run_full_rcm_pipeline` + optional overrides + in-memory store), `GET /claims`, `PATCH /claims/{record_id}`, `GET /appeal_letter/{claim_id}`, `GET /stats/denials`, `GET /stats/coding_accuracy`, `GET /stats/payer_trends`, `GET /events` (SSE), `POST /seed_demo`. **Note:** claim rows for this dashboard are **in-memory** (reset on process restart); see `DASHBOARD.md`. |

**RCM pipeline code paths (orchestration):**

- `app.agents.rcm_pipeline.run_rcm_pipeline` — coding → prior auth.
- `app.agents.rcm_pipeline.run_full_rcm_pipeline` — coding → prior auth → **claim draft** (`run_claim_draft_agent`). Does **not** by itself call submit, denial, or eligibility APIs; those are separate endpoints/steps.
- `app.agents.coding_agent.run_coding_agent` — linear tool/LLM/validation flow (see §2).
- `app.agents.claim_agent` — draft vs submit (`run_claim_draft_agent`, `run_claim_agent`, `submit_reviewed_claim`) with mock clearinghouse adapter.
- `app.agents.denial_agent.run_denial_agent` — invoked via `POST /agents/denial/run` (request-scoped; persistence TBD — see §5–§6).

**Separate service (not mounted on the main app):** `services/mcp_server/main.py` — standalone FastAPI app for CDT semantic search / lookup (`/tools/cdt-search`, `/tools/cdt-lookup/{code}`, `/health`). Dockerized via `services/mcp_server/Dockerfile`.

---

## 2) Coding Workflow

### Input

- `clinical_note`
- `patient_age`
- `insurance`

### Processing

1. LLM proposes CDT and ICD-10 codes (`app.llm.coding_llm`).
2. Deterministic validation checks codes against Supabase reference tables when available.
3. Payer rule flags are applied from payer rules tables.

### Output

- `cdt_codes`
- `icd10_codes`
- `confidence`
- `justification`
- `payer_flags`

### Supabase usage (coding)

- `cdt_codes` reference table
- `icd10_dental_gem_axis` reference table
- `coding_agent.payer_rules` (payer-specific rule flags)

---

## 3) Prior Authorization Workflow

### Input

- Coding output (`cdt_codes`, `icd10_codes`, confidence, etc.)
- `insurance`
- `patient_age`
- Optional clinical note context

### Processing

1. Deterministic auth rules run first (database + in-code fallback stubs).
2. OpenRouter LLM provides structured prior-auth opinion.
3. Results are merged:
   - `requires_auth`: OR merge (conservative)
   - document and payer rule lists: union merge
4. Risk assessment tool calculates operational risk.

### Output

- `requires_auth`
- `required_documents`
- `payer_rules`
- `risk_level`
- `risk_reason`
- `status = pending_review`

### Supabase usage (prior auth)

- Deterministic rule tables/views used by `prior_auth_db` access path
- Falls back to built-in rule stubs when DB unavailable

---

## 4) Claim Workflow (Draft First)

The claim stage is now intentionally draft-first.

### Input to full pipeline

- Clinical fields (`clinical_note`, `patient_age`, `insurance`)
- Either:
  - direct `patient + provider + billing`, or
  - `encounter_id` (loads front-desk snapshot from Supabase)

### Claim context resolution

When `encounter_id` is provided:

1. Pipeline calls RPC `public.get_claim_intake_snapshot(encounter_id)`.
2. If RPC unavailable, falls back to direct read from `public.claim_intake_snapshot`.
3. Validates `ready_for_claim = true`.
4. Maps snapshot blocks into strongly typed claim billing model.

### Draft generation

Claim draft agent:

1. Applies prior-auth gate:
   - If auth/docs unresolved -> `pending_auth` draft with blockers.
2. If clear:
   - Builds full structured claim payload (837-like JSON intent).
   - Returns `status = draft`.
   - Returns `available_actions = ["edit", "submit"]`.

### Biller submit

`POST /agents/claim/submit-draft`

- Accepts reviewed/edited `claim_payload`.
- Validates structure.
- Submits through clearinghouse adapter (`stedi_mock` currently).

### Output objects

From full pipeline endpoint:

- `coding`
- `prior_auth`
- `claim_draft` (final primary output)

From submit endpoint:

- `claim_id`
- `status = submitted`
- `submission_channel`
- `details` (code snapshot)

### Supabase usage (claim)

- `public.claim_intake_snapshot`
- `public.get_claim_intake_snapshot(...)` RPC

---

## 5) Denial Workflow (v1.5: LLM + Deterministic)

Current denial architecture uses an intelligence pre-pass with deterministic final control.

### Input

- `claim_id`
- `cdt_codes`
- `icd10_codes`
- patient/provider/payer context (optional fields)
- `mock_era` (`status`, `reason`)

### Processing

1. ERA parse/normalize:
   - status in `paid | denied | partial`
2. OpenRouter intelligence pass (pre-deterministic):
   - proposes reason token, evidence, confidence, and summary
3. Deterministic reason detection + action mapping:
   - authoritative next action selection
4. Reconciliation:
   - if LLM token conflicts with deterministic token and confidence is high, flag human review
5. Generate outputs:
   - `next_action`
   - `resubmission_steps`
   - appeal letter (denied claims only)

### Output

- Core:
  - `status`, `reason`, `next_action`, `appeal_letter`, `resubmission_steps`
- Intelligence trace:
  - `llm_reason_token`
  - `deterministic_reason_token`
  - `llm_confidence`
  - `reasoning_summary`
  - `required_evidence`
  - `requires_human_review`

### Supabase usage (denial)

At v1.5, denial agent does **not** persist to Supabase yet.
It runs from request payload + OpenRouter + deterministic in-code tools.

---

## 6) Supabase Table/Function Summary by Stage

### Used now

- `public.claim_intake_snapshot` (front-desk encounter snapshot)
- `public.get_claim_intake_snapshot(text)` (claim context lookup RPC)
- Coding and prior-auth reference/rule tables from existing migrations

### Not yet used by denial (planned)

- Denial queue table
- Denial event/audit table
- Assignment/SLA tracking tables

---

## 7) Operational Control Model

The system uses a layered control strategy:

1. **LLM Layer**: interprets free text and adds intelligence.
2. **Deterministic Layer**: enforces policy and final operational decisions.
3. **Human Review Layer**:
   - mandatory for claim draft edits/submission
   - highlighted for denial mismatches (`requires_human_review`)

This keeps automation useful while preserving compliance and auditability.

---

## 8) Swagger Test Run (Recommended)

### A) Build claim draft from encounter snapshot

Endpoint:

- `POST /agents/rcm/full-pipeline`

Payload example:

```json
{
  "clinical_note": "Adult prophylaxis only; no restorative planned today.",
  "patient_age": 38,
  "insurance": "Delta Dental PPO",
  "encounter_id": "enc-demo-001",
  "mock_era": {
    "status": "paid",
    "reason": ""
  }
}
```

Expect:

- `claim_draft.status = draft` (or `pending_auth`)
- full `claim_draft.claim_payload`
- `available_actions` list

### B) Submit reviewed draft

Endpoint:

- `POST /agents/claim/submit-draft`

Payload:

```json
{
  "claim_payload": {
    "patient": {
      "name": "Jane Doe",
      "dob": "1987-03-15"
    },
    "provider": {
      "name": "Dr. Smith DDS",
      "npi": "1234567890"
    },
    "subscriber": {
      "member_id": "MEM12345",
      "relationship_to_patient": "self",
      "name": "Jane Doe",
      "dob": "1987-03-15",
      "address": {
        "line1": "101 Main St",
        "city": "Albany",
        "state": "NY",
        "postal_code": "12207"
      }
    },
    "payer": {
      "payer_name": "Delta Dental PPO",
      "payer_id": "DDPPO01",
      "plan_name": "PPO Plus"
    },
    "billing_provider": {
      "name": "Capital Dental Group",
      "npi": "1234567890",
      "tax_id": "123456789",
      "taxonomy_code": "1223G0001X",
      "address": {
        "line1": "200 Clinic Rd",
        "city": "Albany",
        "state": "NY",
        "postal_code": "12208"
      }
    },
    "rendering_provider": {
      "name": "Dr. Smith DDS",
      "npi": "1234567890",
      "taxonomy_code": "1223G0001X"
    },
    "patient_address": {
      "line1": "101 Main St",
      "city": "Albany",
      "state": "NY",
      "postal_code": "12207"
    },
    "patient_sex": "F",
    "claim_frequency_code": "1",
    "place_of_service": "11",
    "patient_account_number": "ACCT-2001",
    "diagnosis_codes": ["K02.9"],
    "service_lines": [
      {
        "line_number": 1,
        "service_date": "2026-04-09",
        "cdt_code": "D1110",
        "units": 1,
        "charge_amount": 125.0,
        "diagnosis_pointers": [1],
        "tooth_number": null,
        "surface": null,
        "prior_auth_number": null
      }
    ],
    "total_charge_amount": 125.0,
    "codes": {
      "cdt": ["D1110"],
      "icd10": ["K02.9"]
    }
  }
}
```

Expect:

- `status = submitted`
- `claim_id` generated
- `submission_channel = stedi_mock`

### C) Run denial analysis

Endpoint:

- `POST /agents/denial/run`

Payload example:

```json
{
  "claim_id": "CLM99999",
  "cdt_codes": ["D2740"],
  "icd10_codes": ["K02.9"],
  "patient_name": "Jane Doe",
  "insurance_company_name": "Delta Dental",
  "provider_name": "Dr. Smith DDS",
  "mock_era": {
    "status": "denied",
    "reason": "missing_xray"
  }
}
```

Expect:

- deterministic next action
- resubmission checklist
- optional mismatch flag for manual review

---

## 9) Current Limitations

- Claim submission is still mock adapter (`stedi_mock`) not live clearinghouse.
- Denial workflow does not persist queue/history in Supabase yet.
- Full 837D serialization and real 835 parsing are future steps.
- No automated **eligibility / benefits** (270/271 or payer-portal) integration; prior auth path is clinical + rules + LLM opinion, not a benefits engine.
- No dedicated **ML** models for claim scrubbing or denial **prediction** (rules + LLM + heuristics only).
- Mercury **`/dashboard`** analytics operate on the **in-process claim store**, not on full practice AR, aging buckets, or staff time tracking.
- **EHR/practice-management** connectors (Open Dental, Dentrix, Denticon, Ascend) are not implemented; encounters and claim snapshots are expected via Supabase/API contracts you control.
- **`/dashboard/patient_input`** is documented as a full demo pipeline in `DASHBOARD.md`; the synchronous orchestrator behind it stops at **claim draft** unless you chain submit + denial separately and extend the response model / UI accordingly.

---

## 10) Suggested Next Steps (v1.6+)

1. Persist claim drafts and edits with version history.
2. Add denial queue/event tables in Supabase.
3. Add real clearinghouse submission adapter + acknowledgments.
4. Add deterministic pre-submit validator for payer-specific hard stops.

---

## 11) Target product capabilities — distance from current backend

The bullets below are **product goals**. The second column summarizes how the **current** codebase relates (prototype vs production gap).

| Capability | Where we are today | Rough distance |
|------------|--------------------|----------------|
| **Automated eligibility verification and benefits breakdown integrated with payer portals** | Prior auth uses deterministic rules (DB or stubs) plus LLM-structured opinion; `/agents/run` `prior_auth` uses **stub** eligibility. No 270/271, no payer portal or clearinghouse eligibility APIs, no benefits accumulator (deductible/max) engine. | **Far** — needs payer contracts, HIPAA/BAA-safe connectivity, eligibility transaction design, and mapping benefits to treatment plans. |
| **Real-time claim scrubbing and denial prediction using machine learning** | Coding and payer **rules** + optional LLM passes; denial path uses mock ERA + rules + LLM assist. No trained scrubber model, no clearinghouse edit responses looped in real time, no denial-risk model validated on historical claims. | **Far** — needs labeled claim/denial datasets, MLOps, and integration with scrubber / CH workflows; rules+LLM can ship **before** ML. |
| **Workflow dashboards: outstanding claims, AR aging, staff productivity** | `frontend` + `/dashboard` give a **Mercury-style** demo: claim list, stats (denials, coding confidence, payer trends), SSE. Data is **not** full-clinic AR (no aging buckets, no ledger, no user-level productivity). | **Medium–far for “clinic truth”** — near for **demo UX** if you back the same APIs with Postgres/Supabase and practice-wide extracts; far for **native PM AR** without deep PM integration. |
| **Scalable human-in-the-loop** (complex verifications, appeals, high volume) | Strong **coding** HITL: `agent_decisions` + `/review-decision` + optional `decision_feedback`. Claim flow is **draft-first** by design. Denial agent can flag `requires_human_review`. Missing: queues, assignment, SLAs, role-based tasking, appeal case management at volume. | **Partial** — core **review** pattern exists for coding; **scale** needs workflow DB, authz, and operations tooling. |
| **Seamless EHR/PM integration** (Open Dental, Dentrix, Denticon, Ascend) | Integration surface is **generic**: Supabase `encounters`, `claim_intake_snapshot`, RPC `get_claim_intake_snapshot`. No vendor SDKs, sync jobs, or UI embed. | **Far per vendor** — each PM has its own API/export constraints; expect multi-month work per integration plus certification and go-live playbooks. |
| **Analytics and reporting** (revenue leakage, payer trends, staff workload optimization) | **Payer trends** and **denial/coding** stats exist but are derived from **dashboard in-memory** runs (and Supabase-backed coding list is separate). No revenue-leakage model, no staff workload or scheduling analytics. | **Medium** for payer/coding analytics on **real persisted** data; **far** for leakage and staff optimization without PM data and definitions. |

**Summary:** The backend is a **credible staged RCM prototype** (especially **coding + review** on Supabase). The items in the table are mostly **not** done end-to-end; the shortest path is usually **persist dashboard pipeline results**, tighten **submit + denial** chaining, then add **eligibility** and **PM** integrations in that order—unless a pilot explicitly starts from one PM only.

