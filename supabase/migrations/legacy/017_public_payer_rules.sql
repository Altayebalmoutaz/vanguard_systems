-- Optional public payer rules (local/dev). The FastAPI coding agent reads `public.payer_rules`.
-- Rows: payer-specific or global (payer_name '*' / 'any'); code NULL = all CDT on claim; optional conditions JSON.

create table if not exists public.payer_rules (
  id bigserial primary key,
  payer_name text not null,
  rule_type text not null default 'coverage_rule',
  code text,
  related_codes text[],
  rule_text text not null,
  conditions jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists payer_rules_code_idx on public.payer_rules (code) where code is not null;

comment on table public.payer_rules is 'Payer rules evaluated after LLM codes + master validation (coding agent v1).';

comment on column public.payer_rules.conditions is
  'Optional filters: age_min, age_max (int), require_all_codes (text[] — all must appear on claim).';

-- Dev-friendly read access (tighten RLS for production).
alter table public.payer_rules enable row level security;

drop policy if exists "payer_rules_select_anon_authenticated" on public.payer_rules;
create policy "payer_rules_select_anon_authenticated"
  on public.payer_rules
  for select
  to anon, authenticated
  using (true);

grant select on public.payer_rules to anon, authenticated, service_role;

-- Example rules (each row idempotent).
insert into public.payer_rules (payer_name, rule_type, code, rule_text, conditions)
select 'Delta Dental', 'documentation_required', 'D2940',
       'May require clinical narrative or per-plan documentation.', '{}'::jsonb
where not exists (
  select 1 from public.payer_rules pr
  where pr.payer_name = 'Delta Dental' and pr.rule_type = 'documentation_required' and pr.code = 'D2940'
);

insert into public.payer_rules (payer_name, rule_type, code, rule_text, conditions)
select 'Delta Dental', 'bundling_review', null,
       'Review periodic oral evaluation (D0120) vs comprehensive oral evaluation (D0150) on the same date of service when both appear on the claim.',
       '{"require_all_codes": ["D0120", "D0150"]}'::jsonb
where not exists (
  select 1 from public.payer_rules pr
  where pr.payer_name = 'Delta Dental' and pr.rule_type = 'bundling_review' and pr.code is null
);
