# Codebase Audit — Production Readiness, Performance & Maintainability

**Date:** 2026-07-22  
**Scope:** Full repository (FastAPI backend, eligibility dashboard, workers, deps, CI/Docker)  
**Mode:** Read-only analysis — no code changes from this audit  

**Verdict:** The stack is thoughtfully layered (RBAC, PHI scrubbing, pipeline leases, startup guards, supply-chain CI), but production risk concentrates in **BFF auth bypass**, **voice webhook trust**, **eligibility cache correctness**, and **unpooled sync I/O**. Frontend maintainability is dominated by a **~3k-line client god component** and **5s polling**.

---

## Critical

### C1 — Next.js BFF injects `RCM_API_KEY` without requiring a staff session

1. **Files:** `eligibility_dashboard/src/lib/supabase/middleware.ts`, `eligibility_dashboard/src/lib/bff/fastapiProxy.ts`, `eligibility_dashboard/src/app/api/eligibility/voice/approve/route.ts`, stream route under `api/dashboard/eligibility/stream/`
2. **Why:** Middleware skips all `/api/*`. `proxyFastApi` always attaches `x-api-key: RCM_API_KEY` (and practice header) even with no session. Anyone who can reach the Next host can proxy PHI dashboard APIs as the internal key when FastAPI `REQUIRE_AUTH=1`.
3. **Fix:** Require authenticated staff (`getUser()`, not only `getSession()`) before attaching the API key on PHI/mutating routes; return 401 otherwise. Prefer Bearer-only for staff JWTs; keep API key for true server-to-server only.
4. **Impact:** Security (PHI disclosure, unauthorized mutations)
5. **Confidence:** High

### C2 — Bland voice webhook is unauthenticated; Twilio signature fails open

1. **Files:** `app/eligibility/voice/twilio_routes.py` (`voice_bland_webhook`, `_validate_twilio_signature`, `/twiml/{session_id}`); middleware skip in `app/eligibility/main.py`
2. **Why:** Eligibility API-key middleware skips `/eligibility/voice/`. Bland POST accepts any body for a guessable `session_id` and drives reconciliation. Twilio validation returns `True` if token or `twilio` package is missing. TwiML can emit DOB / member-ID tail without signature checks.
3. **Fix:** HMAC/shared-secret (or Bland signing) on Bland webhooks; fail closed when voice is enabled; validate all Twilio endpoints including TwiML; extend `startup_guards` to require webhook secrets.
4. **Impact:** Security / PHI integrity
5. **Confidence:** High

### C3 — Eligibility cache ignores CDT set / completeness

1. **Files:** `app/eligibility/triggers.py` (`resolve_cached_vs_api`), `app/eligibility/db_phi.py` (`get_latest_eligibility_check`)
2. **Why:** For `APPOINTMENT_BOOKED`, a fresh prior check for patient+payer satisfies cache even if CDTs differ or the prior response was incomplete/coverage-only. Call site often omits `practice_id` → Neon path skipped → Supabase fallback / wrong tenancy.
3. **Fix:** Include trigger, normalized CDT hash, and `response_complete`/routing status in freshness rules; always pass `request.practice_id` into cache reads.
4. **Impact:** Clinical correctness / multi-tenant safety
5. **Confidence:** High

### C4 — Open redirect after auth callback

1. **Files:** `eligibility_dashboard/src/app/auth/callback/route.ts` (`new URL(redirect, origin)`)
2. **Why:** Absolute URLs (`https://evil.com`) or protocol-relative (`//evil.com`) bypass the intended same-origin redirect. Login form guards relative paths; callback does not.
3. **Fix:** Allow only paths starting with `/` and not `//`; reject others to `/`.
4. **Impact:** Security (phishing post-login)
5. **Confidence:** High

---

## High

### H1 — No Postgres connection pool

1. **Files:** `app/db/connection.py` (`psycopg.connect` per call); all `neon_connection` / `database_connection` callers
2. **Why:** Every query opens a new TCP/TLS session. Dashboard + workers amplify latency and can exhaust `max_connections`.
3. **Fix:** `psycopg_pool.ConnectionPool` (bounded); share across handlers/workers; keep advisory leases short-lived.
4. **Impact:** Performance / reliability
5. **Confidence:** High

### H2 — Per-row procedure estimate inserts open a new connection each time

1. **Files:** `app/eligibility/db_phi.py` (`_insert_procedure_estimates_neon` → `_neon_insert` loop)
2. **Why:** N connections + N commits for N estimates vs one batched insert on Supabase path.
3. **Fix:** Multi-VALUES / `executemany` in one transaction.
4. **Impact:** Latency / connection churn
5. **Confidence:** High

### H3 — Sync long I/O on request threads (Stedi / OpenDental / OpenRouter)

1. **Files:** `app/eligibility/main.py`, `app/eligibility/api_client.py`, `app/llm/client.py`, `app/integrations/opendental/client.py`, dashboard routes
2. **Why:** Sync handlers + sync `httpx` + retries hold uvicorn threadpool workers for tens of seconds. Primary then secondary Stedi run sequentially (~2× latency).
3. **Fix:** Async clients/routes or sized thread limits; parallelize dual-payer Stedi with bounded concurrency.
4. **Impact:** Latency / capacity
5. **Confidence:** High

### H4 — OpenDental `base_url` SSRF surface

1. **Files:** `app/api/routes/dashboard.py` (`UpdateOpenDentalConnectionBody.base_url`), `app/integrations/opendental/client.py`
2. **Why:** Admins can set arbitrary http(s) URLs; server fetches with OD credentials in headers (exfil / metadata SSRF).
3. **Fix:** Host allowlist / private-IP deny list; prefer HTTPS + pinned hosts.
4. **Impact:** Security
5. **Confidence:** High

### H5 — PHI scrubbing is opt-in; JSON logs and Sentry are unscrubbed

1. **Files:** `app/logging_config.py` (`JsonFormatter`, `init_sentry`), `app/eligibility/services.py` (logs first/last name), `app/security/phi.py` (Presidio loaded at import)
2. **Why:** Scrubbing must be remembered per call site. Exception strings and unscrubbed messages can reach aggregators/Sentry. Presidio/`AnalyzerEngine()` loads at import → large RSS and slow cold start.
3. **Fix:** Global logging filter + Sentry `before_send` scrubber; never log patient names; lazy-init Presidio.
4. **Impact:** Security (HIPAA) / memory / startup
5. **Confidence:** High

### H6 — Dual Neon + PostgREST PHI write paths still live

1. **Files:** `app/eligibility/db_phi.py` (~1122 lines), `app/integrations/agent_runs.py`, voice DB helpers
2. **Why:** Silent fallthrough to PostgREST when `DATABASE_URL`/`practice_id` missing weakens tenancy guarantees.
3. **Fix:** Fail closed in production without Neon; delete or strictly gate Supabase PHI branches.
4. **Impact:** Data integrity / maintainability
5. **Confidence:** High

### H7 — Dashboard BFF polling every 5s instead of existing SSE

1. **Files:** `eligibility_dashboard/src/features/eligibility/EligibilityDashboard.tsx` (~1354); compare `opendental/page.tsx` EventSource; stream route already exists
2. **Why:** Up to ~4 parallel fetches every 5s per open tab → sustained API/DB load.
3. **Fix:** Use `/api/dashboard/eligibility/stream`; invalidate on events; pause when tab hidden.
4. **Impact:** Performance / cost
5. **Confidence:** High

### H8 — ~3031-line client god component

1. **Files:** `EligibilityDashboard.tsx`
2. **Why:** Queue, KPIs, form, detail, CSV, demo data, ~20 `useState` / 8 effects in one `"use client"` tree — large bundle, hard re-renders, high change risk.
3. **Fix:** Split into `QueueTable`, `DetailPanel`, `NewCheckForm`, `ActivityFeed` + data hooks; `next/dynamic` for heavy panels.
4. **Impact:** Maintainability / TTI
5. **Confidence:** High

### H9 — No application rate limiting

1. **Files:** Auth’d routes under `app/api/`, eligibility check, Stedi/OpenRouter callers
2. **Why:** Compromised key or bug burns vendor quota and Postgres.
3. **Fix:** Per-principal / per-practice limits at gateway or SlowAPI; tighter limits on check + voice queue.
4. **Impact:** Abuse / cost
5. **Confidence:** High

### H10 — spaCy `en_core_web_lg` in Docker; Node 22 image vs Node 20 CI

1. **Files:** `Dockerfile`, `eligibility_dashboard/Dockerfile`, `.github/workflows/ci.yml`
2. **Why:** Large image/RAM (≥8 GB class); Node version skew risks “CI green, image fails.”
3. **Fix:** Default `_sm` via build ARG; align Node to one LTS.
4. **Impact:** Build time / memory / CI reliability
5. **Confidence:** High

### H11 — N+1 OpenDental + prior-auth lookups

1. **Files:** `app/eligibility/main.py` / `opendental/eligibility_enqueue.py` / `poller.py`; `app/eligibility/db_reference.py` (`payer_requires_prior_auth` loops per CDT)
2. **Why:** One HTTP/DB call per carrier/appointment/code.
3. **Fix:** Batch/`in_` queries; cache carriers per poll pass.
4. **Impact:** Latency / rate limits
5. **Confidence:** High

### H12 — Advisory lease holds DB connection for entire worker sweep

1. **Files:** `app/db/leases.py` used by poller/retry/voice
2. **Why:** Connection stays open for full OD/voice pass.
3. **Fix:** Claim quickly, release before heavy I/O; or use shorter lock scope.
4. **Impact:** Connection pressure
5. **Confidence:** High

---

## Medium

### M1 — JWT `verify_aud: False`

1. **Files:** `app/api/auth.py`
2. **Why:** Tokens not bound to this API audience are accepted.
3. **Fix:** Verify `aud`/`iss` against configured Supabase values.
4. **Impact:** Auth hardening
5. **Confidence:** Medium

### M2 — Duplicate settings + Supabase client stacks

1. **Files:** `app/config.py` vs `app/eligibility/config.py`; `integrations/supabase_client.py` vs `eligibility/db_reference.get_supabase`
2. **Why:** Drift in key resolution, DSN, and RLS posture.
3. **Fix:** Single settings object + one client factory.
4. **Impact:** Maintainability / ops bugs
5. **Confidence:** High

### M3 — God modules (backend)

1. **Files:** `normalizer.py` (1692), `writeback.py` (1374), `db_phi.py` (1122), `dashboard/store.py` (863), `api/routes/dashboard.py` (765)
2. **Why:** Hard to test, review, and reason about; circular imports (voice approve ↔ reconcile).
3. **Fix:** Split by concern; extract shared voice completion helpers; clear API → services → integrations → db direction.
4. **Impact:** Maintainability
5. **Confidence:** High

### M4 — Broken / dead eligibility sub-app poller lifespan

1. **Files:** `app/eligibility/main.py` (~`start_appointment_poller(run_from_opendental, settings)`); real signature is settings-only
2. **Why:** Mounted lifespan unused; standalone boot would TypeError.
3. **Fix:** Align with `app.main` or delete sub-app lifespan poller.
4. **Impact:** Dead/broken code
5. **Confidence:** High

### M5 — Swallowed exceptions

1. **Files:** `claim_snapshots.py`, `observability/metrics.py`, parts of OD client / Twilio routes
2. **Why:** Silent degradation hides partial outages.
3. **Fix:** Log with `scrub_for_log` + metrics; classify fatal vs recoverable.
4. **Impact:** Observability
5. **Confidence:** High

### M6 — Frontend dead code

1. **Files:** `components/ui/charts.tsx`, `KpiCard.tsx`, `SlideOver.tsx`; `lib/format.ts` (unused; helpers duplicated in god file); unused BFF `pilot/shadow-summary`; duplicate `page.tsx` / `eligibility/page.tsx`
2. **Why:** Noise, bundle risk, extra attack surface for unused routes.
3. **Fix:** Delete or wire; redirect `/eligibility` → `/`; use shared `format.ts`.
4. **Impact:** Maintainability / clarity
5. **Confidence:** High

### M7 — Dual migration trees (`schema/` vs `supabase/`)

1. **Files:** `schema/migrations/`, `supabase/migrations/` (+ legacy)
2. **Why:** Ops confusion; wrong path applied in prod.
3. **Fix:** One documented apply path; archive legacy clearly in README.
4. **Impact:** Ops / correctness
5. **Confidence:** High

### M8 — Pipeline batch processed serially; per-call `httpx.Client`

1. **Files:** `app/pipeline/worker.py`; Stedi/OD/OpenRouter/Bland clients
2. **Why:** Queue lag; reconnect overhead.
3. **Fix:** Bounded concurrency per sweep; process-wide HTTP clients with limits.
4. **Impact:** Throughput
5. **Confidence:** Medium

### M9 — Thin 1:1 BFF routes; TS↔Pydantic schema drift

1. **Files:** ~18 `api/dashboard/**/route.ts`; create-request types in TS vs Pydantic
2. **Why:** Boilerplate + manual contract drift.
3. **Fix:** Catch-all BFF with allowlist; OpenAPI/JSON Schema codegen for shared payloads.
4. **Impact:** Maintainability
5. **Confidence:** Medium

### M10 — Demo PHI-shaped fallback data + RBAC fail-open in UI

1. **Files:** `EligibilityDashboard.tsx` `demoRows`; `hooks/useStaffSession.ts` `canAccessWithRole(null)`
2. **Why:** Fake PHI in non-dev; nav fails open when role missing.
3. **Fix:** Gate demos to development; fail closed for roles in prod.
4. **Impact:** Security UX / clarity
5. **Confidence:** Medium

### M11 — Competing logging setup

1. **Files:** `eligibility/main.py` `logging.basicConfig` vs `logging_config.configure_logging`
2. **Why:** Handler/order fights; JSON/PHI consistency breaks.
3. **Fix:** One configure path only.
4. **Impact:** Ops / compliance
5. **Confidence:** Medium

### M12 — CI red on `main` (pre-existing)

1. **Files:** Stale imports in some tests (partially updated), migration PHI guard, OD poller tests, ruff/eslint/mypy drift, spaCy model-sensitive PHI tests
2. **Why:** Hides real regressions; slows shipping.
3. **Fix:** Separate “make CI green” PR; do not conflate with feature work.
4. **Impact:** Maintainability / velocity
5. **Confidence:** High (documented in `AGENTS.md`)

---

## Low

| ID | Issue | Files | Fix | Impact | Conf. |
| --- | --- | --- | --- | --- | --- |
| L1 | `@tailwindcss/postcss` in runtime deps | `package.json` | Move to `devDependencies` | Hygiene | Med |
| L2 | Settings `@lru_cache` forever | config modules | Document; `cache_clear` in tests | Ops | Low |
| L3 | Naming collision `app/dashboard` vs `eligibility_dashboard` | folders | Rename backend store package later | Clarity | Low |
| L4 | Near-identical `error.tsx` / `loading.tsx` | dashboard routes | Shared components | DRY | Med |
| L5 | `tools/`, `examples/`, `docs/archive/` noise | top-level | Index or archive policy | Onboarding | Low |
| L6 | CORS comment stale; no FastAPI CORSMiddleware | `app/main.py` | Update docs; add allowlist only if browsers hit API | Ops | Med |
| L7 | Role checks only in Sidebar | Sidebar vs pages | Server/layout guards | Security UX | Med |

---

## What’s already in good shape

- Production startup guards (`REQUIRE_AUTH`, RBAC, pipeline worker, eligibility key, voice config)
- Pipeline `FOR UPDATE SKIP LOCKED` + advisory leases for single-flight workers
- PHI scrub helpers exist and are used on many hot paths (but not globally)
- Frontend Node dependency set is lean; no XSS via `dangerouslySetInnerHTML`
- Supply-chain CI (pip-audit, OSV, SBOM) is stronger than average
- Eligibility indexes for queue/patient paths look reasonable

---

## Optimization plan (preserve behavior)

### Quick wins (low risk, high ROI)

1. Fix auth callback open redirect (C4)
2. Require session on BFF before attaching `RCM_API_KEY` (C1)
3. Fail closed Twilio/Bland auth when voice is enabled (C2)
4. Pass `practice_id` into cache reads; tighten cache key with CDTs (C3)
5. Delete unused UI modules + dead BFF route (M6)
6. Batch procedure-estimate inserts (H2); batch prior-auth `in_` (H11)
7. Align Node versions; spaCy `_sm` default ARG (H10)
8. Global log/Sentry scrub filter; stop logging names (H5)

### High-impact refactors (plan carefully)

1. **Connection pool** + short leases (H1, H12)
2. **Split `EligibilityDashboard.tsx` + SSE** (H7, H8)
3. **Async Stedi/OD paths** or sized workers + parallel dual-payer (H3)
4. **Collapse dual PHI persistence** to Neon-only in prod (H6)
5. **Split `normalizer` / `writeback` / `db_phi`** (M3)
6. **Unify Settings + Supabase clients** (M2)
7. **Make CI green** as a dedicated track (M12)

### Suggested sequencing

| Phase | Focus | Outcome |
| --- | --- | --- |
| 1 | C1–C4, H4, H5 logging | Production security bar |
| 2 | H1–H3, H7, H11–H12 | Latency & capacity |
| 3 | H8, M3, M6, H10 | Maintainability & build cost |
| 4 | H6, M2, M7, M9, M12 | Architecture consolidation |

---

## Confidence notes

Highest-confidence items were verified in source (BFF middleware skip + API key injection, Twilio fail-open, Bland unauthenticated webhook, unpooled `psycopg.connect`, per-row neon inserts, cache TTL without CDTs, 5s polling, Presidio import-time load, open redirect). Medium-confidence items are architectural judgments (async migration scope, catch-all BFF design) where tradeoffs depend on deployment topology.
