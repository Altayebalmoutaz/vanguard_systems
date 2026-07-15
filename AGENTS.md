# AGENTS.md

## Cursor Cloud specific instructions

Vanguard MD is a single product with two runnable apps plus a Postgres/Supabase data
plane. Standard commands live in `README.md`; this section only captures non-obvious
caveats for running/testing in the Cursor Cloud VM.

### Services

| Service | Dir | Dev run command | Port |
| --- | --- | --- | --- |
| FastAPI backend (eligibility sub-app mounted at `/eligibility-agent`) | repo root | `source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8000` | 8000 |
| Eligibility dashboard (Next.js, only UI) | `eligibility_dashboard/` | `npm run dev` | 3000 |

Python deps live in a repo-root `venv/` (Python 3.12). The update script recreates the
venv and installs `-e ".[dev,scripts]"` + the `en_core_web_lg` spaCy model, and runs
`npm ci` in `eligibility_dashboard/`.

### Non-obvious gotchas

- **Open the dashboard via `http://localhost:3000`, NOT `http://127.0.0.1:3000`.**
  Next.js 16 (Turbopack) blocks cross-origin dev resources, so loading the dev server
  from the `127.0.0.1` host silently breaks client hydration: the page renders but all
  buttons/handlers are dead (e.g. "+ New Check" does nothing). Using `localhost` fixes it.
- **Two separate env files.** Backend reads repo-root `.env`; the dashboard reads
  `eligibility_dashboard/.env.local` (Next.js does NOT read the root `.env`). Both are
  gitignored. Copy from the respective `.env.example` / `.env.example`.
- **Empty injected secrets override `.env`.** pydantic-settings (backend) and Next.js
  both let real OS environment variables take precedence over the dotenv files. If the
  Cloud Agent environment injects `SUPABASE_URL`/`STEDI_API_KEY`/etc. as *empty* env vars
  (placeholders), they silently override your `.env` values and the app behaves as if
  unconfigured. Either put real values in the Secrets panel, or launch the backend with
  the empties unset, e.g. `unset SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY SUPABASE_ANON_KEY STEDI_API_KEY OPENROUTER_API_KEY JINA_API_KEY && uvicorn main:app ...`.
- **DB `DATABASE_URL` must use the Supabase session pooler on port 5432** (not the
  IPv6-only direct `db.*` host, and not the 6543 transaction pooler) so LISTEN/NOTIFY
  realtime works. The schema is already applied on the pilot project — do NOT run
  `scripts/apply_neon_migrations.py` against it unless you intend to migrate.
- **Live outbound side effects:** enabling `VOICE_VERIFICATION_ENABLED` /
  `VOICE_VERIFICATION_WORKER_ENABLED` places real Bland.ai/Twilio calls, and
  `OPENDENTAL_WRITEBACK_ENABLED` writes to a live PMS. Keep `PILOT_SHADOW_MODE=true`
  (blocks OD write-back + claim submit) for safe smoke tests; `STEDI_TEST_HEADER=true`
  keeps eligibility 270/271 in Stedi sandbox.
- **Local dashboard runs unauthenticated** with `DASHBOARD_REQUIRE_AUTH=0` and blank
  `NEXT_PUBLIC_SUPABASE_*` (the browser Supabase client returns `null` instead of throwing).
- **Full eligibility E2E requires external SaaS creds that are not available offline.**
  Submitting a "New Check" or loading the eligibility queue proxies through the Next BFF to
  FastAPI, which calls a real Supabase project (layer-0 validation), and the pipeline worker
  later calls Stedi (270/271). Without a real `SUPABASE_URL` + service-role key (and a
  Postgres `DATABASE_URL` with `neon/migrations` applied, plus `STEDI_API_KEY`), the
  eligibility check endpoint returns HTTP 503 and the dashboard shows a "Failed to load
  eligibility queue" banner. This is expected offline — the UI, form, and client-side CDT
  parsing still work. Provide those secrets (as env vars / repo secrets) to exercise the
  full flow, and set `PIPELINE_WORKER_ENABLED=1` for the worker to process queued requests.
- **Offline core-logic checks that DO work without any creds:** `GET /health`, the golden
  agent evals (`python -m evals.runner`), PHI scrubbing (Presidio auto-redacts log/error
  output — you will see `<PHI>` in tracebacks), and the pytest suite (run with blank secret
  env vars, mirroring CI).

### Tests / lint / build

- Backend tests: run with blank secret env vars like CI, e.g.
  `SUPABASE_URL="" SUPABASE_SERVICE_ROLE_KEY="" SUPABASE_ANON_KEY="" OPENROUTER_API_KEY="" STEDI_API_KEY="" JINA_API_KEY="" pytest`.
- Lint/type: `ruff check .`, `ruff format --check .`, and the narrow CI mypy scope
  `mypy --follow-imports=silent app/db app/integrations/agent_runs.py`.
- Dashboard: `npm run lint`, `npm run build` (in `eligibility_dashboard/`).
- **CI on `main` is currently red and several checks fail on a clean checkout** (they are
  pre-existing, not environment issues): a stale `_fetch_claim_snapshot` import in
  `tests/test_rcm_pipeline_errors.py` / `tests/test_claim_agent.py` (the symbol moved to
  `app.integrations.claim_snapshots.fetch_claim_intake_snapshot`), the Supabase migration
  PHI guard, the OpenDental poller tests, plus ruff/eslint/mypy drift from newer tool
  versions. A model-sensitive PHI-scrub test also depends on which spaCy model is installed
  (`en_core_web_lg` for dev per README/Dockerfile vs `en_core_web_sm` in CI). Do not treat
  these as regressions caused by setup.
