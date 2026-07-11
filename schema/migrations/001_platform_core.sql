-- =============================================================================
-- Neon 001 — Platform core (patient spine, audit, pipeline queue, RBAC)
-- =============================================================================
-- PHI plane · idempotent · no Supabase roles · FORCE RLS on tenant tables
-- Companion: docs/phi-plane-table-inventory.md §7
-- =============================================================================

begin;

set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- Extensions & schemas
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto;

create schema if not exists platform;
create schema if not exists patient;
create schema if not exists audit;

comment on schema platform is 'Cross-cutting PHI-plane platform tables (pipeline, RBAC, SLA).';
comment on schema patient is 'Patient identity and clinical encounters.';
comment on schema audit is 'Unified audit trail (writer ships Phase 3).';

-- ---------------------------------------------------------------------------
-- Tenancy helpers (FastAPI sets app.practice_id per request)
-- ---------------------------------------------------------------------------
create or replace function platform.current_practice_id()
returns text
language sql
stable
as $$
  select nullif(current_setting('app.practice_id', true), '');
$$;

create or replace function platform.rls_bypass()
returns boolean
language sql
stable
as $$
  select coalesce(current_setting('app.bypass_rls', true), '') = 'true';
$$;

create or replace function platform.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- RBAC (Supabase Auth user_id → practice role; practice registry on Supabase)
-- ---------------------------------------------------------------------------
create table if not exists platform.user_practice_roles (
  user_id     uuid not null,
  practice_id text not null,
  role        text not null
              check (role in ('admin', 'billing_lead', 'front_office', 'read_only')),
  created_at  timestamptz not null default now(),
  primary key (user_id, practice_id)
);

create index if not exists idx_user_practice_roles_practice
  on platform.user_practice_roles (practice_id);

-- ---------------------------------------------------------------------------
-- Durable pipeline queue skeleton (Phase 3 worker)
-- ---------------------------------------------------------------------------
create table if not exists platform.pipeline_runs (
  id               uuid primary key default gen_random_uuid(),
  practice_id      text not null,
  run_type         text not null,
  status           text not null default 'queued'
                   check (status in (
                     'queued', 'processing', 'retrying',
                     'completed', 'failed', 'cancelled'
                   )),
  payload          jsonb not null default '{}'::jsonb,
  result           jsonb,
  error_message    text,
  error_code       text,
  attempt_count    integer not null default 0,
  max_attempts     integer not null default 3,
  locked_at        timestamptz,
  locked_by        text,
  next_retry_at    timestamptz,
  idempotency_key  text,
  source_entity    text,
  source_entity_id uuid,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  started_at       timestamptz,
  completed_at     timestamptz
);

create unique index if not exists idx_pipeline_runs_idempotency
  on platform.pipeline_runs (practice_id, idempotency_key)
  where idempotency_key is not null;

create index if not exists idx_pipeline_runs_queue
  on platform.pipeline_runs (status, next_retry_at, created_at)
  where status in ('queued', 'retrying');

create index if not exists idx_pipeline_runs_practice_status
  on platform.pipeline_runs (practice_id, status, created_at desc);

drop trigger if exists trg_pipeline_runs_updated_at on platform.pipeline_runs;
create trigger trg_pipeline_runs_updated_at
  before update on platform.pipeline_runs
  for each row execute function platform.set_updated_at();

-- ---------------------------------------------------------------------------
-- patient.patients
-- ---------------------------------------------------------------------------
create table if not exists patient.patients (
  id           uuid primary key default gen_random_uuid(),
  practice_id  text not null,
  name         text not null,
  dob          date,
  insurance_id text,
  payer        text,
  created_at   timestamptz not null default now(),
  unique (practice_id, insurance_id)
);

create index if not exists idx_patients_practice
  on patient.patients (practice_id);

-- ---------------------------------------------------------------------------
-- patient.providers
-- ---------------------------------------------------------------------------
create table if not exists patient.providers (
  id           uuid primary key default gen_random_uuid(),
  practice_id  text not null,
  full_name    text,
  specialty    text,
  created_at   timestamptz not null default now()
);

create index if not exists idx_providers_practice
  on patient.providers (practice_id);

-- ---------------------------------------------------------------------------
-- patient.encounters
-- ---------------------------------------------------------------------------
create table if not exists patient.encounters (
  id              uuid primary key default gen_random_uuid(),
  practice_id     text not null,
  patient_id      uuid not null references patient.patients(id) on delete cascade,
  provider_id     uuid references patient.providers(id) on delete set null,
  clinical_note   text,
  procedures_json jsonb,
  attachments     jsonb,
  status          text not null default 'pending',
  created_at      timestamptz not null default now()
);

create index if not exists idx_encounters_patient
  on patient.encounters (patient_id);

create index if not exists idx_encounters_practice
  on patient.encounters (practice_id, created_at desc);

-- ---------------------------------------------------------------------------
-- audit.audit_logs (unified writer — Phase 3)
-- ---------------------------------------------------------------------------
create table if not exists audit.audit_logs (
  id           uuid primary key default gen_random_uuid(),
  practice_id  text not null,
  entity_type  text,
  entity_id    uuid,
  action       text,
  performed_by text,
  metadata     jsonb,
  created_at   timestamptz not null default now()
);

create index if not exists idx_audit_logs_practice_created
  on audit.audit_logs (practice_id, created_at desc);

create index if not exists idx_audit_logs_entity
  on audit.audit_logs (entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- RLS — FORCE so even the app DB role is scoped
-- ---------------------------------------------------------------------------
alter table platform.user_practice_roles enable row level security;
alter table platform.user_practice_roles force row level security;
drop policy if exists tenant_isolation on platform.user_practice_roles;
create policy tenant_isolation on platform.user_practice_roles
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table platform.pipeline_runs enable row level security;
alter table platform.pipeline_runs force row level security;
drop policy if exists tenant_isolation on platform.pipeline_runs;
create policy tenant_isolation on platform.pipeline_runs
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table patient.patients enable row level security;
alter table patient.patients force row level security;
drop policy if exists tenant_isolation on patient.patients;
create policy tenant_isolation on patient.patients
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table patient.providers enable row level security;
alter table patient.providers force row level security;
drop policy if exists tenant_isolation on patient.providers;
create policy tenant_isolation on patient.providers
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table patient.encounters enable row level security;
alter table patient.encounters force row level security;
drop policy if exists tenant_isolation on patient.encounters;
create policy tenant_isolation on patient.encounters
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table audit.audit_logs enable row level security;
alter table audit.audit_logs force row level security;
drop policy if exists tenant_isolation on audit.audit_logs;
create policy tenant_isolation on audit.audit_logs
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

commit;
