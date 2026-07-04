# Migration reconciliation report (2026-06-15)

This folder holds the original `001_*` … `044_*` migrations and the old README.
They were **superseded** by `../000_baseline_production_schema.sql`, which was
built from the **live** Supabase schema. This note records why, so no history is
lost.

## How the comparison was done

The live database was inspected via the Supabase MCP: `supabase_migrations`
history, `information_schema` / `pg_catalog` for columns, constraints, indexes,
triggers, RLS, policies, functions, view definitions, role grants, and
`pg_publication_tables` for realtime membership. The result was diffed against
every file in this folder.

## Applied-migration history (live `supabase_migrations`)

The remote history records only **20** migrations, the earliest being
`021_domain_schema_refactor`. Everything before `021` ran before the history was
re-based, so those tables exist but are untracked.

**Recorded as applied:** 021, 022, 023, 024, `027_minimal_agent_db_aliases_and_runs`,
028, 029, 030 (+ a `webhook_agent_url_timeout` revision), 031, 032, 033, 034,
035, 036, 039, 040, 041, 042, 043.

**In the repo but NOT in the applied history:**
`001`–`020`, `025`, both `026_*`, `027_seed_payer_fee_schedules_illustrative`,
both `037_*` (`eligibility_rls_hardening` **and** `eligibility_daily_kpi_buckets`),
`038`, and `044`.

## Key discrepancies (repo vs production)

1. **History baseline reset at 021.** A fresh `db push` from these files would
   not match production. The new baseline fixes this.

2. **`037_eligibility_rls_hardening` was never applied.** Production has RLS
   **disabled** on `rcm.eligibility_checks` and `rcm.procedure_estimates`, has
   **no `created_by` column** on `rcm.eligibility_requests`, and keeps the
   permissive `using(true)` policies. The baseline reflects production, not 037.

3. **`038_eligibility_webhook_signing` was never applied.** The live
   `rcm.invoke_eligibility_request_processor()` is the `033` version with **no
   HMAC signature** (no `signing_secret`, no `X-Webhook-Signature` header). The
   baseline ships the 033 version.

4. **`037_eligibility_daily_kpi_buckets` was never applied.** None of its objects
   exist in production; excluded from the baseline.

5. **Undocumented production objects (added directly in Supabase, in no file):**
   - `logs.coding_log` → trigger `n8n-coding-assistant`
   - `rcm.denied_claims` → trigger `n8n-denial-trigger`
   Both `AFTER INSERT … EXECUTE supabase_functions.http_request(...)` to a
   hardcoded ngrok URL (`https://haltless-royal-enjoyably.ngrok-free.dev`).
   Excluded from the baseline (ephemeral dev integration).

6. **`019_cdt_codes_embedding_vector_1024` matches production** (`vector(1024)` +
   HNSW index) but targets `public.cdt_codes`, which is now a *view*; re-running
   it as-is would fail. The baseline creates the column on `analytics.cdt_codes`.

7. **Seed-data migrations** (`002`, `006`, `007`, `013`, `023`, `024`, `026_*`,
   `040`–`043`, `044`) are out of scope for the schema-only baseline. Run them
   from this folder if a dataset reload is required.

## Bottom line

- **Trust:** `../000_baseline_production_schema.sql` for schema.
- **Do not trust:** `037_*`, `038`, `025`, `026_*`, `027_seed`, `044` as
  representing production — they were never applied or have drifted.
- **Reapply intentionally as new `045_+` migrations** if you want the RLS
  hardening (037) and webhook signing (038) security posture in production.
