-- =============================================================================
-- Neon 003 — Agents, Workflow OS, coding decisions
-- =============================================================================
-- PHI plane · agents.* + feedback.* + rcm.agent_runs + platform.sla_policies
-- Cross-plane FKs to Supabase reference tables are intentionally omitted.
-- =============================================================================

begin;

set local lock_timeout = '5s';

create schema if not exists agents;
create schema if not exists feedback;

-- ---------------------------------------------------------------------------
-- platform.sla_policies (Workflow OS)
-- ---------------------------------------------------------------------------
create table if not exists platform.sla_policies (
  id              uuid primary key default gen_random_uuid(),
  practice_id     text,
  task_type       text not null,
  target_minutes  integer not null check (target_minutes > 0),
  warn_minutes    integer check (warn_minutes is null or warn_minutes > 0),
  created_at      timestamptz not null default now(),
  unique (practice_id, task_type)
);

comment on column platform.sla_policies.practice_id is
  'NULL = platform default SLA; otherwise per-clinic override.';

create index if not exists idx_sla_policies_task_type
  on platform.sla_policies (task_type);

-- ---------------------------------------------------------------------------
-- agents.rcm_tasks (HITL work queue spine)
-- ---------------------------------------------------------------------------
create table if not exists agents.rcm_tasks (
  id                  uuid primary key default gen_random_uuid(),
  practice_id         text not null,
  backend_record_id   text not null default '',
  backend_claim_id    text not null default '',
  task_type           text not null default 'Full RCM pipeline',
  patient_name        text not null,
  patient_dob         text,
  payer               text,
  clinical_note       text not null default '',
  demographics_block  text,
  ai_codes            text[] default '{}'::text[],
  ai_summary          text,
  confidence          double precision,
  status              text not null default 'pending',
  biller_edited_codes text[],
  pipeline_json       jsonb,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz
);

create index if not exists rcm_tasks_status_created_idx
  on agents.rcm_tasks (practice_id, status, created_at desc);

drop trigger if exists trg_rcm_tasks_updated_at on agents.rcm_tasks;
create trigger trg_rcm_tasks_updated_at
  before update on agents.rcm_tasks
  for each row execute function platform.set_updated_at();

-- ---------------------------------------------------------------------------
-- agents.rcm_task_events
-- ---------------------------------------------------------------------------
create table if not exists agents.rcm_task_events (
  id          uuid primary key default gen_random_uuid(),
  practice_id text not null,
  task_id     uuid not null references agents.rcm_tasks(id) on delete cascade,
  event_type  text not null,
  actor_label text not null default 'system',
  payload     jsonb default '{}'::jsonb,
  created_at  timestamptz not null default now()
);

create index if not exists rcm_task_events_task_created_idx
  on agents.rcm_task_events (task_id, created_at desc);

-- ---------------------------------------------------------------------------
-- agents.agent_decisions (coding path)
-- ---------------------------------------------------------------------------
create table if not exists agents.agent_decisions (
  id             uuid primary key default gen_random_uuid(),
  practice_id    text not null,
  encounter_id   uuid references patient.encounters(id) on delete cascade,
  agent_name     text,
  agent_id       uuid,
  input_snapshot jsonb,
  reasoning      text,
  output         jsonb,
  confidence     double precision,
  status         text not null default 'pending_review',
  created_at     timestamptz not null default now()
);

comment on column agents.agent_decisions.agent_id is
  'Logical ref to Supabase agents.agents — no cross-DB FK.';

create index if not exists idx_decisions_encounter
  on agents.agent_decisions (encounter_id);

create index if not exists idx_decisions_practice_status
  on agents.agent_decisions (practice_id, status, created_at desc);

-- ---------------------------------------------------------------------------
-- feedback.decision_feedback
-- ---------------------------------------------------------------------------
create table if not exists feedback.decision_feedback (
  id             uuid primary key default gen_random_uuid(),
  practice_id    text not null,
  decision_id    uuid not null references agents.agent_decisions(id) on delete cascade,
  human_override jsonb,
  reason         text,
  created_at     timestamptz not null default now()
);

create index if not exists idx_feedback_decision
  on feedback.decision_feedback (decision_id);

-- ---------------------------------------------------------------------------
-- rcm.agent_runs (shared agent run log — prior-auth today)
-- ---------------------------------------------------------------------------
create table if not exists rcm.agent_runs (
  id          uuid primary key default gen_random_uuid(),
  practice_id text not null,
  patient_id  uuid,
  agent       text not null,
  payer_id    text,
  status      text not null default 'pending_review'
              check (status in (
                'pending_review', 'approved', 'denied', 'expired', 'superseded'
              )),
  input_json  jsonb not null default '{}'::jsonb,
  output_json jsonb not null default '{}'::jsonb,
  meta        jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz
);

comment on column rcm.agent_runs.payer_id is
  'Logical ref to Supabase rcm.payer_network.payer_id — no cross-DB FK.';

create index if not exists idx_agent_runs_agent_created
  on rcm.agent_runs (practice_id, agent, created_at desc);

create index if not exists idx_agent_runs_patient_created
  on rcm.agent_runs (practice_id, patient_id, created_at desc);

create index if not exists idx_agent_runs_status
  on rcm.agent_runs (practice_id, status, created_at desc);

drop trigger if exists trg_agent_runs_updated_at on rcm.agent_runs;
create trigger trg_agent_runs_updated_at
  before update on rcm.agent_runs
  for each row execute function platform.set_updated_at();

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table platform.sla_policies enable row level security;
alter table platform.sla_policies force row level security;
drop policy if exists tenant_isolation on platform.sla_policies;
create policy tenant_isolation on platform.sla_policies
  for all
  using (
    platform.rls_bypass()
    or practice_id is null
    or practice_id = platform.current_practice_id()
  )
  with check (
    platform.rls_bypass()
    or practice_id is null
    or practice_id = platform.current_practice_id()
  );

alter table agents.rcm_tasks enable row level security;
alter table agents.rcm_tasks force row level security;
drop policy if exists tenant_isolation on agents.rcm_tasks;
create policy tenant_isolation on agents.rcm_tasks
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table agents.rcm_task_events enable row level security;
alter table agents.rcm_task_events force row level security;
drop policy if exists tenant_isolation on agents.rcm_task_events;
create policy tenant_isolation on agents.rcm_task_events
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table agents.agent_decisions enable row level security;
alter table agents.agent_decisions force row level security;
drop policy if exists tenant_isolation on agents.agent_decisions;
create policy tenant_isolation on agents.agent_decisions
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table feedback.decision_feedback enable row level security;
alter table feedback.decision_feedback force row level security;
drop policy if exists tenant_isolation on feedback.decision_feedback;
create policy tenant_isolation on feedback.decision_feedback
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.agent_runs enable row level security;
alter table rcm.agent_runs force row level security;
drop policy if exists tenant_isolation on rcm.agent_runs;
create policy tenant_isolation on rcm.agent_runs
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

commit;
