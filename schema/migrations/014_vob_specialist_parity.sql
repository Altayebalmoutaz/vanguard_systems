-- 014_vob_specialist_parity.sql
-- Persist VOB specialist-parity fields (pre-auth, IND/FAM, history, age, downgrades)
-- and downgrade-aware procedure estimate columns.

alter table rcm.eligibility_checks
  add column if not exists vob_details jsonb not null default '{}'::jsonb;

comment on column rcm.eligibility_checks.vob_details is
  'Specialist-parity VOB fields: prior_auth_required, last_service_dates, IND/FAM financials, age_limits, downgrades.';

alter table rcm.procedure_estimates
  add column if not exists downgrade_applied boolean not null default false;

alter table rcm.procedure_estimates
  add column if not exists alternate_cdt text;

comment on column rcm.procedure_estimates.downgrade_applied is
  'True when estimate used an alternate-benefit (downgrade) allowed amount.';

comment on column rcm.procedure_estimates.alternate_cdt is
  'CDT code used as alternate benefit when downgrade_applied is true.';
