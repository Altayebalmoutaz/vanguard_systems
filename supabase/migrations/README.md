# Supabase migrations

## Current state — consolidated baseline

`000_baseline_production_schema.sql` is the **single source of truth** for the
database schema. It was generated on **2026-06-15** by inspecting the **live**
Supabase database directly (tables, columns, constraints, indexes, triggers,
RLS, policies, functions, views, grants, realtime membership) rather than by
trusting the historical migration files — several of which were never applied
or had drifted from production.

The 47 historical files (`001_*` … `044_*`) plus the old README are preserved
unchanged under [`legacy/`](./legacy). They are **not** applied by the Supabase
CLI (it ignores subdirectories) and are kept only for provenance and for
reloading reference/seed data if needed. See
[`legacy/RECONCILIATION.md`](./legacy/RECONCILIATION.md) for the full repo-vs-live
diff that motivated this consolidation.

### What the baseline contains

- Domain schemas: `patient`, `agents`, `analytics`, `audit`, `feedback`,
  `logs`, `rcm`. `public` holds backward-compatible **views** over them.
- All tables, constraints, indexes, RLS + policies, trigger functions, and the
  `eligibility_requests` queue dispatcher (`rcm.invoke_eligibility_request_processor`,
  the production 033-era version **without** HMAC signing).
- Schema only — **no reference/seed data**. To repopulate `payer_network`,
  `payer_rules`, `icd10_codes`, fee schedules, etc., run the relevant seed files
  from `legacy/` against the baseline.

### Deliberately excluded (documented, not reproduced)

- **n8n webhook triggers** on `logs.coding_log` and `rcm.denied_claims`. These
  exist in production with a hardcoded ngrok URL and are treated as an ephemeral
  dev integration. Recreate them manually (or via a dedicated, Vault-parameterized
  migration) if they become permanent.
- **`037_eligibility_rls_hardening`** and **`038_eligibility_webhook_signing`** —
  present in the repo but **never applied** to production. The baseline matches
  the live (un-hardened) state. Re-introduce these as new forward migrations
  (`045_*`, `046_*`) if/when you actually want to ship that security posture.

## Re-pointing the migration history (run intentionally, not automatically)

Production already has this schema, so do **not** re-run the baseline against it.
Instead, mark the baseline as already-applied and reset the (rebased) history.
On a **development branch first**, then production:

```bash
# 1. Validate the baseline reproduces prod on a throwaway Supabase branch:
supabase db reset           # applies 000_baseline_* to a fresh local/branch DB
#    …then diff the branch schema against production.

# 2. Once verified, baseline the remote history (does not touch data):
supabase migration repair --status reverted <every-old-version>
supabase migration repair --status applied  000   # the baseline
```

Never reset history on production until the branch diff is clean.

## Application schema (Supabase Postgres)

App DDL lives in [`schema/migrations/`](../schema/migrations/) (apply with
`python scripts/apply_schema_migrations.py`). See
[`schema/migrations/README.md`](../schema/migrations/README.md).

> Formerly `neon/migrations/` — renamed; pilot DB is Supabase, not Neon.

## Forward convention (every new migration)

1. **Unique zero-padded prefix `>= 045_`** (the legacy set used 001–044; 000 is
   the baseline). Never reuse a prefix.
2. Snake-case description of the *what* (and *why* if non-obvious), under ~80 chars.
3. **Idempotent DDL** (`create table if not exists`, guarded `alter`, `do $$ … $$`
   for policies).
4. **RLS-by-default**: every new table enables RLS and ships an explicit policy
   (or a comment justifying service-role-only access).
5. **PHI/PII columns** get restrictive policies — no `grant select` to `anon` on
   names, DOB, member IDs, or `raw_response`-style payloads. Prefer a redacted
   view for dashboards.
6. **No destructive defaults**: `drop … if exists` only inside the same migration
   that recreates the object.
7. **CI PHI guard** — forward migrations are scanned by
   `python scripts/check_supabase_migrations_phi_columns.py` (also in GitHub Actions).
   Do not add PHI-plane tables or forbidden column names here; use
   `schema/migrations/` instead.

## Checklist before opening a PR

- [ ] Filename has a unique prefix `>= 045_`
- [ ] Migration is idempotent (`if not exists`, `if exists`)
- [ ] New tables enable RLS and have at least one policy
- [ ] No `using (true)` policies on PHI tables (use role-aware predicates)
- [ ] No `grant select` to `anon` on PHI columns
- [ ] Down-migration considered (document in the PR if not provided)
