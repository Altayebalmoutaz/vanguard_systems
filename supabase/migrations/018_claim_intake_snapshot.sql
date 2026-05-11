-- One-table intake snapshot for claim assembly (prototype-friendly).
-- Goal: capture all front-desk claim context now, then split into normalized
-- patient/coverage/provider/encounter tables later without breaking claim-agent reads.

create table if not exists public.claim_intake_snapshot (
  id bigserial primary key,
  encounter_id text not null unique,
  schema_version int not null default 1,

  -- Lifecycle / provenance
  intake_status text not null default 'draft' check (intake_status in ('draft', 'ready', 'submitted', 'archived')),
  ready_for_claim boolean not null default false,
  validation_errors jsonb not null default '[]'::jsonb,
  source_system text not null default 'frontdesk_ui',
  created_by text,

  -- Optional stable references for future normalization
  patient_id text,
  provider_id text,
  insurance_id text,

  -- Claim-context blocks (JSONB by design for split-later flexibility)
  patient jsonb not null default '{}'::jsonb,
  subscriber jsonb not null default '{}'::jsonb,
  payer jsonb not null default '{}'::jsonb,
  billing_provider jsonb not null default '{}'::jsonb,
  rendering_provider jsonb not null default '{}'::jsonb,
  claim_header jsonb not null default '{}'::jsonb,
  diagnosis_codes jsonb not null default '[]'::jsonb,
  service_lines jsonb not null default '[]'::jsonb,
  financials jsonb not null default '{}'::jsonb,

  -- Optional links to agent outputs
  coding_run_id text,
  prior_auth_run_id text,
  coding_output jsonb not null default '{}'::jsonb,
  prior_auth_output jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Shape guards
  constraint claim_intake_validation_errors_array check (jsonb_typeof(validation_errors) = 'array'),
  constraint claim_intake_patient_object check (jsonb_typeof(patient) = 'object'),
  constraint claim_intake_subscriber_object check (jsonb_typeof(subscriber) = 'object'),
  constraint claim_intake_payer_object check (jsonb_typeof(payer) = 'object'),
  constraint claim_intake_billing_provider_object check (jsonb_typeof(billing_provider) = 'object'),
  constraint claim_intake_rendering_provider_object check (jsonb_typeof(rendering_provider) = 'object'),
  constraint claim_intake_claim_header_object check (jsonb_typeof(claim_header) = 'object'),
  constraint claim_intake_diagnosis_array check (jsonb_typeof(diagnosis_codes) = 'array'),
  constraint claim_intake_service_lines_array check (jsonb_typeof(service_lines) = 'array'),
  constraint claim_intake_financials_object check (jsonb_typeof(financials) = 'object')
);

create index if not exists claim_intake_snapshot_ready_idx
  on public.claim_intake_snapshot (ready_for_claim, intake_status, updated_at desc);

create index if not exists claim_intake_snapshot_patient_id_idx
  on public.claim_intake_snapshot (patient_id);

create index if not exists claim_intake_snapshot_provider_id_idx
  on public.claim_intake_snapshot (provider_id);

create index if not exists claim_intake_snapshot_payer_id_idx
  on public.claim_intake_snapshot ((payer ->> 'payer_id'));

create index if not exists claim_intake_snapshot_patient_name_idx
  on public.claim_intake_snapshot ((patient ->> 'name'));

create index if not exists claim_intake_snapshot_service_lines_gin_idx
  on public.claim_intake_snapshot
  using gin (service_lines jsonb_path_ops);

create index if not exists claim_intake_snapshot_diagnosis_codes_gin_idx
  on public.claim_intake_snapshot
  using gin (diagnosis_codes jsonb_path_ops);

comment on table public.claim_intake_snapshot is
  'Front-desk encounter snapshot for claim agent input; intentionally denormalized for MVP.';

comment on column public.claim_intake_snapshot.schema_version is
  'Payload schema version for forward-compatible migrations to normalized tables.';

comment on column public.claim_intake_snapshot.validation_errors is
  'Array of machine-readable validation issues. Keep empty array when claim is ready.';

comment on column public.claim_intake_snapshot.claim_header is
  'Expected keys: claim_frequency_code, place_of_service, patient_account_number, dos_from, dos_to, patient_sex.';

comment on column public.claim_intake_snapshot.financials is
  'Expected keys: total_charge_amount (string/number), patient_responsibility, payer_responsibility.';

-- Keep updated_at fresh on every row mutation.
create or replace function public.set_claim_intake_snapshot_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_claim_intake_snapshot_updated_at on public.claim_intake_snapshot;
create trigger trg_claim_intake_snapshot_updated_at
before update on public.claim_intake_snapshot
for each row
execute function public.set_claim_intake_snapshot_updated_at();

-- RPC: retrieve one encounter snapshot as claim-agent context.
create or replace function public.get_claim_intake_snapshot(p_encounter_id text)
returns jsonb
language sql
stable
as $$
  select to_jsonb(cis)
  from public.claim_intake_snapshot cis
  where cis.encounter_id = p_encounter_id
  limit 1
$$;

grant execute on function public.get_claim_intake_snapshot(text)
to service_role, authenticated;

-- RLS: deny by default; permit reads/writes for authenticated + service_role in dev.
alter table public.claim_intake_snapshot enable row level security;

drop policy if exists "claim_intake_snapshot_select_auth" on public.claim_intake_snapshot;
create policy "claim_intake_snapshot_select_auth"
  on public.claim_intake_snapshot
  for select
  to authenticated, service_role
  using (true);

drop policy if exists "claim_intake_snapshot_insert_auth" on public.claim_intake_snapshot;
create policy "claim_intake_snapshot_insert_auth"
  on public.claim_intake_snapshot
  for insert
  to authenticated, service_role
  with check (true);

drop policy if exists "claim_intake_snapshot_update_auth" on public.claim_intake_snapshot;
create policy "claim_intake_snapshot_update_auth"
  on public.claim_intake_snapshot
  for update
  to authenticated, service_role
  using (true)
  with check (true);
