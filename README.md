# Vanguard MD

> Dental + medical Revenue Cycle Management automation. Coding → prior authorization →
> claim submission → denial / appeal flows, plus realtime eligibility (Stedi 270/271)
> with payer-aware cost estimation. Built on FastAPI, Supabase, and a "Claw-style"
> linear, tool-first agent runtime.

This repository is **HIPAA-relevant**. Read [`SECURITY.md`](./SECURITY.md) before doing
anything that touches a Supabase project, an LLM provider, or the Stedi clearinghouse.

---

## Quickstart (local development)

### Prerequisites

| Tool         | Version    | Notes                                                      |
| ------------ | ---------- | ---------------------------------------------------------- |
| Python       | 3.12.x     | pinned in `.python-version`                                |
| Node         | 20.x       | for the eligibility dashboard                              |
| Supabase CLI | latest     | only if you want to run a local Postgres / edge functions |
| Docker       | recent     | optional — for the production-shape compose stack          |

### Backend

```bash
git clone <repo> vanguard-md
cd vanguard-md

python -m venv venv
source venv/bin/activate          # PowerShell: .\venv\Scripts\Activate.ps1

pip install -e ".[dev,scripts]"
python -m spacy download en_core_web_lg   # required by Presidio (PHI scrub)

cp .env.example .env              # fill in real values; never commit .env

uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The eligibility sub-app is mounted at `/eligibility-agent`. Health check: `GET /health`.

### Eligibility dashboard (only UI in-tree)

```bash
cd eligibility_dashboard
cp .env.local.example .env.local  # set NEXT_PUBLIC_SUPABASE_URL / ANON_KEY
npm install
npm run dev
```

The eligibility dashboard talks to Supabase directly (no FastAPI dependency) and is the
only first-party UI shipped from this repository.

### Run the test suite

```bash
pytest                                    # all tests
pytest tests/eligibility_agent -k layer1  # focused
pytest --cov=app --cov-report=term        # with coverage
ruff check . && ruff format --check .     # lint
```

### Docker

```bash
docker compose up --build                 # backend on :8000
```

---

## Architecture at a glance

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                        Vanguard MD backend (FastAPI)                     │
│                                                                          │
│   /health  /agents/*  /coding/*  /rcm/*  /review-decision                │
│        │                                                                 │
│        ▼                                                                 │
│   app/agents/        Claw-style agents (coding, prior_auth, claim,       │
│                      denial, rcm_pipeline) — linear, tool-first.         │
│                                                                          │
│   app/tools/         Deterministic tool functions invoked by agents.     │
│                                                                          │
│   app/eligibility/   Sub-FastAPI mounted at /eligibility-agent: Stedi    │
│                      270/271, canonical record, cost calculator,         │
│                      Presidio PHI scrubbing, audit log.                  │
│                                                                          │
│   app/integrations/  Supabase client, payer identity, agent_runs DAO.    │
│                                                                          │
│   app/security/      Cross-cutting PHI scrubbers (Presidio + regex).     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        Supabase (Postgres + Edge)                        │
│                                                                          │
│   migrations/        37 SQL files (see supabase/migrations/README.md     │
│                      for forward conventions and historical gotchas).    │
│                                                                          │
│   functions/         Deno edge functions; process-eligibility-request    │
│                      drains the eligibility queue and POSTs to FastAPI.  │
└──────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              External APIs                               │
│                                                                          │
│   Stedi (eligibility 270/271 + planned 837P/D submissions)               │
│   OpenRouter (LLM completions for coding + denial intelligence)          │
│   Jina (vector embeddings for CDT semantic retrieval)                    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Top-level layout

```text
.
├── app/                       # FastAPI backend
│   ├── agents/                # claw-style agents (coding, prior_auth, claim, denial)
│   ├── api/                   # routers + auth dependencies
│   ├── eligibility/           # mounted sub-app (Stedi 270/271, cost calc, Presidio)
│   ├── integrations/          # Supabase client, payer identity, agent_runs DAO
│   ├── llm/                   # OpenRouter wrappers (coding, prior auth, denial)
│   ├── runtime/               # async tool executor + agent context
│   ├── schemas/               # Pydantic request/response models
│   ├── security/              # cross-cutting PHI scrub helpers
│   ├── services/              # cross-cutting helpers (RAG, Jina, etc.)
│   ├── tools/                 # deterministic tools the agents invoke
│   ├── config.py              # pydantic-settings: Settings + get_settings()
│   └── main.py                # create_app() + sub-app mounts
│
├── archive/                   # historical artefacts — not deployed
├── docs/                      # human-readable docs (architecture, contracts)
│   └── archive/               # superseded plans
│
├── eligibility_dashboard/     # Next.js eligibility-operator UI (Supabase-only)
│
├── supabase/
│   ├── functions/             # Deno edge functions
│   └── migrations/            # SQL migrations (see migrations/README.md)
│
├── scripts/                   # operational scripts (ingest CDT 2024, RAG ingest, etc.)
├── tests/                     # pytest suites mirroring app/ layout
│
├── Dockerfile                 # multi-stage, non-root, /health probe
├── docker-compose.yml         # backend service
├── .github/workflows/ci.yml   # ruff + mypy + pytest + eligibility-dashboard build
├── pyproject.toml             # PEP 621 metadata + tool config (ruff, mypy, pytest)
├── requirements.txt           # mirror of runtime deps for legacy installers
├── SECURITY.md                # HIPAA posture, BAA matrix, secret rotation
├── main.py                    # uvicorn entry shim (re-exports app.main:app)
└── README.md                  # this file
```

---

## Environment variables

The full contract lives in [`.env.example`](./.env.example). The most important keys:

| Variable                            | Purpose                                                |
| ----------------------------------- | ------------------------------------------------------ |
| `SUPABASE_URL`                      | Supabase project URL                                   |
| `SUPABASE_SERVICE_ROLE_KEY`         | Service-role key (RLS-bypassing — server only)         |
| `SUPABASE_ANON_KEY`                 | Anon key (used by the eligibility dashboard UI)        |
| `OPENROUTER_API_KEY`                | LLM completions for coding + denial intelligence       |
| `JINA_API_KEY`                      | Vector embeddings for CDT semantic retrieval           |
| `STEDI_API_KEY`                     | Stedi clearinghouse (270/271 eligibility)              |
| `STEDI_TEST_HEADER`                 | `true` when using Stedi sandbox / mock                 |
| `ELIGIBILITY_AGENT_API_KEY`         | Bearer token enforced on /eligibility-agent routes     |
| `REQUIRE_AUTH`                      | `1` (default in prod) gates main FastAPI routes        |
| `SUPABASE_JWT_SECRET`               | HS256 secret for verifying Supabase user JWTs          |
| `INTERNAL_API_KEYS`                 | Comma-separated allow-list for server-to-server calls  |

`REQUIRE_AUTH=1` is mandatory in production. See `app/api/auth.py` for the verifier.

---

## Where to look next

- [`SECURITY.md`](./SECURITY.md) — HIPAA posture, secret rotation, threat model.
- [`docs/production-roadmap.md`](./docs/production-roadmap.md) — current product /
  engineering doctrine.
- [`docs/eligibility-workflow.md`](./docs/eligibility-workflow.md) — operational spec
  for the eligibility agent.
- [`supabase/migrations/README.md`](./supabase/migrations/README.md) — migration
  conventions and historical gotchas.
- [`app/eligibility/README.md`](./app/eligibility/README.md) — package-level docs for
  the eligibility sub-app.

---

## License

Proprietary. All rights reserved.
