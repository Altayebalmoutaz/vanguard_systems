-- HIPAA hardening for the eligibility data plane.
--
-- Audit findings addressed:
--   1. rcm.eligibility_checks and rcm.procedure_estimates lacked row-level
--      security; raw 270/271 payloads (PHI) were reachable by the anon role
--      via the public bridge views and `eligibility_dashboard_rows`.
--   2. rcm.eligibility_requests had `using(true) with check(true)` policies
--      for both anon and authenticated, equivalent to RLS-off.
--
-- Strategy:
--   * Enable RLS on rcm.eligibility_checks / rcm.procedure_estimates and
--     restrict SELECT to authenticated callers (and service_role implicitly).
--   * Revoke all anon SELECT on the eligibility check tables and the dashboard
--     view; the eligibility dashboard must run with an authenticated Supabase
--     session before viewing PHI.
--   * Add `created_by uuid` to rcm.eligibility_requests and scope the
--     authenticated policies to `created_by = auth.uid()`. Keep INSERT open to
--     anon for back-compat with kiosk-style flows; tighten to authenticated-
--     only in a follow-up once all upstream callers carry a JWT.
--   * Re-publish realtime tables explicitly so the new policies take effect.
--
-- Idempotent: every CREATE/DROP/POLICY uses IF EXISTS / IF NOT EXISTS or the
-- equivalent guard so re-running the migration is safe.

begin;

-- ---------------------------------------------------------------------------
-- 1. rcm.eligibility_checks — enable RLS, drop anon SELECT, lock raw_response.
-- ---------------------------------------------------------------------------
alter table rcm.eligibility_checks enable row level security;

revoke select on rcm.eligibility_checks from anon;
revoke select on public.eligibility_checks from anon;

-- Authenticated operators retain full read access (they are the dashboard users
-- and need raw_response / missing_fields for PHI-aware troubleshooting).
grant select on rcm.eligibility_checks to authenticated;
grant select on public.eligibility_checks to authenticated;

drop policy if exists "eligibility_checks_select_authenticated" on rcm.eligibility_checks;
create policy "eligibility_checks_select_authenticated"
  on rcm.eligibility_checks for select
  to authenticated
  using (true);  -- operators see all rows; tenant scoping comes later.

-- service_role bypasses RLS automatically and continues to read/write freely.

-- ---------------------------------------------------------------------------
-- 2. rcm.procedure_estimates — enable RLS, drop anon SELECT.
-- ---------------------------------------------------------------------------
alter table rcm.procedure_estimates enable row level security;

revoke select on rcm.procedure_estimates from anon;
revoke select on public.procedure_estimates from anon;

grant select on rcm.procedure_estimates to authenticated;
grant select on public.procedure_estimates to authenticated;

drop policy if exists "procedure_estimates_select_authenticated" on rcm.procedure_estimates;
create policy "procedure_estimates_select_authenticated"
  on rcm.procedure_estimates for select
  to authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- 3. eligibility_dashboard_rows view — drop anon SELECT (it joins raw_response).
-- ---------------------------------------------------------------------------
revoke select on public.eligibility_dashboard_rows from anon;
grant select on public.eligibility_dashboard_rows to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. rcm.eligibility_requests — replace using(true) with scoped policies.
-- ---------------------------------------------------------------------------
alter table rcm.eligibility_requests
  add column if not exists created_by uuid default auth.uid();

create index if not exists idx_eligibility_requests_created_by
  on rcm.eligibility_requests (created_by);

drop policy if exists "eligibility_requests_all_anon" on rcm.eligibility_requests;
drop policy if exists "eligibility_requests_all_authenticated" on rcm.eligibility_requests;

-- anon: write-only queue submission. After insert, the caller must subscribe to
-- realtime as an authenticated session to track its own request.
drop policy if exists "eligibility_requests_insert_anon" on rcm.eligibility_requests;
create policy "eligibility_requests_insert_anon"
  on rcm.eligibility_requests for insert
  to anon
  with check (true);

-- authenticated: read/write only rows the user owns (or unowned legacy rows
-- created by anon callers prior to this migration).
drop policy if exists "eligibility_requests_select_authenticated" on rcm.eligibility_requests;
create policy "eligibility_requests_select_authenticated"
  on rcm.eligibility_requests for select
  to authenticated
  using (created_by = auth.uid() or created_by is null);

drop policy if exists "eligibility_requests_insert_authenticated" on rcm.eligibility_requests;
create policy "eligibility_requests_insert_authenticated"
  on rcm.eligibility_requests for insert
  to authenticated
  with check (created_by = auth.uid() or created_by is null);

-- updates remain service_role only (already revoked from anon/authenticated in
-- migration 033). No further policy needed.

-- ---------------------------------------------------------------------------
-- 5. Re-publish realtime so the new policies are evaluated on every event.
-- ---------------------------------------------------------------------------
do $realtime$
begin
  alter publication supabase_realtime drop table rcm.eligibility_checks;
  alter publication supabase_realtime add table rcm.eligibility_checks;
exception
  when others then
    null;
end
$realtime$;

do $realtime$
begin
  alter publication supabase_realtime drop table rcm.procedure_estimates;
  alter publication supabase_realtime add table rcm.procedure_estimates;
exception
  when others then
    null;
end
$realtime$;

do $realtime$
begin
  alter publication supabase_realtime drop table rcm.eligibility_requests;
  alter publication supabase_realtime add table rcm.eligibility_requests;
exception
  when others then
    null;
end
$realtime$;

commit;
