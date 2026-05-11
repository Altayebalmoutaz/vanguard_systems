-- Minimal agent DB: consolidate payer aliases onto rcm.payer_network; add rcm.agent_runs for persisted outcomes.

begin;

-- 1) Aliases live on the payer directory (no separate alias table).
alter table rcm.payer_network
  add column if not exists aliases jsonb not null default '[]'::jsonb;

comment on column rcm.payer_network.aliases is
  'JSON array of normalized lowercase strings for insurance free-text resolution (e.g. ["aetna","aetna dental"]).';

-- 2) Migrate from payer_identity_alias (025/026) then drop it; otherwise seed inline.
do $mig$
begin
  if to_regclass('rcm.payer_identity_alias') is not null then
    execute $u$
      update rcm.payer_network pn
      set aliases = coalesce(sub.arr, '[]'::jsonb)
      from (
        select payer_id, jsonb_agg(alias_normalized order by alias_normalized) as arr
        from rcm.payer_identity_alias
        group by payer_id
      ) sub
      where pn.payer_id = sub.payer_id
    $u$;
    execute 'drop view if exists public.payer_identity_alias cascade';
    execute 'drop table rcm.payer_identity_alias cascade';
  else
    -- Fresh installs that skipped 025/026: seed aliases per payer_id.
    update rcm.payer_network set aliases = '["aetna","aetna dental"]'::jsonb where payer_id = '60054';
    update rcm.payer_network set aliases = '["cigna","cigna dental"]'::jsonb where payer_id = '62308';
    update rcm.payer_network set aliases = '["humana","humana dental"]'::jsonb where payer_id = '61101';
    update rcm.payer_network set aliases = '["guardian","guardian dental"]'::jsonb where payer_id = '64246';
    update rcm.payer_network set aliases = '["metlife","metlife dental"]'::jsonb where payer_id = '10134';
    update rcm.payer_network set aliases = '["united healthcare dental","united healthcare","uhc dental","uhc"]'::jsonb where payer_id = '52133';
    update rcm.payer_network set aliases = '["dentaquest"]'::jsonb where payer_id = 'CX014';
    update rcm.payer_network set aliases = '["ameritas"]'::jsonb where payer_id = '47009';
    update rcm.payer_network set aliases = '["anthem","anthem blue cross"]'::jsonb where payer_id = '84105';
    update rcm.payer_network set aliases = '["fep bluedental","federal employee"]'::jsonb where payer_id = 'BCAFD';
    update rcm.payer_network set aliases = '["delta dental california","delta dental of california","delta ca"]'::jsonb where payer_id = '77777';
    update rcm.payer_network set aliases = '["delta dental new jersey","delta dental of new jersey","delta nj"]'::jsonb where payer_id = '22189';
    update rcm.payer_network set aliases = '["united concordia","united concordia dental"]'::jsonb where payer_id = 'CX013';
    update rcm.payer_network set aliases = '["principal","principal financial"]'::jsonb where payer_id = '00143MC';
  end if;
end
$mig$;

-- 3) One table for all agent runs (prior auth first; extend agent string later).
create table if not exists rcm.agent_runs (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid,
  practice_id text,
  agent text not null,
  payer_id text references rcm.payer_network (payer_id) on delete set null,
  status text not null default 'pending_review',
  input_json jsonb not null default '{}'::jsonb,
  output_json jsonb not null default '{}'::jsonb,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_agent_runs_patient_created
  on rcm.agent_runs (patient_id, created_at desc);

create index if not exists idx_agent_runs_agent_created
  on rcm.agent_runs (agent, created_at desc);

comment on table rcm.agent_runs is
  'Persisted agent I/O for audit and gating (e.g. prior_auth). claim_gate_blocked lives in meta when set by app.';

create or replace view public.agent_runs as
select * from rcm.agent_runs;

grant select, insert, update, delete on rcm.agent_runs to service_role;
grant select on public.agent_runs to anon, authenticated, service_role;

-- Refresh public view so new payer_network.aliases column is visible to PostgREST.
create or replace view public.payer_network as
select * from rcm.payer_network;

commit;
