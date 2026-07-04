# Neon PHI-plane migrations

**Scope:** Plain Postgres DDL for the **PHI plane** (Neon Scale). No Supabase roles
(`anon`, `authenticated`, `service_role`), no browser-facing views, no Edge triggers.

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
```

Or concatenate in order for a single apply.

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
