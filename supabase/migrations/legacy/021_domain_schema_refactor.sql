-- Domain-driven schema refactor with compatibility bridge views.
-- Safe incremental migration:
-- 1) Move existing public tables into domain schemas.
-- 2) Re-create public views with old names to avoid immediate app breakage.
-- 3) Keep cross-schema FKs/constraints/indexes intact via ALTER TABLE ... SET SCHEMA.

begin;

set local lock_timeout = '5s';
set local statement_timeout = '0';

-- ---------------------------------------------------------------------------
-- Create target domain schemas
-- ---------------------------------------------------------------------------
create schema if not exists rcm;
create schema if not exists patient;
create schema if not exists agents;
create schema if not exists analytics;
create schema if not exists logs;
create schema if not exists audit;
create schema if not exists feedback;

-- Allow API roles to traverse schemas.
grant usage on schema rcm, patient, agents, analytics, logs, audit, feedback
to anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Move tables into domain schemas
-- ---------------------------------------------------------------------------
do $move$
begin
  -- patient domain
  if to_regclass('public.patients') is not null then
    execute 'alter table public.patients set schema patient';
  end if;
  if to_regclass('public.providers') is not null then
    execute 'alter table public.providers set schema patient';
  end if;
  if to_regclass('public.encounters') is not null then
    execute 'alter table public.encounters set schema patient';
  end if;

  -- agents domain
  if to_regclass('public.agents') is not null then
    execute 'alter table public.agents set schema agents';
  end if;
  if to_regclass('public.rcm_tasks') is not null then
    execute 'alter table public.rcm_tasks set schema agents';
  end if;
  if to_regclass('public.rcm_task_events') is not null then
    execute 'alter table public.rcm_task_events set schema agents';
  end if;
  if to_regclass('public.agent_decisions') is not null then
    execute 'alter table public.agent_decisions set schema agents';
  end if;
  if to_regclass('public.claim_intake_snapshot') is not null then
    execute 'alter table public.claim_intake_snapshot set schema agents';
  end if;

  -- feedback domain
  if to_regclass('public.decision_feedback') is not null then
    execute 'alter table public.decision_feedback set schema feedback';
  end if;

  -- rcm domain
  if to_regclass('public.claims') is not null then
    execute 'alter table public.claims set schema rcm';
  end if;
  if to_regclass('public.accepted_claims') is not null then
    execute 'alter table public.accepted_claims set schema rcm';
  end if;
  if to_regclass('public.denied_claims') is not null then
    execute 'alter table public.denied_claims set schema rcm';
  end if;
  if to_regclass('public.payer_network') is not null then
    execute 'alter table public.payer_network set schema rcm';
  end if;
  if to_regclass('public.payer_rules') is not null then
    execute 'alter table public.payer_rules set schema rcm';
  end if;
  if to_regclass('public.cdt_payer_rules') is not null then
    execute 'alter table public.cdt_payer_rules set schema rcm';
  end if;
  if to_regclass('public.cdt_payer_rules_structured') is not null then
    execute 'alter table public.cdt_payer_rules_structured set schema rcm';
  end if;
  if to_regclass('public.eligibility_checks') is not null then
    execute 'alter table public.eligibility_checks set schema rcm';
  end if;
  if to_regclass('public.procedure_estimates') is not null then
    execute 'alter table public.procedure_estimates set schema rcm';
  end if;
  if to_regclass('public.payer_prior_auth_rules') is not null then
    execute 'alter table public.payer_prior_auth_rules set schema rcm';
  end if;
  if to_regclass('public.payer_fee_schedules') is not null then
    execute 'alter table public.payer_fee_schedules set schema rcm';
  end if;

  -- analytics domain
  if to_regclass('public.cdt_codes') is not null then
    execute 'alter table public.cdt_codes set schema analytics';
  end if;
  if to_regclass('public.icd10_codes') is not null then
    execute 'alter table public.icd10_codes set schema analytics';
  end if;
  if to_regclass('public.icd10_dental_gem_axis') is not null then
    execute 'alter table public.icd10_dental_gem_axis set schema analytics';
  end if;
  if to_regclass('public.rule_sources') is not null then
    execute 'alter table public.rule_sources set schema analytics';
  end if;
  if to_regclass('public.cdt_code_master') is not null then
    execute 'alter table public.cdt_code_master set schema analytics';
  end if;
  if to_regclass('public.hio_rules') is not null then
    execute 'alter table public.hio_rules set schema analytics';
  end if;
  if to_regclass('public.codes') is not null then
    execute 'alter table public.codes set schema analytics';
  end if;
  if to_regclass('public.coding_rules') is not null then
    execute 'alter table public.coding_rules set schema analytics';
  end if;

  -- logs domain
  if to_regclass('public.coding_log') is not null then
    execute 'alter table public.coding_log set schema logs';
  end if;
  if to_regclass('public.eligibility_audit_log') is not null then
    execute 'alter table public.eligibility_audit_log set schema logs';
  end if;

  -- audit domain
  if to_regclass('public.audit_logs') is not null then
    execute 'alter table public.audit_logs set schema audit';
  end if;
end
$move$;

-- ---------------------------------------------------------------------------
-- Public compatibility views (bridge for existing code paths)
-- ---------------------------------------------------------------------------
create or replace view public.patients as
select * from patient.patients;

create or replace view public.providers as
select * from patient.providers;

create or replace view public.encounters as
select * from patient.encounters;

create or replace view public.agents as
select * from agents.agents;

create or replace view public.rcm_tasks as
select * from agents.rcm_tasks;

create or replace view public.rcm_task_events as
select * from agents.rcm_task_events;

create or replace view public.agent_decisions as
select * from agents.agent_decisions;

create or replace view public.claim_intake_snapshot as
select * from agents.claim_intake_snapshot;

create or replace view public.decision_feedback as
select * from feedback.decision_feedback;

create or replace view public.claims as
select * from rcm.claims;

create or replace view public.accepted_claims as
select * from rcm.accepted_claims;

create or replace view public.denied_claims as
select * from rcm.denied_claims;

create or replace view public.payer_network as
select * from rcm.payer_network;

create or replace view public.payer_rules as
select * from rcm.payer_rules;

create or replace view public.cdt_payer_rules as
select * from rcm.cdt_payer_rules;

create or replace view public.cdt_payer_rules_structured as
select * from rcm.cdt_payer_rules_structured;

create or replace view public.eligibility_checks as
select * from rcm.eligibility_checks;

create or replace view public.procedure_estimates as
select * from rcm.procedure_estimates;

create or replace view public.payer_prior_auth_rules as
select * from rcm.payer_prior_auth_rules;

create or replace view public.payer_fee_schedules as
select * from rcm.payer_fee_schedules;

create or replace view public.cdt_codes as
select * from analytics.cdt_codes;

create or replace view public.icd10_codes as
select * from analytics.icd10_codes;

create or replace view public.icd10_dental_gem_axis as
select * from analytics.icd10_dental_gem_axis;

create or replace view public.rule_sources as
select * from analytics.rule_sources;

create or replace view public.cdt_code_master as
select * from analytics.cdt_code_master;

create or replace view public.hio_rules as
select * from analytics.hio_rules;

create or replace view public.codes as
select * from analytics.codes;

create or replace view public.coding_rules as
select * from analytics.coding_rules;

create or replace view public.coding_log as
select * from logs.coding_log;

create or replace view public.eligibility_audit_log as
select * from logs.eligibility_audit_log;

create or replace view public.audit_logs as
select * from audit.audit_logs;

-- Preserve view-level grants used by clients.
grant select, insert, update, delete on all tables in schema public
to anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Functions with hardcoded public table references
-- ---------------------------------------------------------------------------
create or replace function public.get_claim_intake_snapshot(p_encounter_id text)
returns jsonb
language sql
stable
as $function$
  select to_jsonb(cis)
  from agents.claim_intake_snapshot cis
  where cis.encounter_id = p_encounter_id
  limit 1
$function$;

commit;
