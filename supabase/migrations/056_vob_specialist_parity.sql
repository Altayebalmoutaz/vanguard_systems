-- Mirror schema/migrations/014_vob_specialist_parity.sql for Supabase bridge.
alter table if exists rcm.eligibility_checks
  add column if not exists vob_details jsonb not null default '{}'::jsonb;

comment on column rcm.eligibility_checks.vob_details is
  'Specialist-parity VOB fields: prior_auth_required, last_service_dates, IND/FAM financials, age_limits, downgrades.';

alter table if exists rcm.procedure_estimates
  add column if not exists downgrade_applied boolean not null default false;

alter table if exists rcm.procedure_estimates
  add column if not exists alternate_cdt text;

comment on column rcm.procedure_estimates.downgrade_applied is
  'True when estimate used an alternate-benefit (downgrade) allowed amount.';

comment on column rcm.procedure_estimates.alternate_cdt is
  'CDT code used as alternate benefit when downgrade_applied is true.';
