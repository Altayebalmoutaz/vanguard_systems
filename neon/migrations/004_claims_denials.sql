-- =============================================================================
-- Neon 004 — Claims, denials, intake snapshots, coding log
-- =============================================================================
-- PHI plane · rcm.claims* + agents.claim_intake_snapshot + logs.coding_log
-- =============================================================================

begin;

set local lock_timeout = '5s';

create schema if not exists logs;

-- ---------------------------------------------------------------------------
-- agents.claim_intake_snapshot
-- ---------------------------------------------------------------------------
create or replace function agents.set_claim_intake_snapshot_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create table if not exists agents.claim_intake_snapshot (
  id                 bigserial primary key,
  practice_id        text not null,
  encounter_id       text not null,
  schema_version     integer not null default 1,
  intake_status      text not null default 'draft'
                     check (intake_status in ('draft', 'ready', 'submitted', 'archived')),
  ready_for_claim    boolean not null default false,
  validation_errors  jsonb not null default '[]'::jsonb
                     check (jsonb_typeof(validation_errors) = 'array'),
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
  updated_at         timestamptz not null default now(),
  unique (practice_id, encounter_id)
);

create index if not exists claim_intake_snapshot_ready_idx
  on agents.claim_intake_snapshot (practice_id, ready_for_claim, intake_status, updated_at desc);

create index if not exists claim_intake_snapshot_patient_id_idx
  on agents.claim_intake_snapshot (patient_id);

create index if not exists claim_intake_snapshot_payer_id_idx
  on agents.claim_intake_snapshot (((payer ->> 'payer_id')));

create index if not exists claim_intake_snapshot_diagnosis_codes_gin_idx
  on agents.claim_intake_snapshot using gin (diagnosis_codes jsonb_path_ops);

create index if not exists claim_intake_snapshot_service_lines_gin_idx
  on agents.claim_intake_snapshot using gin (service_lines jsonb_path_ops);

drop trigger if exists trg_claim_intake_snapshot_updated_at on agents.claim_intake_snapshot;
create trigger trg_claim_intake_snapshot_updated_at
  before update on agents.claim_intake_snapshot
  for each row execute function agents.set_claim_intake_snapshot_updated_at();

-- ---------------------------------------------------------------------------
-- rcm.claims
-- ---------------------------------------------------------------------------
create table if not exists rcm.claims (
  id                uuid primary key default gen_random_uuid(),
  practice_id       text not null,
  patient_id        uuid references patient.patients(id),
  visit_date        date,
  provider          text,
  raw_note          text,
  status            text not null default 'draft',
  cdt_lines         jsonb,
  icd10_codes       jsonb,
  compliance_status text,
  compliance_flags  jsonb,
  compliance_note   text,
  coded_at          timestamptz,
  created_at        timestamptz not null default now()
);

create index if not exists idx_claims_practice_status
  on rcm.claims (practice_id, status, created_at desc);

-- ---------------------------------------------------------------------------
-- rcm.accepted_claims
-- ---------------------------------------------------------------------------
create table if not exists rcm.accepted_claims (
  id                   uuid primary key default gen_random_uuid(),
  practice_id          text not null,
  task_id              uuid not null unique references agents.rcm_tasks(id) on delete cascade,
  backend_record_id    text not null,
  backend_claim_id     text not null,
  patient_name         text not null,
  payer                text,
  final_codes          text[],
  final_summary        text,
  confidence           double precision,
  source_pipeline_json jsonb,
  accepted_at          timestamptz not null default now()
);

create index if not exists accepted_claims_accepted_at_idx
  on rcm.accepted_claims (practice_id, accepted_at desc);

-- ---------------------------------------------------------------------------
-- rcm.denied_claims
-- ---------------------------------------------------------------------------
create table if not exists rcm.denied_claims (
  id                  uuid primary key default gen_random_uuid(),
  practice_id         text not null,
  created_at          timestamptz not null default now(),
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
  status              text not null default 'pending'
              check (status in ('pending', 'in_appeal', 'resolved', 'abandoned'))
);

create index if not exists idx_denied_claims_practice_status
  on rcm.denied_claims (practice_id, status, created_at desc);

-- ---------------------------------------------------------------------------
-- logs.coding_log
-- ---------------------------------------------------------------------------
create table if not exists logs.coding_log (
  id                       uuid primary key default gen_random_uuid(),
  practice_id              text not null,
  created_at               timestamptz not null default now(),
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

create index if not exists idx_coding_log_practice_created
  on logs.coding_log (practice_id, created_at desc);

-- ---------------------------------------------------------------------------
-- BFF helper — replaces public.get_claim_intake_snapshot RPC
-- ---------------------------------------------------------------------------
create or replace function agents.get_claim_intake_snapshot(
  p_practice_id text,
  p_encounter_id text
)
returns jsonb
language sql
stable
as $$
  select to_jsonb(s)
  from agents.claim_intake_snapshot s
  where s.practice_id = p_practice_id
    and s.encounter_id = p_encounter_id
    and s.ready_for_claim = true
  limit 1;
$$;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table agents.claim_intake_snapshot enable row level security;
alter table agents.claim_intake_snapshot force row level security;
drop policy if exists tenant_isolation on agents.claim_intake_snapshot;
create policy tenant_isolation on agents.claim_intake_snapshot
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.claims enable row level security;
alter table rcm.claims force row level security;
drop policy if exists tenant_isolation on rcm.claims;
create policy tenant_isolation on rcm.claims
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.accepted_claims enable row level security;
alter table rcm.accepted_claims force row level security;
drop policy if exists tenant_isolation on rcm.accepted_claims;
create policy tenant_isolation on rcm.accepted_claims
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table rcm.denied_claims enable row level security;
alter table rcm.denied_claims force row level security;
drop policy if exists tenant_isolation on rcm.denied_claims;
create policy tenant_isolation on rcm.denied_claims
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

alter table logs.coding_log enable row level security;
alter table logs.coding_log force row level security;
drop policy if exists tenant_isolation on logs.coding_log;
create policy tenant_isolation on logs.coding_log
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

commit;
