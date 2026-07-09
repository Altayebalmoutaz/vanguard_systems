# Application Postgres migrations (Supabase-only pilot)

**Scope:** Plain Postgres DDL for the application data plane. Originally authored for
a dedicated Neon PHI project; for the **Supabase-only pilot** the same DDL is applied
to the **Supabase Postgres** (direct connection string), which becomes the single
database. No Supabase roles (`anon`, `authenticated`, `service_role`) are granted,
no browser-facing views, no Edge triggers — the browser reaches this data only
through the FastAPI BFF.

## Supabase-only pilot notes

- Set `DATABASE_URL` (canonical; `NEON_DATABASE_URL` still works as an alias) to the
  Supabase **direct** Postgres connection string (Settings → Database). Use the
  session pooler string for the app if connection counts become a concern.
- Apply migrations with the runner: `python scripts/apply_neon_migrations.py`
  (applies every numbered file below in order, stopping on the first error).
- **Tenant isolation for the pilot is enforced in the application layer** — every
  query in `app/` filters by `practice_id`, and the per-request session GUC
  (`app.practice_id`) is still set. FORCE ROW LEVEL SECURITY with a dedicated
  non-superuser app role is deferred until the BAA / scale-up step; the Supabase
  `postgres` role bypasses RLS, so do not treat RLS as active protection yet.
- Compliance: Supabase Pro has **no BAA**. This configuration is for a shadow-mode /
  limited pilot only.

**Source:** Ported from `supabase/migrations/000_baseline_production_schema.sql` per
[`docs/phi-plane-table-inventory.md`](../../docs/phi-plane-table-inventory.md).

## Apply order

Run against an empty Neon database (HIPAA project recommended):

```bash
# Example — replace with your Neon direct (non-pooler) URL for migrations
psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -f neon/migrations/001_platform_core.sql
psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -f neon/migrations/002_eligibility.sql
psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -f neon/migrations/003_agents_workflow.sql
psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -f neon/migrations/004_claims_denials.sql
# …through 012_opendental_writeback_full.sql (or: python scripts/apply_neon_migrations.py)
```

Or concatenate in order for a single apply. Numbered files include realtime notify (008),
opendental_connections (009), worker_state (010), pgaudit_supabase (011), and
`writeback_full` on OD connections (012).

## Design choices (Phase 0.3)

| Topic | Decision |
| --- | --- |
| Domain schemas | Same names as Supabase (`patient`, `agents`, `rcm`, …) so asyncpg rewrite can reuse qualified names |
| Cross-plane FKs | **Dropped** — `agent_runs.payer_id`, `agent_decisions.agent_id` are plain columns; payer/agent registries stay on Supabase |
| Tenancy | `practice_id text not null` on all tenant-scoped PHI rows; `platform.user_practice_roles` maps Supabase Auth `user_id` → role |
| RLS | **FORCE ROW LEVEL SECURITY** + `app.practice_id` session GUC (set by FastAPI per request); `app.bypass_rls=true` for migrations/admin only |
| Queue | No `net.http_post` / Edge triggers — `platform.pipeline_runs` skeleton for Phase 3 worker |
| Reference data | **Not included** — CDT, payer rules, fee schedules remain on Supabase |

## Runtime session variables

FastAPI (Phase 2) should connect with a **dedicated app role** that does **not** have
`BYPASSRLS` (Neon's default `neondb_owner` bypasses RLS — fine for migrations only).

Per request:

```sql
SET LOCAL app.practice_id = '<practice_id>';
-- migrations / break-glass only:
SET LOCAL app.bypass_rls = 'true';
```

## After apply

1. Store connection string as `NEON_DATABASE_URL` (pooler endpoint for app, direct for migrations).
2. Do **not** copy Supabase reference seeds here.
3. Data cutover from Supabase PHI tables is a separate operational step (Phase 2).
