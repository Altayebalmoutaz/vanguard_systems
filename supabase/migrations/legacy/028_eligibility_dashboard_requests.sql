-- Eligibility dashboard request queue and realtime wiring.
-- The browser writes only to Supabase; an Edge Function/DB webhook processes queued rows.

begin;

create schema if not exists rcm;

grant usage on schema rcm to anon, authenticated, service_role;

create table if not exists rcm.eligibility_requests (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null default gen_random_uuid(),
  first_name text not null,
  last_name text not null,
  dob date not null,
  subscriber_id text not null,
  primary_payer_id text not null,
  secondary_payer_id text,
  plan_id text,
  cdt_codes text[] not null default '{}',
  trigger_event text not null default 'APPOINTMENT_BOOKED'
    check (trigger_event in ('NEW_PATIENT', 'APPOINTMENT_BOOKED', 'PRE_APPOINTMENT', 'BATCH_SWEEP')),
  status text not null default 'queued'
    check (status in ('queued', 'processing', 'completed', 'failed')),
  primary_check_id uuid references rcm.eligibility_checks (id) on delete set null,
  secondary_check_id uuid references rcm.eligibility_checks (id) on delete set null,
  input_json jsonb not null default '{}'::jsonb,
  output_json jsonb not null default '{}'::jsonb,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists idx_eligibility_requests_status_created
  on rcm.eligibility_requests (status, created_at desc);

create index if not exists idx_eligibility_requests_patient_created
  on rcm.eligibility_requests (patient_id, created_at desc);

create index if not exists idx_eligibility_requests_primary_check
  on rcm.eligibility_requests (primary_check_id);

create or replace function rcm.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_eligibility_requests_updated_at on rcm.eligibility_requests;
create trigger trg_eligibility_requests_updated_at
before update on rcm.eligibility_requests
for each row
execute function rcm.set_updated_at();

comment on table rcm.eligibility_requests is
  'Supabase-first eligibility dashboard queue. UI inserts queued rows; Edge Function processes and links normalized output rows.';

create or replace view public.eligibility_requests as
select * from rcm.eligibility_requests;

grant select, insert, update on rcm.eligibility_requests to anon, authenticated;
grant select, insert, update, delete on rcm.eligibility_requests to service_role;
grant select, insert, update on public.eligibility_requests to anon, authenticated, service_role;

grant select on rcm.eligibility_checks to anon, authenticated;
grant select on rcm.procedure_estimates to anon, authenticated;
grant select on public.eligibility_checks to anon, authenticated, service_role;
grant select on public.procedure_estimates to anon, authenticated, service_role;

alter table rcm.eligibility_requests enable row level security;

drop policy if exists "eligibility_requests_all_anon" on rcm.eligibility_requests;
create policy "eligibility_requests_all_anon"
  on rcm.eligibility_requests for all to anon
  using (true)
  with check (true);

drop policy if exists "eligibility_requests_all_authenticated" on rcm.eligibility_requests;
create policy "eligibility_requests_all_authenticated"
  on rcm.eligibility_requests for all to authenticated
  using (true)
  with check (true);

do $realtime$
begin
  alter publication supabase_realtime add table rcm.eligibility_requests;
exception
  when others then
    null;
end
$realtime$;

do $realtime$
begin
  alter publication supabase_realtime add table rcm.eligibility_checks;
exception
  when others then
    null;
end
$realtime$;

do $realtime$
begin
  alter publication supabase_realtime add table rcm.procedure_estimates;
exception
  when others then
    null;
end
$realtime$;

commit;
