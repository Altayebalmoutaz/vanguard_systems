-- =============================================================================
-- Vanguard MD — consolidated production baseline (schema only)
-- =============================================================================
-- This file is a single, authoritative snapshot of the LIVE Supabase schema as
-- reconciled on 2026-06-15 by inspecting the running database (not the old
-- 001..044 migration files, several of which were never applied or drifted from
-- production). The superseded files are preserved under `legacy/` for history.
--
-- Scope: DDL only — schemas, tables, columns, constraints, indexes, triggers,
-- RLS + policies, functions, compatibility views, grants, and realtime
-- publication membership. Reference/seed DATA is intentionally excluded; the
-- original seed migrations remain in `legacy/` if a dataset reload is needed.
--
-- Deliberately NOT reproduced (documented in legacy/RECONCILIATION.md):
--   * n8n webhook triggers on logs.coding_log and rcm.denied_claims
--     (hardcoded ngrok URL; treated as ephemeral dev integration).
--   * migration 037 (eligibility RLS hardening) and 038 (HMAC webhook signing):
--     present in the repo but NEVER applied to production. The live webhook
--     function below is the 033-era version (no HMAC); eligibility_checks /
--     procedure_estimates have RLS disabled, matching production.
--
-- Architecture: real tables live in domain schemas (patient, agents, analytics,
-- audit, feedback, logs, rcm); `public` holds backward-compatible views.
-- =============================================================================

begin;

set local lock_timeout = '5s';
set local statement_timeout = '0';

-- Resolve unqualified extension functions (uuid_generate_v4, vector ops) the
-- same way production does: Supabase keeps `extensions` on the search_path, so
-- column defaults such as uuid_generate_v4() bind to extensions.* and render
-- unqualified. Setting it here makes a fresh apply match production exactly.
set local search_path = public, extensions;

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
create extension if not exists "uuid-ossp" with schema extensions;   -- uuid_generate_v4()
create extension if not exists pgcrypto with schema extensions;       -- gen_random_uuid()
create extension if not exists vector with schema public;             -- cdt_codes.embedding
-- pg_net (net.http_post) is provided by Supabase for the eligibility webhook.

-- ---------------------------------------------------------------------------
-- Domain schemas
-- ---------------------------------------------------------------------------
create schema if not exists rcm;
create schema if not exists patient;
create schema if not exists agents;
create schema if not exists analytics;
create schema if not exists logs;
create schema if not exists audit;
create schema if not exists feedback;

grant usage on schema rcm, patient, agents, analytics, logs, audit, feedback
  to anon, authenticated, service_role;

-- ===========================================================================
-- patient domain
-- ===========================================================================
create table if not exists patient.patients (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  dob          date,
  insurance_id text unique,
  payer        text,
  created_at   timestamptz default now()
);

create table if not exists patient.providers (
  id         uuid primary key default uuid_generate_v4(),
  full_name  text,
  specialty  text,
  created_at timestamp default now()
);

create table if not exists patient.encounters (
  id             uuid primary key default gen_random_uuid(),
  patient_id     uuid references patient.patients(id) on delete cascade,
  provider_id    uuid references patient.providers(id) on delete set null,
  clinical_note  text,
  procedures_json jsonb,
  attachments    jsonb,
  status         text default 'pending',
  created_at     timestamp default now()
);
create index if not exists idx_encounters_patient on patient.encounters (patient_id);

-- ===========================================================================
-- analytics domain (reference / RAG)
-- ===========================================================================
create table if not exists analytics.rule_sources (
  id             bigserial primary key,
  source_slug    text not null unique,
  title          text not null,
  payer_name     text not null,
  source_file    text,
  effective_date date,
  ingested_at    timestamptz not null default now()
);

create table if not exists analytics.cdt_code_master (
  code              text primary key,
  short_description text not null,
  section_label     text,
  source_id         bigint not null references analytics.rule_sources(id) on delete cascade,
  source_page       integer,
  raw_text          text,
  created_at        timestamptz not null default now()
);

create table if not exists analytics.cdt_codes (
  code                varchar(10) primary key,
  description         text not null,
  category            text,
  keyword             text,
  requires_tooth      boolean,
  requires_surfaces   boolean,
  requires_radiograph boolean,
  fee_schedule        numeric,
  specificity_score   integer,
  subcategory         text,
  effective_date      date,
  status              text,
  notes               text,
  source_file         text,
  updated_at          timestamptz not null default now(),
  embedding           vector(1024)
);
comment on column analytics.cdt_codes.embedding is 'Jina v5 passage embedding (1024); scripts/embed_cdt_jina_backfill.py';
create index if not exists cdt_codes_embedding_hnsw
  on analytics.cdt_codes using hnsw (embedding vector_cosine_ops)
  where embedding is not null;

create table if not exists analytics.codes (
  id          uuid primary key default uuid_generate_v4(),
  code        text not null,
  description text,
  code_type   text,
  created_at  timestamp default now()
);
create index if not exists idx_codes_code on analytics.codes (code);

create table if not exists analytics.coding_rules (
  id           uuid primary key default uuid_generate_v4(),
  rule_name    text,
  description  text,
  conditions   jsonb,
  output_codes jsonb,
  priority     integer default 1,
  is_active    boolean default true,
  created_at   timestamp default now()
);

create table if not exists analytics.hio_rules (
  id             serial primary key,
  code           text not null unique,
  category       text not null,
  payer          text not null,
  description_en text,
  description_ar text,
  action         text,
  source_url     text
);
create index if not exists idx_hio_rules_category on analytics.hio_rules (category);
create index if not exists idx_hio_rules_payer on analytics.hio_rules (payer);

create table if not exists analytics.icd10_codes (
  code        text primary key,
  description text,
  updated_at  timestamptz not null default now()
);

create table if not exists analytics.icd10_dental_gem_axis (
  record_id          text primary key,
  icd10_code_compact text not null,
  icd10_code         text not null,
  icd10_description  text not null,
  icd9_code_compact  text not null,
  icd9_code          text not null,
  icd9_description   text not null,
  axis_group         text not null,
  flag_1             text not null,
  flag_2             text not null,
  flag_3             text not null,
  flag_4             text not null,
  flag_5             text not null,
  gem_axis           text not null,
  combined_line      text not null,
  effective_at       timestamptz,
  notes              text
);

-- ===========================================================================
-- agents domain
-- ===========================================================================
create table if not exists agents.agents (
  id          uuid primary key default uuid_generate_v4(),
  name        text,
  description text,
  version     text,
  is_active   boolean default true,
  created_at  timestamp default now()
);

create table if not exists agents.agent_decisions (
  id             uuid primary key default uuid_generate_v4(),
  encounter_id   uuid references patient.encounters(id) on delete cascade,
  agent_name     text,
  input_snapshot jsonb,
  reasoning      text,
  output         jsonb,
  confidence     double precision,
  status         text default 'pending',
  created_at     timestamp default now(),
  agent_id       uuid references agents.agents(id)
);
create index if not exists idx_decisions_encounter on agents.agent_decisions (encounter_id);

create table if not exists agents.rcm_tasks (
  id                 uuid primary key default gen_random_uuid(),
  backend_record_id  text not null default '',
  backend_claim_id   text not null default '',
  task_type          text not null default 'Full RCM pipeline',
  patient_name       text not null,
  patient_dob        text,
  payer              text,
  clinical_note      text not null default '',
  demographics_block text,
  ai_codes           text[] default '{}'::text[],
  ai_summary         text,
  confidence         double precision,
  status             text not null default 'pending',
  biller_edited_codes text[],
  pipeline_json      jsonb,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz
);
create index if not exists rcm_tasks_status_created_idx on agents.rcm_tasks (status, created_at desc);

create table if not exists agents.rcm_task_events (
  id          uuid primary key default gen_random_uuid(),
  task_id     uuid not null references agents.rcm_tasks(id) on delete cascade,
  event_type  text not null,
  actor_label text not null default 'system',
  payload     jsonb default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists rcm_task_events_task_created_idx on agents.rcm_task_events (task_id, created_at desc);

create table if not exists agents.claim_intake_snapshot (
  id                 bigserial primary key,
  encounter_id       text not null unique,
  schema_version     integer not null default 1,
  intake_status      text not null default 'draft'
                     check (intake_status in ('draft', 'ready', 'submitted', 'archived')),
  ready_for_claim    boolean not null default false,
  validation_errors  jsonb not null default '[]'::jsonb check (jsonb_typeof(validation_errors) = 'array'),
  source_system      text not null default 'frontdesk_ui',
  created_by         text,
  patient_id         text,
  provider_id        text,
  insurance_id       text,
  patient            jsonb not null default '{}'::jsonb check (jsonb_typeof(patient) = 'object'),
  subscriber         jsonb not null default '{}'::jsonb check (jsonb_typeof(subscriber) = 'object'),
  payer              jsonb not null default '{}'::jsonb check (jsonb_typeof(payer) = 'object'),
  billing_provider   jsonb not null default '{}'::jsonb check (jsonb_typeof(billing_provider) = 'object'),
  rendering_provider jsonb not null default '{}'::jsonb check (jsonb_typeof(rendering_provider) = 'object'),
  claim_header       jsonb not null default '{}'::jsonb check (jsonb_typeof(claim_header) = 'object'),
  diagnosis_codes    jsonb not null default '[]'::jsonb check (jsonb_typeof(diagnosis_codes) = 'array'),
  service_lines      jsonb not null default '[]'::jsonb check (jsonb_typeof(service_lines) = 'array'),
  financials         jsonb not null default '{}'::jsonb check (jsonb_typeof(financials) = 'object'),
  coding_run_id      text,
  prior_auth_run_id  text,
  coding_output      jsonb not null default '{}'::jsonb,
  prior_auth_output  jsonb not null default '{}'::jsonb,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index if not exists claim_intake_snapshot_ready_idx on agents.claim_intake_snapshot (ready_for_claim, intake_status, updated_at desc);
create index if not exists claim_intake_snapshot_patient_id_idx on agents.claim_intake_snapshot (patient_id);
create index if not exists claim_intake_snapshot_provider_id_idx on agents.claim_intake_snapshot (provider_id);
create index if not exists claim_intake_snapshot_patient_name_idx on agents.claim_intake_snapshot (((patient ->> 'name')));
create index if not exists claim_intake_snapshot_payer_id_idx on agents.claim_intake_snapshot (((payer ->> 'payer_id')));
create index if not exists claim_intake_snapshot_diagnosis_codes_gin_idx on agents.claim_intake_snapshot using gin (diagnosis_codes jsonb_path_ops);
create index if not exists claim_intake_snapshot_service_lines_gin_idx on agents.claim_intake_snapshot using gin (service_lines jsonb_path_ops);

-- ===========================================================================
-- feedback domain
-- ===========================================================================
create table if not exists feedback.decision_feedback (
  id             uuid primary key default uuid_generate_v4(),
  decision_id    uuid references agents.agent_decisions(id) on delete cascade,
  human_override jsonb,
  reason         text,
  created_at     timestamp default now()
);
create index if not exists idx_feedback_decision on feedback.decision_feedback (decision_id);

-- ===========================================================================
-- audit domain
-- ===========================================================================
create table if not exists audit.audit_logs (
  id           uuid primary key default uuid_generate_v4(),
  entity_type  text,
  entity_id    uuid,
  action       text,
  performed_by text,
  metadata     jsonb,
  created_at   timestamp default now()
);

-- ===========================================================================
-- logs domain
-- ===========================================================================
create table if not exists logs.coding_log (
  id                       uuid primary key default gen_random_uuid(),
  created_at               timestamptz default now(),
  patient_id               text,
  department               text,
  coder_name               text,
  clinical_note            text,
  primary_diagnosis        text,
  note_summary             text,
  code_1                   text,
  code_2                   text,
  code_3                   text,
  overall_confidence       integer,
  requires_review          boolean,
  status                   text default 'pending',
  code_1_description       text,
  code_1_type              text,
  code_1_confidence_score  integer,
  code_1_confidence_level  text,
  code_1_reasoning         text,
  code_1_icd10_companion   text,
  code_2_description       text,
  code_2_type              text,
  code_2_confidence_score  integer,
  code_2_confidence_level  text,
  code_2_reasoning         text,
  code_2_icd10_companion   text,
  code_3_description       text,
  code_3_type              text,
  code_3_confidence_score  integer,
  code_3_confidence_level  text,
  code_3_reasoning         text,
  code_3_icd10_companion   text,
  suggested_codes          jsonb,
  ambiguity_flags          jsonb,
  flags_text               text,
  requires_human_review    boolean default false,
  review_reason            text,
  needs_review             boolean default false,
  additional_notes         text,
  analyzed_at              timestamptz,
  ai_assisted              boolean default false
);

create table if not exists logs.eligibility_audit_log (
  id         uuid primary key default gen_random_uuid(),
  patient_id uuid,
  event_type text,
  detail     jsonb,
  created_at timestamptz default now()
);
create index if not exists idx_eligibility_audit_patient on logs.eligibility_audit_log (patient_id, created_at desc);

-- ===========================================================================
-- rcm domain
-- ===========================================================================
create table if not exists rcm.payer_network (
  payer_id                   text primary key,
  trading_partner_service_id text not null unique,
  display_name               text,
  coverage_type              text not null check (coverage_type in ('dental', 'medical')),
  created_at                 timestamptz default now(),
  aliases                    jsonb not null default '[]'::jsonb
);
create index if not exists idx_payer_network_coverage on rcm.payer_network (coverage_type);

create table if not exists rcm.practices (
  practice_id text primary key,
  display_name text not null,
  billing_npi  text check (billing_npi is null or billing_npi ~ '^[0-9]{10}$'),
  city         text,
  state_code   text check (state_code is null or char_length(state_code) = 2),
  postal_code  text,
  notes        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table if not exists rcm.provider_payer_network (
  id                            uuid primary key default gen_random_uuid(),
  practice_id                   text not null references rcm.practices(practice_id) on delete cascade,
  rendering_provider_npi        text not null check (rendering_provider_npi ~ '^[0-9]{10}$'),
  payer_id                      text not null references rcm.payer_network(payer_id) on delete cascade,
  provider_service_location_key text,
  in_network_for_fees           boolean not null,
  contract_label                text,
  notes                         text,
  effective_from                date not null default (timezone('utc', now()))::date,
  effective_to                  date check (effective_to is null or effective_to >= effective_from),
  created_at                    timestamptz not null default now(),
  updated_at                    timestamptz not null default now()
);
create index if not exists idx_provider_payer_network_lookup
  on rcm.provider_payer_network (practice_id, rendering_provider_npi, payer_id, effective_from desc);
create unique index if not exists idx_provider_payer_network_row_identity
  on rcm.provider_payer_network (practice_id, rendering_provider_npi, payer_id, coalesce(provider_service_location_key, ''), effective_from);

create table if not exists rcm.agent_runs (
  id          uuid primary key default gen_random_uuid(),
  patient_id  uuid,
  practice_id text,
  agent       text not null,
  payer_id    text references rcm.payer_network(payer_id) on delete set null,
  status      text not null default 'pending_review',
  input_json  jsonb not null default '{}'::jsonb,
  output_json jsonb not null default '{}'::jsonb,
  meta        jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists idx_agent_runs_agent_created on rcm.agent_runs (agent, created_at desc);
create index if not exists idx_agent_runs_patient_created on rcm.agent_runs (patient_id, created_at desc);

create table if not exists rcm.claims (
  id                uuid primary key default gen_random_uuid(),
  patient_id        uuid references patient.patients(id),
  visit_date        date,
  provider          text,
  raw_note          text,
  status            text default 'draft',
  cdt_lines         jsonb,
  icd10_codes       jsonb,
  compliance_status text,
  compliance_flags  jsonb,
  compliance_note   text,
  coded_at          timestamptz,
  created_at        timestamptz default now()
);

create table if not exists rcm.accepted_claims (
  id                  uuid primary key default gen_random_uuid(),
  task_id             uuid not null unique references agents.rcm_tasks(id) on delete cascade,
  backend_record_id   text not null,
  backend_claim_id    text not null,
  patient_name        text not null,
  payer               text,
  final_codes         text[],
  final_summary       text,
  confidence          double precision,
  source_pipeline_json jsonb,
  accepted_at         timestamptz not null default now()
);
create index if not exists accepted_claims_accepted_at_idx on rcm.accepted_claims (accepted_at desc);

create table if not exists rcm.denied_claims (
  id                  uuid primary key default gen_random_uuid(),
  created_at          timestamptz default now(),
  claim_reference     text,
  payer               text,
  provider_code       text,
  claim_details       text,
  root_cause          text,
  corrective_actions  text,
  corrected_diagnosis text,
  corrected_procedure text,
  coding_note         text,
  missing_documents   text,
  priority            text,
  recoverable_amount  text,
  appeal_deadline     text,
  confidence_score    integer,
  executive_summary   text,
  is_denial_valid     text,
  validity_reasoning  text,
  analyzed_at         timestamptz,
  status              text default 'pending'
);

create table if not exists rcm.eligibility_checks (
  id                   uuid primary key default gen_random_uuid(),
  patient_id           uuid not null,
  payer_id             text not null,
  checked_at           timestamptz not null default now(),
  coverage_order       text check (coverage_order in ('primary', 'secondary')),
  is_active            boolean,
  inactive_reason      text,
  is_covered           boolean,
  in_network           boolean,
  coverage_percent     numeric,
  copay                numeric,
  coinsurance          numeric,
  deductible_total     numeric,
  deductible_met       numeric,
  deductible_remaining numeric,
  annual_max_total     numeric,
  annual_max_used      numeric,
  annual_max_remaining numeric,
  has_secondary        boolean default false,
  secondary_payer_id   text,
  raw_response         jsonb,
  response_complete    boolean,
  missing_fields       text[],
  normalization_version text default '1.0',
  routing_status       text,
  integrity_warnings   text[],
  created_at           timestamptz default now()
);
create index if not exists idx_eligibility_checks_patient_checked
  on rcm.eligibility_checks (patient_id, payer_id, checked_at desc);

create table if not exists rcm.eligibility_requests (
  id                   uuid primary key default gen_random_uuid(),
  patient_id           uuid not null default gen_random_uuid(),
  first_name           text not null,
  last_name            text not null,
  dob                  date not null,
  subscriber_id        text not null,
  primary_payer_id     text not null,
  secondary_payer_id   text,
  plan_id              text,
  cdt_codes            text[] not null default '{}'::text[],
  trigger_event        text not null default 'APPOINTMENT_BOOKED'
                       check (trigger_event in ('NEW_PATIENT', 'APPOINTMENT_BOOKED', 'PRE_APPOINTMENT', 'BATCH_SWEEP')),
  status               text not null default 'queued'
                       check (status in ('queued', 'processing', 'retrying', 'completed', 'failed', 'needs_attention')),
  primary_check_id     uuid references rcm.eligibility_checks(id) on delete set null,
  secondary_check_id   uuid references rcm.eligibility_checks(id) on delete set null,
  input_json           jsonb not null default '{}'::jsonb,
  output_json          jsonb not null default '{}'::jsonb,
  error_message        text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  completed_at         timestamptz,
  attempt_count        integer not null default 0,
  max_attempts         integer not null default 3,
  started_at           timestamptz,
  last_attempt_at      timestamptz,
  locked_at            timestamptz,
  locked_by            text,
  next_retry_at        timestamptz,
  failure_category     text check (failure_category is null or failure_category in
                         ('config_error', 'agent_error', 'payer_error', 'timeout', 'validation_error', 'unknown')),
  status_reason        text,
  idempotency_key      text,
  parent_request_id    uuid references rcm.eligibility_requests(id) on delete set null,
  agent_http_status    integer,
  agent_duration_ms    integer,
  edge_duration_ms     integer,
  error_code           text,
  suggested_action     text,
  priority             text not null default 'medium' check (priority in ('low', 'medium', 'high')),
  appointment_date     date,
  estimated_claim_value numeric,
  coverage_status      text check (coverage_status is null or coverage_status in ('active', 'inactive', 'unknown')),
  appointment_time     time,
  provider_name        text
);
create unique index if not exists idx_eligibility_requests_idempotency
  on rcm.eligibility_requests (idempotency_key) where idempotency_key is not null;
create index if not exists idx_eligibility_requests_retry on rcm.eligibility_requests (status, next_retry_at, attempt_count);
create index if not exists idx_eligibility_requests_parent on rcm.eligibility_requests (parent_request_id);
create index if not exists idx_eligibility_requests_patient_created on rcm.eligibility_requests (patient_id, created_at desc);
create index if not exists idx_eligibility_requests_primary_check on rcm.eligibility_requests (primary_check_id);
create index if not exists idx_eligibility_requests_status_created on rcm.eligibility_requests (status, created_at desc);
create index if not exists idx_eligibility_requests_priority_schedule
  on rcm.eligibility_requests (priority, appointment_date, estimated_claim_value desc);

create table if not exists rcm.eligibility_request_events (
  id         uuid primary key default gen_random_uuid(),
  request_id uuid not null references rcm.eligibility_requests(id) on delete cascade,
  event_type text not null,
  detail     jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_eligibility_request_events_request_created
  on rcm.eligibility_request_events (request_id, created_at desc);

create table if not exists rcm.procedure_estimates (
  id                     uuid primary key default gen_random_uuid(),
  eligibility_check_id   uuid references rcm.eligibility_checks(id) on delete cascade,
  cdt_code               text,
  procedure_covered      boolean,
  waiting_period_end     date,
  waiting_period_category text,
  non_covered_reason     text,
  allowed_amount         numeric,
  insurance_pays         numeric,
  patient_responsibility numeric,
  created_at             timestamptz default now()
);
create index if not exists idx_procedure_estimates_check on rcm.procedure_estimates (eligibility_check_id);

create table if not exists rcm.eligibility_agent_settings (
  id                 boolean primary key default true check (id = true),
  auto_check_enabled boolean not null default true,
  auto_retry_enabled boolean not null default true,
  last_sync_at       timestamptz,
  next_retry_at      timestamptz,
  updated_at         timestamptz not null default now()
);

create table if not exists rcm.cdt_payer_rules (
  id          bigserial primary key,
  code        text not null references analytics.cdt_code_master(code) on delete cascade,
  payer_name  text not null,
  rule_type   text not null,
  rule_text   text not null,
  conditions  jsonb not null default '{}'::jsonb,
  source_id   bigint not null references analytics.rule_sources(id) on delete cascade,
  source_page integer,
  created_at  timestamptz not null default now()
);
create index if not exists cdt_payer_rules_code_idx on rcm.cdt_payer_rules (code);
create index if not exists cdt_payer_rules_type_idx on rcm.cdt_payer_rules (rule_type);

create table if not exists rcm.cdt_payer_rules_structured (
  id                          bigserial primary key,
  code                        text not null references analytics.cdt_code_master(code) on delete cascade,
  payer_name                  text not null,
  rule_type                   text not null,
  rule_text                   text not null,
  fee                         numeric,
  age_min                     integer,
  age_max                     integer,
  frequency_count             integer,
  frequency_period_months     integer,
  requires_prior_auth         boolean not null default false,
  requires_report             boolean not null default false,
  allowed_pos_codes           text[],
  restriction_exception_codes text[],
  not_billable_with_codes     text[],
  section_label               text,
  source_page                 integer,
  conditions                  jsonb not null default '{}'::jsonb,
  source_id                   bigint references analytics.rule_sources(id) on delete cascade,
  created_at                  timestamptz not null default now(),
  unique (code, payer_name)
);
create index if not exists cdt_rules_structured_code_idx on rcm.cdt_payer_rules_structured (code);
create index if not exists cdt_rules_structured_pa_idx on rcm.cdt_payer_rules_structured (requires_prior_auth);
create index if not exists cdt_rules_structured_type_idx on rcm.cdt_payer_rules_structured (rule_type);

create table if not exists rcm.payer_rules (
  id                    bigserial primary key,
  payer_name            text not null,
  payer_plan_scope      text not null default 'model_policy',
  rule_type             text not null,
  code                  text,
  transforms_to_code    text,
  related_codes         text[],
  rule_text             text not null,
  conditions            jsonb not null default '{}'::jsonb,
  contract_override_note boolean not null default false,
  source_id             bigint references analytics.rule_sources(id) on delete set null,
  source_page           integer,
  evidence_text         text,
  created_at            timestamptz not null default now()
);
create index if not exists payer_rules_code_idx on rcm.payer_rules (code);
create index if not exists payer_rules_payer_type_idx on rcm.payer_rules (payer_name, rule_type);

create table if not exists rcm.payer_fee_schedules (
  payer_id       text not null,
  cdt_code       text not null,
  contracted_fee numeric not null,
  effective_date date not null,
  primary key (payer_id, cdt_code, effective_date)
);
create index if not exists idx_payer_fee_schedules_lookup
  on rcm.payer_fee_schedules (payer_id, cdt_code, effective_date desc);

create table if not exists rcm.payer_prior_auth_rules (
  payer_id      text not null,
  cdt_code      text not null,
  auth_required boolean not null default false,
  primary key (payer_id, cdt_code)
);

-- ===========================================================================
-- Functions
-- ===========================================================================
create or replace function public.set_claim_intake_snapshot_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function rcm.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Eligibility queue → Edge Function dispatcher (033-era; NO HMAC signing — this
-- is the version actually running in production; migration 038's signed variant
-- was never applied).
create or replace function rcm.invoke_eligibility_request_processor()
returns trigger
language plpgsql
security definer
set search_path = rcm, public, vault, net, pg_temp
as $$
declare
  function_url text;
  anon_key text;
  service_role_key text;
  agent_url text;
begin
  if new.status <> 'queued' then
    return new;
  end if;

  select decrypted_secret into function_url from vault.decrypted_secrets
   where name = 'eligibility_dashboard_edge_function_url' limit 1;
  select decrypted_secret into anon_key from vault.decrypted_secrets
   where name = 'eligibility_dashboard_edge_function_anon_key' limit 1;
  select decrypted_secret into service_role_key from vault.decrypted_secrets
   where name = 'eligibility_dashboard_edge_function_service_role_key' limit 1;
  select decrypted_secret into agent_url from vault.decrypted_secrets
   where name = 'eligibility_agent_check_url' limit 1;

  if function_url is null or anon_key is null or service_role_key is null then
    update rcm.eligibility_requests
       set status = 'failed',
           error_message = 'Eligibility webhook is missing Edge Function Vault configuration.',
           failure_category = 'config_error',
           status_reason = 'Missing Edge Function Vault configuration'
     where id = new.id;
    return new;
  end if;

  if agent_url is null then
    update rcm.eligibility_requests
       set status = 'failed',
           error_message = 'Eligibility webhook is missing eligibility_agent_check_url Vault configuration.',
           failure_category = 'config_error',
           status_reason = 'Missing FastAPI URL Vault configuration'
     where id = new.id;
    return new;
  end if;

  perform net.http_post(
    url := function_url,
    body := jsonb_build_object(
      'type', 'INSERT',
      'table', tg_table_name,
      'schema', tg_table_schema,
      'record', to_jsonb(new),
      'old_record', null,
      'agent_url', agent_url,
      'supabase_key', service_role_key
    ),
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || anon_key,
      'apikey', anon_key
    ),
    timeout_milliseconds := 60000
  );

  return new;
end;
$$;

create or replace function public.get_claim_intake_snapshot(p_encounter_id text)
returns jsonb language sql stable as $$
  select to_jsonb(cis)
  from agents.claim_intake_snapshot cis
  where cis.encounter_id = p_encounter_id
  limit 1
$$;

-- NOTE: public.match_cdt_codes() is defined further down, AFTER the public
-- bridge views, because its SQL body references the public.cdt_codes /
-- public.payer_rules views (validated at creation time).

-- ===========================================================================
-- Triggers (n8n webhook triggers intentionally excluded — see header)
-- ===========================================================================
drop trigger if exists trg_claim_intake_snapshot_updated_at on agents.claim_intake_snapshot;
create trigger trg_claim_intake_snapshot_updated_at
  before update on agents.claim_intake_snapshot
  for each row execute function public.set_claim_intake_snapshot_updated_at();

drop trigger if exists trg_eligibility_agent_settings_updated_at on rcm.eligibility_agent_settings;
create trigger trg_eligibility_agent_settings_updated_at
  before update on rcm.eligibility_agent_settings
  for each row execute function rcm.set_updated_at();

drop trigger if exists trg_eligibility_requests_updated_at on rcm.eligibility_requests;
create trigger trg_eligibility_requests_updated_at
  before update on rcm.eligibility_requests
  for each row execute function rcm.set_updated_at();

drop trigger if exists trg_process_eligibility_request on rcm.eligibility_requests;
create trigger trg_process_eligibility_request
  after insert on rcm.eligibility_requests
  for each row when (new.status = 'queued')
  execute function rcm.invoke_eligibility_request_processor();

drop trigger if exists trg_retry_eligibility_request on rcm.eligibility_requests;
create trigger trg_retry_eligibility_request
  after update of status on rcm.eligibility_requests
  for each row when (new.status = 'queued' and old.status is distinct from new.status)
  execute function rcm.invoke_eligibility_request_processor();

-- ===========================================================================
-- public compatibility views (and rcm/agents/audit aliases)
-- ===========================================================================
create or replace view public.patients as select * from patient.patients;
create or replace view public.providers as select * from patient.providers;
create or replace view public.encounters as select * from patient.encounters;
create or replace view public.agents as select * from agents.agents;
create or replace view public.agent_decisions as select * from agents.agent_decisions;
create or replace view public.rcm_tasks as select * from agents.rcm_tasks;
create or replace view public.rcm_task_events as select * from agents.rcm_task_events;
create or replace view public.claim_intake_snapshot as select * from agents.claim_intake_snapshot;
create or replace view public.decision_feedback as select * from feedback.decision_feedback;
create or replace view public.audit_logs as select * from audit.audit_logs;
create or replace view public.coding_log as select * from logs.coding_log;
create or replace view public.eligibility_audit_log as select * from logs.eligibility_audit_log;
create or replace view public.codes as select * from analytics.codes;
create or replace view public.coding_rules as select * from analytics.coding_rules;
create or replace view public.hio_rules as select * from analytics.hio_rules;
create or replace view public.icd10_codes as select * from analytics.icd10_codes;
create or replace view public.icd10_dental_gem_axis as select * from analytics.icd10_dental_gem_axis;
create or replace view public.rule_sources as select * from analytics.rule_sources;
create or replace view public.cdt_code_master as select * from analytics.cdt_code_master;
create or replace view public.cdt_codes as select * from analytics.cdt_codes;
create or replace view public.agent_runs as select * from rcm.agent_runs;
create or replace view public.claims as select * from rcm.claims;
create or replace view public.accepted_claims as select * from rcm.accepted_claims;
create or replace view public.claim_submissions as select * from rcm.accepted_claims;
create or replace view public.denied_claims as select * from rcm.denied_claims;
create or replace view public.denials as select * from rcm.denied_claims;
create or replace view public.eligibility_checks as select * from rcm.eligibility_checks;
create or replace view public.eligibility_requests as select * from rcm.eligibility_requests;
create or replace view public.eligibility_request_events as select * from rcm.eligibility_request_events;
create or replace view public.eligibility_agent_settings as select * from rcm.eligibility_agent_settings;
create or replace view public.procedure_estimates as select * from rcm.procedure_estimates;
create or replace view public.payer_network as select * from rcm.payer_network;
create or replace view public.payer_rules as select * from rcm.payer_rules;
create or replace view public.payer_fee_schedules as select * from rcm.payer_fee_schedules;
create or replace view public.payer_prior_auth_rules as select * from rcm.payer_prior_auth_rules;
create or replace view public.cdt_payer_rules as select * from rcm.cdt_payer_rules;
create or replace view public.cdt_payer_rules_structured as select * from rcm.cdt_payer_rules_structured;
create or replace view public.practices as select * from rcm.practices;
create or replace view public.provider_payer_network as select * from rcm.provider_payer_network;

-- legacy alias retained by some clients (subset of analytics.cdt_codes columns)
create or replace view public.cdt_codes_master as
select code, description, category,
       null::text as subcategory, null::date as effective_date, null::text as status,
       null::text as notes, null::text as source_file, null::timestamptz as updated_at
from analytics.cdt_codes;

-- domain aliases
create or replace view rcm.denials as select * from rcm.denied_claims;
create or replace view rcm.claim_submissions as select * from rcm.accepted_claims;
create or replace view agents.registry as select * from agents.agents;
create or replace view agents.tasks as select * from agents.rcm_tasks;
create or replace view agents.task_events as select * from agents.rcm_task_events;
create or replace view audit.audit_events as select * from audit.audit_logs;

-- agent-facing rule projections
create or replace view public.v_rules_for_coding_agent as
select id, payer_name, payer_plan_scope, rule_type, code, transforms_to_code, related_codes,
       rule_text, conditions, contract_override_note, source_id, source_page, evidence_text, created_at
from rcm.payer_rules
where rule_type = any (array['processed_as', 'coverage_rule', 'frequency_limit', 'telehealth_policy']);

create or replace view public.v_rules_for_preauth_agent as
select id, payer_name, payer_plan_scope, rule_type, code, transforms_to_code, related_codes,
       rule_text, conditions, contract_override_note, source_id, source_page, evidence_text, created_at
from rcm.payer_rules
where rule_type = any (array['prior_auth', 'documentation_required']);

create or replace view public.v_rules_for_estimation_agent as
select id, payer_name, payer_plan_scope, rule_type, code, transforms_to_code, related_codes,
       rule_text, conditions, contract_override_note, source_id, source_page, evidence_text, created_at
from rcm.payer_rules
where rule_type = any (array['not_billable_to_patient', 'alternative_benefit', 'processed_as']);

create or replace view public.v_rules_for_scrubber_agent as
select id, payer_name, payer_plan_scope, rule_type, code, transforms_to_code, related_codes,
       rule_text, conditions, contract_override_note, source_id, source_page, evidence_text, created_at
from rcm.payer_rules
where rule_type = any (array['billing_exclusion', 'deny', 'bundling_rule', 'not_billable_to_patient']);

create or replace view public.v_rules_for_appeals_agent as
select id, payer_name, payer_plan_scope, rule_type, code, transforms_to_code, related_codes,
       rule_text, conditions, contract_override_note, source_id, source_page, evidence_text, created_at
from rcm.payer_rules;

create or replace view public.v_cdt_code_exclusions as
select r.payer_name, r.code as code_a, x.code as code_b
from rcm.cdt_payer_rules_structured r
cross join lateral unnest(coalesce(r.not_billable_with_codes, '{}'::text[])) x(code);

-- dashboard read model (current production version)
create or replace view public.eligibility_dashboard_rows as
with estimate_summary as (
  select eligibility_check_id,
         sum(coalesce(patient_responsibility, 0)) as estimated_patient_responsibility
  from rcm.procedure_estimates
  group by eligibility_check_id
)
select er.id as request_id, er.patient_id, er.first_name, er.last_name,
  trim(both from (er.first_name || ' ') || er.last_name) as patient_name,
  er.dob, er.subscriber_id, er.primary_payer_id,
  coalesce(nullif(ec.payer_id, ''), er.primary_payer_id) as payer_label,
  er.secondary_payer_id, er.plan_id, er.cdt_codes, er.trigger_event,
  er.status as request_status, er.primary_check_id, er.secondary_check_id,
  er.error_message, er.error_code, er.suggested_action, er.failure_category, er.status_reason,
  er.priority,
  case er.priority when 'high' then 1 when 'medium' then 2 else 3 end as priority_rank,
  er.appointment_date, er.appointment_time, er.provider_name, er.estimated_claim_value,
  er.coverage_status as request_coverage_status,
  er.attempt_count, er.max_attempts, er.started_at, er.last_attempt_at,
  er.locked_at, er.locked_by, er.next_retry_at, er.parent_request_id, er.idempotency_key,
  er.agent_http_status, er.agent_duration_ms, er.edge_duration_ms,
  er.created_at, er.updated_at, er.completed_at,
  ec.id as check_id, ec.checked_at, ec.coverage_order, ec.is_active, ec.inactive_reason,
  ec.is_covered, ec.in_network, ec.coverage_percent, ec.copay, ec.coinsurance,
  ec.deductible_total, ec.deductible_met, ec.deductible_remaining,
  ec.annual_max_total, ec.annual_max_used, ec.annual_max_remaining,
  coalesce(es.estimated_patient_responsibility, 0) as estimated_patient_responsibility,
  coalesce(er.coverage_status, case
      when ec.is_active is true then 'active'
      when ec.is_active is false then 'inactive'
      else 'unknown' end) as coverage_status,
  ec.response_complete,
  coalesce(array_length(ec.missing_fields, 1), 0) as missing_fields_count,
  ec.missing_fields, ec.routing_status,
  coalesce(array_length(ec.integrity_warnings, 1), 0) as integrity_warnings_count,
  ec.integrity_warnings, ec.raw_response,
  case
    when er.status = 'queued' then 'Queued'
    when er.status = 'processing' then 'Processing'
    when er.status = 'retrying' then 'Retrying'
    when er.status = 'failed' then 'Failed'
    when er.status = 'needs_attention' then 'Needs Attention'
    when ec.is_active is false then 'Inactive'
    when ec.id is null then 'Needs Attention'
    when ec.response_complete is false then 'Needs Attention'
    when coalesce(array_length(ec.missing_fields, 1), 0) > 0 then 'Needs Attention'
    when coalesce(array_length(ec.integrity_warnings, 1), 0) > 0 then 'Needs Attention'
    when ec.routing_status is not null and (ec.routing_status <> all (array['CLEARED', 'APPROVED'])) then 'Needs Attention'
    else 'Verified'
  end as status_label,
  case
    when er.suggested_action is not null then er.suggested_action
    when er.status = any (array['queued', 'processing', 'retrying']) then er.status_reason
    when er.status = 'failed' then coalesce(er.error_message, er.status_reason, 'Processing failed')
    when ec.is_active is false then coalesce(ec.inactive_reason, 'Coverage inactive')
    when ec.response_complete is false then 'Payer response is incomplete'
    when coalesce(array_length(ec.missing_fields, 1), 0) > 0 then 'Missing normalized eligibility fields'
    when coalesce(array_length(ec.integrity_warnings, 1), 0) > 0 then 'Integrity warnings require review'
    when ec.routing_status is not null and (ec.routing_status <> all (array['CLEARED', 'APPROVED'])) then ec.routing_status
    else 'Eligibility verified'
  end as status_detail
from rcm.eligibility_requests er
left join rcm.eligibility_checks ec on ec.id = er.primary_check_id
left join estimate_summary es on es.eligibility_check_id = ec.id;

-- ===========================================================================
-- Vector retrieval RPC (defined after views: body references public bridge views)
-- ===========================================================================
create or replace function public.match_cdt_codes(
  query_embedding vector,
  match_threshold double precision default 0.3,
  match_count integer default 5,
  payer_filter text default 'Delta Dental'::text
)
returns table(
  code text, description text, category text, subcategory text,
  requires_tooth boolean, requires_surfaces boolean, requires_radiograph boolean,
  similarity double precision, deny_rules jsonb, coverage_rules jsonb,
  bundling_rules jsonb, frequency_limits jsonb, documentation_required jsonb,
  billing_exclusions jsonb, processed_as_rules jsonb, not_billable_to_patient jsonb
)
language sql stable as $$
  select
    c.code, c.description, c.category, c.subcategory,
    c.requires_tooth, c.requires_surfaces, c.requires_radiograph,
    1 - (c.embedding <=> query_embedding) as similarity,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='deny') as deny_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text,'contract_note',r.contract_override_note)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='coverage_rule') as coverage_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'transforms_to',r.transforms_to_code,'related_codes',r.related_codes,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='bundling_rule') as bundling_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='frequency_limit') as frequency_limits,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='documentation_required') as documentation_required,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'related_codes',r.related_codes,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='billing_ exclusion') as billing_exclusions,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'transforms_to',r.transforms_to_code,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='processed_as') as processed_as_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='not_billable_to_patient') as not_billable_to_patient
  from cdt_codes c
  where c.embedding is not null
    and 1 - (c.embedding <=> query_embedding) > match_threshold
  order by similarity desc
  limit match_count;
$$;

-- ===========================================================================
-- Row Level Security + policies (matches production; permissive using(true))
-- ===========================================================================
alter table agents.claim_intake_snapshot enable row level security;
drop policy if exists claim_intake_snapshot_select_auth on agents.claim_intake_snapshot;
create policy claim_intake_snapshot_select_auth on agents.claim_intake_snapshot
  for select to authenticated, service_role using (true);
drop policy if exists claim_intake_snapshot_insert_auth on agents.claim_intake_snapshot;
create policy claim_intake_snapshot_insert_auth on agents.claim_intake_snapshot
  for insert to authenticated, service_role with check (true);
drop policy if exists claim_intake_snapshot_update_auth on agents.claim_intake_snapshot;
create policy claim_intake_snapshot_update_auth on agents.claim_intake_snapshot
  for update to authenticated, service_role using (true) with check (true);

alter table agents.rcm_tasks enable row level security;
drop policy if exists rcm_tasks_all_anon on agents.rcm_tasks;
create policy rcm_tasks_all_anon on agents.rcm_tasks for all to anon using (true) with check (true);
drop policy if exists rcm_tasks_all_authenticated on agents.rcm_tasks;
create policy rcm_tasks_all_authenticated on agents.rcm_tasks for all to authenticated using (true) with check (true);

alter table agents.rcm_task_events enable row level security;
drop policy if exists rcm_task_events_all_anon on agents.rcm_task_events;
create policy rcm_task_events_all_anon on agents.rcm_task_events for all to anon using (true) with check (true);
drop policy if exists rcm_task_events_all_authenticated on agents.rcm_task_events;
create policy rcm_task_events_all_authenticated on agents.rcm_task_events for all to authenticated using (true) with check (true);

alter table rcm.accepted_claims enable row level security;
drop policy if exists accepted_claims_all_anon on rcm.accepted_claims;
create policy accepted_claims_all_anon on rcm.accepted_claims for all to anon using (true) with check (true);
drop policy if exists accepted_claims_all_authenticated on rcm.accepted_claims;
create policy accepted_claims_all_authenticated on rcm.accepted_claims for all to authenticated using (true) with check (true);

alter table rcm.eligibility_agent_settings enable row level security;
drop policy if exists eligibility_agent_settings_all_anon on rcm.eligibility_agent_settings;
create policy eligibility_agent_settings_all_anon on rcm.eligibility_agent_settings for all to anon using (true) with check (true);
drop policy if exists eligibility_agent_settings_all_authenticated on rcm.eligibility_agent_settings;
create policy eligibility_agent_settings_all_authenticated on rcm.eligibility_agent_settings for all to authenticated using (true) with check (true);

alter table rcm.eligibility_request_events enable row level security;
drop policy if exists eligibility_request_events_all_anon on rcm.eligibility_request_events;
create policy eligibility_request_events_all_anon on rcm.eligibility_request_events for all to anon using (true) with check (true);
drop policy if exists eligibility_request_events_all_authenticated on rcm.eligibility_request_events;
create policy eligibility_request_events_all_authenticated on rcm.eligibility_request_events for all to authenticated using (true) with check (true);

alter table rcm.eligibility_requests enable row level security;
drop policy if exists eligibility_requests_all_anon on rcm.eligibility_requests;
create policy eligibility_requests_all_anon on rcm.eligibility_requests for all to anon using (true) with check (true);
drop policy if exists eligibility_requests_all_authenticated on rcm.eligibility_requests;
create policy eligibility_requests_all_authenticated on rcm.eligibility_requests for all to authenticated using (true) with check (true);

alter table rcm.practices enable row level security;
drop policy if exists practices_select on rcm.practices;
create policy practices_select on rcm.practices for select to anon, authenticated using (true);

alter table rcm.provider_payer_network enable row level security;
drop policy if exists provider_payer_network_select on rcm.provider_payer_network;
create policy provider_payer_network_select on rcm.provider_payer_network for select to anon, authenticated using (true);

-- ===========================================================================
-- Grants (mirror production privilege matrix)
-- ===========================================================================
-- Full read/write reference + workflow tables.
grant select, insert, update, delete on
  patient.patients, patient.providers, patient.encounters,
  agents.agents, agents.agent_decisions, agents.rcm_tasks, agents.rcm_task_events, agents.claim_intake_snapshot,
  feedback.decision_feedback,
  audit.audit_logs,
  logs.coding_log, logs.eligibility_audit_log,
  analytics.cdt_code_master, analytics.cdt_codes, analytics.codes, analytics.coding_rules,
  analytics.hio_rules, analytics.icd10_codes, analytics.icd10_dental_gem_axis, analytics.rule_sources,
  rcm.accepted_claims, rcm.claims, rcm.denied_claims, rcm.eligibility_checks, rcm.procedure_estimates,
  rcm.payer_network, rcm.payer_fee_schedules, rcm.payer_prior_auth_rules,
  rcm.cdt_payer_rules, rcm.cdt_payer_rules_structured, rcm.payer_rules
to anon, authenticated, service_role;

-- agent_runs: server-side only (PHI-adjacent run log).
grant select, insert, update, delete on rcm.agent_runs to service_role;

-- Eligibility queue: browsers may submit + read; status updates are server-only.
grant select, insert on rcm.eligibility_requests to anon, authenticated;
grant select, insert, update, delete on rcm.eligibility_requests to service_role;
grant select, insert on rcm.eligibility_request_events to anon, authenticated;
grant select, insert, delete on rcm.eligibility_request_events to service_role;
grant select, update on rcm.eligibility_agent_settings to anon, authenticated;
grant select, insert, update, delete on rcm.eligibility_agent_settings to service_role;

-- Directory reference: read for clients; writes server-only.
grant select on rcm.practices, rcm.provider_payer_network to anon, authenticated;
grant insert, update, delete on rcm.practices, rcm.provider_payer_network to service_role;

-- Domain alias views.
grant select, insert, update, delete on
  rcm.denials, rcm.claim_submissions,
  agents.registry, agents.tasks, agents.task_events,
  audit.audit_events
to anon, authenticated, service_role;

-- All public bridge views inherit broad grants; the eligibility_requests view
-- is read/insert only for browsers (status updates go through service_role).
grant select, insert, update, delete on all tables in schema public
  to anon, authenticated, service_role;
revoke update on public.eligibility_requests from anon, authenticated;

-- ===========================================================================
-- Realtime publication membership
-- ===========================================================================
do $$
declare t text;
begin
  foreach t in array array[
    'agents.rcm_tasks', 'logs.coding_log', 'rcm.denied_claims',
    'rcm.eligibility_agent_settings', 'rcm.eligibility_checks',
    'rcm.eligibility_request_events', 'rcm.eligibility_requests', 'rcm.procedure_estimates'
  ] loop
    begin
      execute format('alter publication supabase_realtime add table %s', t);
    exception when duplicate_object then null;
             when others then null;
    end;
  end loop;
end
$$;

commit;
