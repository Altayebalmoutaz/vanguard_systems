-- =============================================================================
-- Neon 002 — Eligibility subsystem
-- =============================================================================
-- PHI plane · port of rcm.eligibility_* + logs.eligibility_audit_log
-- No Edge/webhook triggers — worker enqueues via platform.pipeline_runs (Phase 3)
-- =============================================================================

begin;

set local lock_timeout = '5s';

create schema if not exists logs;
create schema if not exists rcm;

-- ---------------------------------------------------------------------------
-- rcm.eligibility_checks (created before requests for FK order)
-- ---------------------------------------------------------------------------
create table if not exists rcm.eligibility_checks (
  id                    uuid primary key default gen_random_uuid(),
  practice_id           text not null,
  patient_id            uuid not null,
  payer_id              text not null,
  checked_at            timestamptz not null default now(),
  coverage_order        text check (coverage_order in ('primary', 'secondary')),
  is_active             boolean,
  inactive_reason       text,
  is_covered            boolean,
  in_network            boolean,
  coverage_percent      numeric,
  copay                 numeric,
  coinsurance           numeric,
  deductible_total      numeric,
  deductible_met        numeric,
  deductible_remaining  numeric,
  annual_max_total      numeric,
  annual_max_used       numeric,
  annual_max_remaining  numeric,
  has_secondary         boolean default false,
  secondary_payer_id    text,
  raw_response          jsonb,
  response_complete     boolean,
  missing_fields        text[],
  normalization_version text default '1.0',
  routing_status        text,
  integrity_warnings    text[],
  created_at            timestamptz not null default now()
);

create index if not exists idx_eligibility_checks_patient_checked
  on rcm.eligibility_checks (practice_id, patient_id, payer_id, checked_at desc);

-- ---------------------------------------------------------------------------
-- rcm.eligibility_requests
-- ---------------------------------------------------------------------------
create table if not exists rcm.eligibility_requests (
  id                    uuid primary key default gen_random_uuid(),
  practice_id           text not null,
  patient_id            uuid not null,
  created_by            uuid,
  first_name            text not null,
  last_name             text not null,
  dob                   date not null,
  subscriber_id         text not null,
  primary_payer_id      text not null,
  secondary_payer_id    text,
  plan_id               text,
  cdt_codes             text[] not null default '{}'::text[],
  trigger_event         text not null default 'APPOINTMENT_BOOKED'
                        check (trigger_event in (
                          'NEW_PATIENT', 'APPOINTMENT_BOOKED',
                          'PRE_APPOINTMENT', 'BATCH_SWEEP'
                        )),
  status                text not null default 'queued'
                        check (status in (
                          'queued', 'processing', 'retrying',
                          'completed', 'failed', 'needs_attention'
                        )),
  primary_check_id      uuid references rcm.eligibility_checks(id) on delete set null,
  secondary_check_id    uuid references rcm.eligibility_checks(id) on delete set null,
  input_json            jsonb not null default '{}'::jsonb,
  output_json           jsonb not null default '{}'::jsonb,
  error_message         text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  completed_at          timestamptz,
  attempt_count         integer not null default 0,
  max_attempts          integer not null default 3,
  started_at            timestamptz,
  last_attempt_at       timestamptz,
  locked_at             timestamptz,
  locked_by             text,
  next_retry_at         timestamptz,
  failure_category      text check (failure_category is null or failure_category in (
                          'config_error', 'agent_error', 'payer_error',
                          'timeout', 'validation_error', 'unknown'
                        )),
  status_reason         text,
  idempotency_key       text,
  parent_request_id     uuid references rcm.eligibility_requests(id) on delete set null,
  agent_http_status     integer,
  agent_duration_ms     integer,
  edge_duration_ms      integer,
  error_code            text,
  suggested_action      text,
  priority              text not null default 'medium'
                        check (priority in ('low', 'medium', 'high')),
  appointment_date      date,
  estimated_claim_value numeric,
  coverage_status       text check (coverage_status is null or coverage_status in (
                          'active', 'inactive', 'unknown'
                        )),
  appointment_time      time,
  provider_name         text
);

create unique index if not exists idx_eligibility_requests_idempotency
  on rcm.eligibility_requests (practice_id, idempotency_key)
  where idempotency_key is not null;

create index if not exists idx_eligibility_requests_retry
  on rcm.eligibility_requests (status, next_retry_at, attempt_count)
  where status in ('retrying', 'queued');

create index if not exists idx_eligibility_requests_parent
  on rcm.eligibility_requests (parent_request_id);

create index if not exists idx_eligibility_requests_patient_created
  on rcm.eligibility_requests (practice_id, patient_id, created_at desc);

create index if not exists idx_eligibility_requests_primary_check
  on rcm.eligibility_requests (primary_check_id);

create index if not exists idx_eligibility_requests_status_created
  on rcm.eligibility_requests (practice_id, status, created_at desc);

create index if not exists idx_eligibility_requests_priority_schedule
  on rcm.eligibility_requests (practice_id, priority, appointment_date, estimated_claim_value desc);

drop trigger if exists trg_eligibility_requests_updated_at on rcm.eligibility_requests;
create trigger trg_eligibility_requests_updated_at
  before update on rcm.eligibility_requests
  for each row execute function platform.set_updated_at();

-- ---------------------------------------------------------------------------
-- rcm.eligibility_request_events
-- ---------------------------------------------------------------------------
create table if not exists rcm.eligibility_request_events (
  id          uuid primary key default gen_random_uuid(),
  practice_id text not null,
  request_id  uuid not null references rcm.eligibility_requests(id) on delete cascade,
  event_type  text not null,
  detail      jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);

create index if not exists idx_eligibility_request_events_request_created
  on rcm.eligibility_request_events (request_id, created_at desc);

-- ---------------------------------------------------------------------------
-- rcm.procedure_estimates
-- ---------------------------------------------------------------------------
create table if not exists rcm.procedure_estimates (
  id                      uuid primary key default gen_random_uuid(),
  practice_id             text not null,
  eligibility_check_id    uuid not null references rcm.eligibility_checks(id) on delete cascade,
  cdt_code                text,
  procedure_covered       boolean,
  waiting_period_end      date,
  waiting_period_category text,
  non_covered_reason      text,
  allowed_amount          numeric,
  insurance_pays          numeric,
  patient_responsibility  numeric,
  created_at              timestamptz not null default now()
);

create index if not exists idx_procedure_estimates_check
  on rcm.procedure_estimates (eligibility_check_id);

-- ---------------------------------------------------------------------------
-- rcm.eligibility_agent_settings (singleton per practice)
-- ---------------------------------------------------------------------------
create table if not exists rcm.eligibility_agent_settings (
  practice_id        text primary key,
  auto_check_enabled boolean not null default true,
  auto_retry_enabled boolean not null default true,
  last_sync_at       timestamptz,
  next_retry_at      timestamptz,
  updated_at         timestamptz not null default now()
);

drop trigger if exists trg_eligibility_agent_settings_updated_at on rcm.eligibility_agent_settings;
create trigger trg_eligibility_agent_settings_updated_at
  before update on rcm.eligibility_agent_settings
  for each row execute function platform.set_updated_at();

-- ---------------------------------------------------------------------------
-- logs.eligibility_audit_log
-- ---------------------------------------------------------------------------
create table if not exists logs.eligibility_audit_log (
  id          uuid primary key default gen_random_uuid(),
  practice_id text not null,
  patient_id  uuid,
  event_type  text,
  detail      jsonb,
  created_at  timestamptz not null default now()
);

create index if not exists idx_eligibility_audit_patient
  on logs.eligibility_audit_log (practice_id, patient_id, created_at desc);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table rcm.eligibility_checks enable row level security;
alter table rcm.eligibility_checks force row level security;
drop policy if exists tenant_isolation on rcm.eligibility_checks;
create policy tenant_isolation on rcm.eligibility_checks
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.eligibility_requests enable row level security;
alter table rcm.eligibility_requests force row level security;
drop policy if exists tenant_isolation on rcm.eligibility_requests;
create policy tenant_isolation on rcm.eligibility_requests
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.eligibility_request_events enable row level security;
alter table rcm.eligibility_request_events force row level security;
drop policy if exists tenant_isolation on rcm.eligibility_request_events;
create policy tenant_isolation on rcm.eligibility_request_events
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.procedure_estimates enable row level security;
alter table rcm.procedure_estimates force row level security;
drop policy if exists tenant_isolation on rcm.procedure_estimates;
create policy tenant_isolation on rcm.procedure_estimates
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.eligibility_agent_settings enable row level security;
alter table rcm.eligibility_agent_settings force row level security;
drop policy if exists tenant_isolation on rcm.eligibility_agent_settings;
create policy tenant_isolation on rcm.eligibility_agent_settings
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table logs.eligibility_audit_log enable row level security;
alter table logs.eligibility_audit_log force row level security;
drop policy if exists tenant_isolation on logs.eligibility_audit_log;
create policy tenant_isolation on logs.eligibility_audit_log
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

commit;
