-- Coding runs contain patient identifiers, clinical findings, and supporting
-- notes. They are server-side PHI and must never be reachable through Supabase
-- browser roles. The service role remains the only PostgREST caller.
--
-- This forward migration also repairs environments where migrations 059/061
-- were already applied with authenticated-role grants.

begin;

revoke all privileges on table agents.coding_runs
  from public, anon, authenticated;
revoke all privileges on table agents.coding_decisions
  from public, anon, authenticated;
revoke all privileges on table public.coding_runs
  from public, anon, authenticated;
revoke all privileges on table public.coding_decisions
  from public, anon, authenticated;

grant select, insert, update, delete on table agents.coding_runs
  to service_role;
grant select, insert, update, delete on table agents.coding_decisions
  to service_role;
grant select, insert, update, delete on table public.coding_runs
  to service_role;
grant select, insert, update, delete on table public.coding_decisions
  to service_role;

commit;
