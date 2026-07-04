-- Master list for validate_icd_tool (expand or load from CMS ICD-10 files).
create table if not exists public.icd10_codes (
  code text primary key,
  description text,
  updated_at timestamptz not null default now()
);

comment on table public.icd10_codes is 'ICD-10-CM codes for server-side validation of agent output.';

alter table public.icd10_codes enable row level security;

-- Seed examples (safe to re-run with ON CONFLICT)
insert into public.icd10_codes (code, description) values
  ('K02.9', 'Dental caries, unspecified'),
  ('K02.51', 'Dental caries on pit and fissure surface limited to enamel'),
  ('K02.52', 'Dental caries on pit and fissure surface penetrating dentin'),
  ('M26.59', 'Other dentofacial anomalies'),
  ('R68.89', 'Other general symptoms and signs')
on conflict (code) do nothing;
