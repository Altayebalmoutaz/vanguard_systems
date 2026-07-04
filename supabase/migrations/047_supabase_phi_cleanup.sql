-- Wave 7: Supabase PHI cleanup after Neon cutover.
-- Apply ONLY after dashboard BFF + pipeline worker are live on Neon.
-- Keeps reference/non-PHI tables (payer rules, CDT, fee schedules, payer_network).

begin;

-- Demo RCM seed (unsafe anon PHI views)
drop view if exists public.demo_denial_cases cascade;
drop view if exists public.demo_claim_cases cascade;
drop view if exists public.demo_prior_auth_cases cascade;
drop view if exists public.demo_coding_cases cascade;

-- Eligibility edge dispatch (also in 048; idempotent)
drop trigger if exists trg_process_eligibility_request on public.eligibility_requests;
drop trigger if exists trg_retry_eligibility_request on public.eligibility_requests;

-- PHI tables migrated to Neon — revoke runtime access from Supabase API roles
revoke all on table public.eligibility_requests from anon, authenticated, service_role;
revoke all on table public.eligibility_checks from anon, authenticated, service_role;
revoke all on table public.eligibility_request_events from anon, authenticated, service_role;
revoke all on table public.eligibility_audit_log from anon, authenticated, service_role;
revoke all on table public.procedure_estimates from anon, authenticated, service_role;
revoke all on table public.payer_verification_sessions from anon, authenticated, service_role;

comment on table public.eligibility_requests is
  'DEPRECATED (Wave 7): PHI moved to Neon rcm.eligibility_requests. Do not write.';

commit;
