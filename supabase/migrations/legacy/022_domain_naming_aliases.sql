-- Non-breaking naming aliases for domain consistency.
-- Keep existing table names intact while exposing cleaner canonical names.

begin;

create schema if not exists rcm;
create schema if not exists agents;
create schema if not exists audit;

-- RCM aliases
create or replace view rcm.denials as
select * from rcm.denied_claims;

create or replace view rcm.claim_submissions as
select * from rcm.accepted_claims;

-- Agents aliases
create or replace view agents.registry as
select * from agents.agents;

create or replace view agents.tasks as
select * from agents.rcm_tasks;

create or replace view agents.task_events as
select * from agents.rcm_task_events;

-- Audit alias
create or replace view audit.audit_events as
select * from audit.audit_logs;

-- Optional compatibility aliases in public for cleaner API transition
create or replace view public.denials as
select * from rcm.denied_claims;

create or replace view public.claim_submissions as
select * from rcm.accepted_claims;

grant select, insert, update, delete on
  rcm.denials,
  rcm.claim_submissions,
  agents.registry,
  agents.tasks,
  agents.task_events,
  audit.audit_events,
  public.denials,
  public.claim_submissions
to anon, authenticated, service_role;

commit;
