-- =============================================================================
-- 053 — Supabase-only pilot bridge for FastAPI dashboard (BFF + psycopg path)
-- =============================================================================
-- Adds platform.* tables and practice_id tenancy columns expected by app/dashboard
-- when DATABASE_URL points at this Supabase Postgres (single-DB pilot).
-- Existing rows backfill to vgd_mock_brooklyn (mock clinic seed).
-- =============================================================================

begin;

set local lock_timeout = '5s';
set local search_path = public, extensions;

create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- platform schema + helpers
-- ---------------------------------------------------------------------------
create schema if not exists platform;

create or replace function platform.current_practice_id()
returns text language sql stable as $$
  select nullif(current_setting('app.practice_id', true), '');
$$;

create or replace function platform.rls_bypass()
returns boolean language sql stable as $$
  select coalesce(current_setting('app.bypass_rls', true), '') = 'true';
$$;

create or replace function platform.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

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

create table if not exists platform.worker_state (
  key        text primary key,
  value      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists platform.pilot_shadow_events (
  id              uuid primary key default gen_random_uuid(),
  practice_id     text not null,
  event_type      text not null,
  source          text not null default 'system',
  patient_id      uuid,
  external_ref    text,
  agent_payload   jsonb not null default '{}'::jsonb,
  human_label     jsonb,
  match_status    text not null default 'pending',
  metadata        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);

create index if not exists idx_pilot_shadow_events_practice_created
  on platform.pilot_shadow_events (practice_id, created_at desc);

-- ---------------------------------------------------------------------------
-- practice_id tenancy columns (backfill mock clinic)
-- ---------------------------------------------------------------------------
do $add_practice_id$
declare
  t record;
  default_practice text := 'vgd_mock_brooklyn';
begin
  for t in
    select * from (values
      ('rcm', 'eligibility_requests'),
      ('rcm', 'eligibility_checks'),
      ('rcm', 'eligibility_request_events'),
      ('rcm', 'eligibility_agent_settings'),
      ('rcm', 'procedure_estimates'),
      ('rcm', 'claims'),
      ('rcm', 'denied_claims'),
      ('rcm', 'accepted_claims'),
      ('agents', 'rcm_tasks'),
      ('agents', 'rcm_task_events'),
      ('agents', 'agent_decisions'),
      ('agents', 'claim_intake_snapshot'),
      ('audit', 'audit_logs'),
      ('logs', 'eligibility_audit_log'),
      ('patient', 'patients'),
      ('patient', 'providers'),
      ('patient', 'encounters')
    ) as x(schema_name, table_name)
  loop
    execute format(
      'alter table %I.%I add column if not exists practice_id text',
      t.schema_name, t.table_name
    );
    execute format(
      'update %I.%I set practice_id = %L where practice_id is null',
      t.schema_name, t.table_name, default_practice
    );
    begin
      execute format(
        'alter table %I.%I alter column practice_id set not null',
        t.schema_name, t.table_name
      );
    exception when others then
      raise notice 'practice_id NOT NULL skipped for %.%', t.schema_name, t.table_name;
    end;
  end loop;
end;
$add_practice_id$;

-- eligibility_agent_settings: singleton row → tag with default practice_id
do $settings$
begin
  update rcm.eligibility_agent_settings
  set practice_id = 'vgd_mock_brooklyn'
  where practice_id is null;
end;
$settings$;

-- OpenDental connections (per-clinic poller control)
create table if not exists rcm.opendental_connections (
  id                     uuid primary key default gen_random_uuid(),
  practice_id            text not null unique,
  display_name           text,
  base_url               text not null default 'https://api.opendental.com/api/v1',
  customer_key_ref       text,
  poll_enabled           boolean not null default false,
  poll_interval_seconds  numeric not null default 60,
  poll_window_days       integer not null default 0,
  cdt_codes              text not null default 'D1110',
  writeback_enabled      boolean not null default false,
  last_poll_at           timestamptz,
  last_poll_status       text,
  last_poll_appointments integer,
  last_error             text,
  health_status          text,
  health_checked_at      timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

drop trigger if exists trg_opendental_connections_updated_at on rcm.opendental_connections;
create trigger trg_opendental_connections_updated_at
  before update on rcm.opendental_connections
  for each row execute function platform.set_updated_at();

-- Realtime NOTIFY for dashboard SSE
create or replace function platform.notify_rcm_event() returns trigger
language plpgsql as $$
declare
  rec jsonb := to_jsonb(new);
  payload text;
begin
  payload := jsonb_build_object(
    'source', tg_table_schema || '.' || tg_table_name,
    'op', tg_op,
    'practice_id', rec->>'practice_id',
    'id', rec->>'id',
    'request_id', coalesce(rec->>'request_id', rec->>'id'),
    'status', rec->>'status',
    'event_type', rec->>'event_type',
    'run_type', rec->>'run_type'
  )::text;
  if octet_length(payload) < 7900 then
    perform pg_notify('rcm_events', payload);
  end if;
  return new;
end;
$$;

drop trigger if exists trg_notify_eligibility_requests on rcm.eligibility_requests;
create trigger trg_notify_eligibility_requests
  after insert or update on rcm.eligibility_requests
  for each row execute function platform.notify_rcm_event();

drop trigger if exists trg_notify_eligibility_request_events on rcm.eligibility_request_events;
create trigger trg_notify_eligibility_request_events
  after insert on rcm.eligibility_request_events
  for each row execute function platform.notify_rcm_event();

drop trigger if exists trg_notify_pipeline_runs on platform.pipeline_runs;
create trigger trg_notify_pipeline_runs
  after insert or update on platform.pipeline_runs
  for each row execute function platform.notify_rcm_event();

drop trigger if exists trg_notify_opendental_connections on rcm.opendental_connections;
create trigger trg_notify_opendental_connections
  after insert or update on rcm.opendental_connections
  for each row execute function platform.notify_rcm_event();

commit;
