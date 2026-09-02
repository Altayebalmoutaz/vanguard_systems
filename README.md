# Vanguard MD

Dental revenue-cycle automation for clinics that run **Open Dental**. The live product (branded **ezFi**) verifies benefits, writes results back into Open Dental, runs chairside CDT suggestions, and gives billing staff an operator dashboard.

This repository is **HIPAA-relevant**. Read [`SECURITY.md`](./SECURITY.md) before touching a database, an LLM provider, Stedi, Open Dental, or production.

**Repo:** [github.com/Altayebalmoutaz/vanguard_systems](https://github.com/Altayebalmoutaz/vanguard_systems)

---

## Current status (September 2026)

Pilot is deployed at **[ezfi.smilesuite.ai](https://ezfi.smilesuite.ai)** on a GCP VM (`docker-compose.prod.yml` + Caddy). Use **synthetic / non-PHI data only** until BAAs cover the data plane. See [`docs/deployment/README.md`](./docs/deployment/README.md).

| Area | Status |
| --- | --- |
| Eligibility (Stedi 270/271) + payer-aware estimates | **Live in the tree** — operator dashboard, queue, retry worker |
| Open Dental poll + write-back | **Live in the tree** — InsVerify, notes, InsHist, benefits grid; shadow-compare / review queue / reverify |
| Voice verification (Bland) | **Live in the tree** — `/voice` + background worker |
| Chairside coding agent | **Live in the tree** — `POST /coding-agent/v1/suggest` for scribe review |
| Staff dashboard (Next.js) | **Live in the tree** — BFF over FastAPI (not a direct-Supabase UI) |
| Claim 837 submit / ERA posting / bank recon | **Not production** — agents and stubs exist; remittance work is not on `main` |
| Real-patient PHI in production | **Blocked** until a BAA-covered store is in place |

CI on `main`: Ruff, narrow Mypy, Pytest, agent evals, Next.js lint/build, Playwright smoke, PHI-column guard, SBOM + CVE scan, backend image to GHCR.

---

## What it does

1. **Poll Open Dental** (or **Poll now** in the dashboard) for upcoming appointments.
2. Map patient + insurance → a Stedi **270/271** eligibility check.
3. Normalize benefits, compute procedure estimates, optionally enrich with an LLM narrator.
4. **Write back** into Open Dental objects staff already use (gated per connection; shadow mode logs without writing).
5. **Chairside:** the scribe posts a structured payload to `/coding-agent` and gets line-level CDT recommendations without blocking on unspoken chart fields.
6. **Voice:** outbound verification calls (Bland) with a worker that reconciles results.

The original Claw-style agents (coding → prior auth → claim draft → denial) are still in `app/agents/`. They are **not** the live clinic path.

---

## Quickstart (local)

### Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.12.x | pinned in `.python-version` |
| Node | 20.x | eligibility / operator dashboard |
| Docker | recent | recommended; compose runs API + UI |

### Docker (API + dashboard)

```bash
cp .env.example .env
cp eligibility_dashboard/.env.example eligibility_dashboard/.env.local
# Fill Supabase, Stedi, OpenRouter, Open Dental, and FASTAPI_BASE_URL
docker compose up --build
```

- Backend: `http://localhost:8000/health`
- Dashboard: `http://localhost:3000`
- Eligibility OpenAPI: `http://localhost:8000/eligibility-agent/docs`
- Coding OpenAPI: `http://localhost:8000/coding-agent/docs`

Inside Compose the dashboard must use `FASTAPI_BASE_URL=http://backend:8000` (already set in `docker-compose.yml`). Do not point it at `127.0.0.1` from inside a container.

### Backend only

```bash
python -m venv venv
# PowerShell: .\venv\Scripts\Activate.ps1
pip install -e ".[dev,scripts]"
python -m spacy download en_core_web_lg   # Presidio PHI scrub; CI uses en_core_web_sm
cp .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Dashboard only

```bash
cd eligibility_dashboard
cp .env.example .env.local
# NEXT_PUBLIC_SUPABASE_* for Auth; FASTAPI_BASE_URL=http://127.0.0.1:8000 for the BFF
npm install
npm run dev
```

Login uses **Supabase Auth**. Almost all dashboard data goes through Next.js route handlers → FastAPI (`eligibility_dashboard/src/lib/bff/fastapiProxy.ts`).

### Tests

```bash
pytest
ruff check . && ruff format --check .
cd eligibility_dashboard && npm run lint && npm run test:e2e
```

Production deploy from a machine that already has the VM SSH setup:

```bash
# on the VM
cd /opt/vanguard && ./scripts/deploy/update.sh
```

---

## Architecture

```text
┌──────────────────────────────┐     BFF + JWT      ┌─────────────────────────────────────┐
│  Next.js dashboard (ezFi)    │ ─────────────────► │  FastAPI  (main.py → app.main)      │
│  /  /voice  /opendental      │                    │                                     │
│  /patients/[id]  /settings   │                    │  /health  /api/dashboard/*           │
└──────────────────────────────┘                    │  /eligibility-agent  (Stedi 270/271) │
                                                    │  /coding-agent       (chairside CDT) │
                                                    │                                     │
                                                    │  Workers: pipeline, OD poller,      │
                                                    │  eligibility retry, voice, realtime │
                                                    └──────────────┬──────────────────────┘
                                                                   │
                    ┌──────────────┬───────────────┬───────────────┼──────────────┐
                    ▼              ▼               ▼               ▼              ▼
              Postgres        Open Dental        Stedi          Bland        OpenRouter
              (Supabase)      Remote API         270/271        voice        (LLM, scrubbed)
```

Auth: Supabase JWT **or** `X-API-Key` (`REQUIRE_AUTH=1` in production). Eligibility and coding sub-apps have their own bearer keys. Startup fails closed in production via `app/startup_guards.py`.

---

## Layout

```text
app/
  agents/           Claw-style RCM agents (not the live clinic path)
  api/              Auth, RBAC, dashboard BFF, health
  coding/           Scribe-facing CDT suggest API (mounted at /coding-agent)
  eligibility/      Stedi 270/271, estimates, voice, Open Dental enqueue
  integrations/     Open Dental client + write-back, Stedi claims, ERA adapters
  pipeline/         Durable worker (eligibility, write-back, HITL gating)
  security/         Presidio + regex PHI scrub
eligibility_dashboard/   Next.js operator UI (BFF over FastAPI)
supabase/migrations/     000_baseline + forward SQL (see migrations/README.md)
schema/migrations/       Parallel schema set used by apply scripts
docs/deployment/         VM + Caddy runbook
docker-compose.yml       Local API + UI
docker-compose.prod.yml  Prod: internal network, public Caddy only
```

---

## Environment

Full contract: [`.env.example`](./.env.example) and [`deploy/.env.production.example`](./deploy/.env.production.example).

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Server-side Postgres + Auth |
| `SUPABASE_ANON_KEY` / `NEXT_PUBLIC_SUPABASE_*` | Dashboard Auth (browser) |
| `FASTAPI_BASE_URL` | Dashboard server-side BFF target |
| `STEDI_API_KEY` | Eligibility 270/271 |
| `OPENROUTER_API_KEY` | LLM (PHI-scrubbed) |
| `OPENDENTAL_*` | Remote API + write-back feature flags |
| `ELIGIBILITY_AGENT_API_KEY` | Bearer on `/eligibility-agent` |
| `CODING_AGENT_API_KEY` | Bearer on `/coding-agent` |
| `REQUIRE_AUTH` | Must be `1` in production |
| `PILOT_SHADOW_MODE` | Blocks OD write-back and claim submit; logs shadow events |

Never commit `.env`, `deploy/.env.production`, or `eligibility_dashboard/.env.local`.

---

## Docs

| Doc | What it is |
| --- | --- |
| [`SECURITY.md`](./SECURITY.md) | HIPAA posture, secrets, threat model |
| [`docs/deployment/README.md`](./docs/deployment/README.md) | Pilot VM, Caddy, `update.sh` |
| [`docs/opendental-integration.md`](./docs/opendental-integration.md) | Poll, enqueue, write-back |
| [`docs/eligibility-workflow.md`](./docs/eligibility-workflow.md) | Eligibility operator spec |
| [`docs/coding-agent-api.md`](./docs/coding-agent-api.md) | Chairside `/v1/suggest` |
| [`docs/vanguard-production-execution-plan.md`](./docs/vanguard-production-execution-plan.md) | Pilot engineering plan |
| [`supabase/migrations/README.md`](./supabase/migrations/README.md) | Baseline vs forward migrations |

---

## License

Proprietary. All rights reserved.
