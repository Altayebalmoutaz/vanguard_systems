-- Vanguard MD Eligibility Agent tables + dental payer network reference

create table if not exists public.payer_network (
  payer_id text primary key,
  trading_partner_service_id text not null unique,
  display_name text,
  coverage_type text not null check (coverage_type in ('dental', 'medical')),
  created_at timestamptz default now()
);

create index if not exists idx_payer_network_coverage on public.payer_network (coverage_type);

create table if not exists public.eligibility_checks (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null,
  payer_id text not null,
  checked_at timestamptz not null default now(),
  coverage_order text check (coverage_order in ('primary', 'secondary')),
  is_active boolean,
  inactive_reason text,
  is_covered boolean,
  in_network boolean,
  coverage_percent numeric,
  copay numeric,
  coinsurance numeric,
  deductible_total numeric,
  deductible_met numeric,
  deductible_remaining numeric,
  annual_max_total numeric,
  annual_max_used numeric,
  annual_max_remaining numeric,
  has_secondary boolean default false,
  secondary_payer_id text,
  raw_response jsonb,
  response_complete boolean,
  missing_fields text[],
  normalization_version text default '1.0',
  routing_status text,
  integrity_warnings text[],
  created_at timestamptz default now()
);

create index if not exists idx_eligibility_checks_patient_checked
  on public.eligibility_checks (patient_id, payer_id, checked_at desc);

create table if not exists public.procedure_estimates (
  id uuid primary key default gen_random_uuid(),
  eligibility_check_id uuid references public.eligibility_checks (id) on delete cascade,
  cdt_code text,
  procedure_covered boolean,
  waiting_period_end date,
  waiting_period_category text,
  non_covered_reason text,
  allowed_amount numeric,
  insurance_pays numeric,
  patient_responsibility numeric,
  created_at timestamptz default now()
);

create index if not exists idx_procedure_estimates_check
  on public.procedure_estimates (eligibility_check_id);

create table if not exists public.eligibility_audit_log (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid,
  event_type text,
  detail jsonb,
  created_at timestamptz default now()
);

create index if not exists idx_eligibility_audit_patient
  on public.eligibility_audit_log (patient_id, created_at desc);

create table if not exists public.payer_prior_auth_rules (
  payer_id text not null,
  cdt_code text not null,
  auth_required boolean not null default false,
  primary key (payer_id, cdt_code)
);

create table if not exists public.payer_fee_schedules (
  payer_id text not null,
  cdt_code text not null,
  contracted_fee numeric not null,
  effective_date date not null,
  primary key (payer_id, cdt_code, effective_date)
);

create index if not exists idx_payer_fee_schedules_lookup
  on public.payer_fee_schedules (payer_id, cdt_code, effective_date desc);
