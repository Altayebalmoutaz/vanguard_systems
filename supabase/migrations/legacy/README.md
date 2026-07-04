# Supabase migrations — conventions and historical notes

Supabase CLI applies migrations in **lexicographic** order and tracks each by **filename**
in `supabase_migrations.schema_migrations`. Renaming any file that has already run on a
hosted database will cause that file to be re-applied (and almost certainly fail because
the schema already exists). For that reason the historical artefacts below are
**preserved as-is**; only **new** migrations should follow the conventions in this file.

## Forward convention (apply to every new migration)

1. **Unique zero-padded numeric prefix.** Use `044_`, `045_`, `046_` …; never reuse a
   prefix. If two changes are intentionally co-deployed, give them adjacent prefixes
   (`041_*`, `042_*`) rather than collide on one.
2. **Snake-case description** describing the *what* (table or feature) followed by the
   *why* if not obvious. Keep under ~80 chars total.
3. **Idempotent DDL** wherever possible (`create table if not exists`, guarded `alter`,
   `do $$ … $$` blocks for policies).
4. **RLS-by-default.** Every new table must `enable row level security` and ship an
   explicit policy (or an explicit comment justifying service-role only access).
5. **PHI / PII columns** must be paired with restrictive policies. Do **not** grant
   `select` to `anon` on tables containing names, DOB, member IDs, or `raw_response`-style
   payloads. Prefer a *view* with redacted columns for dashboards.
6. **No destructive defaults.** Use `drop … if exists` only inside the same migration that
   re-creates the object; never drop tables that downstream environments may rely on
   without an explicit deprecation migration first.

## Historical artefacts (do not rename)

### Missing slot: `012_`

There is no `012_*.sql`. The next migration after `011_unified_claim_adjudication_function.sql`
is `013_cdt2024_master_load.sql`. The skip is intentional / lost-to-history; treat slot
`012` as **permanently retired**. Do not create a new file with that prefix.

### Duplicate prefix: `026_`

Two files start with `026_`:

- `026_seed_payer_identity_aliases.sql`
- `026_seed_payer_network_from_stedi_csv.sql`

Lex order is stable (`identity_aliases` precedes `network_from_stedi_csv` alphabetically),
so apply order is well-defined, but the convention is fragile. Do not add any further
`026_` files.

### Duplicate prefix: `027_`

Two files start with `027_`:

- `027_minimal_agent_db_aliases_and_runs.sql`
- `027_seed_payer_fee_schedules_illustrative.sql`

Same situation: lex-stable (`minimal_agent_*` before `seed_payer_*`), but do not add any
further `027_` files.

### `037_eligibility_rls_hardening.sql`

Audit-driven hardening of the eligibility data plane. Enables RLS on
`rcm.eligibility_checks` and `rcm.procedure_estimates`, drops the previous
`using(true)` policies on `rcm.eligibility_requests`, scopes authenticated reads
via a new `created_by` column, and revokes all anon `SELECT` on the dashboard
view (`raw_response` is PHI). See migration header for the full rationale.

### `038_eligibility_webhook_signing.sql`

Adds HMAC-SHA256 signing to the eligibility webhook trigger so the edge function
(`supabase/functions/process-eligibility-request`) can authenticate every call.
Requires a new Vault secret `eligibility_dashboard_edge_function_signing_secret`
that mirrors the function's `WEBHOOK_SECRET` environment variable.

### `039_provider_payer_network_directory.sql`

Adds `rcm.provider_payer_network`: practice + rendering NPI + payer (FK to
`payer_network`), optional `provider_service_location_key`, and
`in_network_for_fees` for fee-schedule vs UCR modeling when Stedi 271 network
flags are not trusted. Non-PHI reference data; anon/authenticated SELECT;
mutations `service_role` only. Mirror view `public.provider_payer_network`.

### `040_mock_clinic_practices_and_seed.sql`

Adds `rcm.practices`, FK from `provider_payer_network.practice_id`, and seeds the
`vgd_mock_brooklyn` demo. Apply after `039_*`.

### `041_extend_mock_provider_payer_inn_seeds.sql`

Re-seeds `vgd_mock_brooklyn` provider_payer_network (Anthem, Ameritas, Cigna, MetLife, UHC, etc.).

### `042_seed_payer_84103_fee_schedules_from_baseline.sql`

Copies baseline `52133` fee rows onto `84103` for Layer 5 when running Stedi **84103** scenarios.

### `043_seed_payer_amitas_fee_schedules.sql`

Copies baseline ``52133`` fee rows onto **AMTAS00425** so Ameritas Stedi scenarios have Layer 5 amounts.

## Quick checklist before opening a PR

- [ ] Filename has a unique prefix `>= 044_`
- [ ] Migration is idempotent (`if not exists`, `if exists`)
- [ ] New tables enable RLS and have at least one policy
- [ ] No `using (true)` policies on PHI tables (use role-aware predicates instead)
- [ ] No `grant select` to `anon` on PHI columns
- [ ] Down-migration considered (if not provided, document in PR description)
