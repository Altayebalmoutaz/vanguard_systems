-- Product intelligence layer for the eligibility dashboard:
-- richer lifecycle statuses, actionable failures, automation settings,
-- priority sorting, and financial summary fields in the dashboard read model.

begin;

alter table rcm.eligibility_requests
  add column if not exists error_code text,
  add column if not exists suggested_action text,
  add column if not exists priority text not null default 'medium',
  add column if not exists appointment_date date,
  add column if not exists estimated_claim_value numeric,
  add column if not exists coverage_status text;

do $constraints$
declare
  status_constraint_name text;
begin
  select conname into status_constraint_name
  from pg_constraint
  where conrelid = 'rcm.eligibility_requests'::regclass
    and contype = 'c'
    and pg_get_constraintdef(oid) like '%status%'
    and pg_get_constraintdef(oid) like '%queued%'
  limit 1;

  if status_constraint_name is not null then
    execute format('alter table rcm.eligibility_requests drop constraint %I', status_constraint_name);
  end if;

  alter table rcm.eligibility_requests
    add constraint eligibility_requests_status_lifecycle_check
    check (status in ('queued', 'processing', 'retrying', 'completed', 'failed', 'needs_attention'));

  if not exists (
    select 1
    from pg_constraint
    where conname = 'eligibility_requests_priority_check'
      and conrelid = 'rcm.eligibility_requests'::regclass
  ) then
    alter table rcm.eligibility_requests
      add constraint eligibility_requests_priority_check
      check (priority in ('low', 'medium', 'high'));
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'eligibility_requests_coverage_status_check'
      and conrelid = 'rcm.eligibility_requests'::regclass
  ) then
    alter table rcm.eligibility_requests
      add constraint eligibility_requests_coverage_status_check
      check (coverage_status is null or coverage_status in ('active', 'inactive', 'unknown'));
  end if;
end
$constraints$;

create index if not exists idx_eligibility_requests_priority_schedule
  on rcm.eligibility_requests (priority, appointment_date, estimated_claim_value desc);

create table if not exists rcm.eligibility_agent_settings (
  id boolean primary key default true,
  auto_check_enabled boolean not null default true,
  auto_retry_enabled boolean not null default true,
  last_sync_at timestamptz,
  next_retry_at timestamptz,
  updated_at timestamptz not null default now(),
  check (id = true)
);

insert into rcm.eligibility_agent_settings (id)
values (true)
on conflict (id) do nothing;

drop trigger if exists trg_eligibility_agent_settings_updated_at on rcm.eligibility_agent_settings;
create trigger trg_eligibility_agent_settings_updated_at
before update on rcm.eligibility_agent_settings
for each row
execute function rcm.set_updated_at();

create or replace view public.eligibility_agent_settings as
select * from rcm.eligibility_agent_settings;

create or replace view public.eligibility_requests as
select * from rcm.eligibility_requests;

drop view if exists public.eligibility_dashboard_rows;

create or replace view public.eligibility_dashboard_rows as
with estimate_summary as (
  select
    eligibility_check_id,
    sum(coalesce(patient_responsibility, 0)) as estimated_patient_responsibility
  from rcm.procedure_estimates
  group by eligibility_check_id
)
select
  er.id as request_id,
  er.patient_id,
  er.first_name,
  er.last_name,
  trim(er.first_name || ' ' || er.last_name) as patient_name,
  er.dob,
  er.subscriber_id,
  er.primary_payer_id,
  coalesce(nullif(ec.payer_id, ''), er.primary_payer_id) as payer_label,
  er.secondary_payer_id,
  er.plan_id,
  er.cdt_codes,
  er.trigger_event,
  er.status as request_status,
  er.primary_check_id,
  er.secondary_check_id,
  er.error_message,
  er.error_code,
  er.suggested_action,
  er.failure_category,
  er.status_reason,
  er.priority,
  case er.priority when 'high' then 1 when 'medium' then 2 else 3 end as priority_rank,
  er.appointment_date,
  er.estimated_claim_value,
  er.coverage_status as request_coverage_status,
  er.attempt_count,
  er.max_attempts,
  er.started_at,
  er.last_attempt_at,
  er.locked_at,
  er.locked_by,
  er.next_retry_at,
  er.parent_request_id,
  er.idempotency_key,
  er.agent_http_status,
  er.agent_duration_ms,
  er.edge_duration_ms,
  er.created_at,
  er.updated_at,
  er.completed_at,
  ec.id as check_id,
  ec.checked_at,
  ec.coverage_order,
  ec.is_active,
  ec.inactive_reason,
  ec.is_covered,
  ec.in_network,
  ec.coverage_percent,
  ec.copay,
  ec.coinsurance,
  ec.deductible_total,
  ec.deductible_met,
  ec.deductible_remaining,
  ec.annual_max_total,
  ec.annual_max_used,
  ec.annual_max_remaining,
  coalesce(es.estimated_patient_responsibility, 0) as estimated_patient_responsibility,
  coalesce(er.coverage_status, case when ec.is_active is true then 'active' when ec.is_active is false then 'inactive' else 'unknown' end) as coverage_status,
  ec.response_complete,
  coalesce(array_length(ec.missing_fields, 1), 0) as missing_fields_count,
  ec.missing_fields,
  ec.routing_status,
  coalesce(array_length(ec.integrity_warnings, 1), 0) as integrity_warnings_count,
  ec.integrity_warnings,
  ec.raw_response,
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
    when ec.routing_status is not null and ec.routing_status not in ('CLEARED', 'APPROVED') then 'Needs Attention'
    else 'Verified'
  end as status_label,
  case
    when er.suggested_action is not null then er.suggested_action
    when er.status in ('queued', 'processing', 'retrying') then er.status_reason
    when er.status = 'failed' then coalesce(er.error_message, er.status_reason, 'Processing failed')
    when ec.is_active is false then coalesce(ec.inactive_reason, 'Coverage inactive')
    when ec.response_complete is false then 'Payer response is incomplete'
    when coalesce(array_length(ec.missing_fields, 1), 0) > 0 then 'Missing normalized eligibility fields'
    when coalesce(array_length(ec.integrity_warnings, 1), 0) > 0 then 'Integrity warnings require review'
    when ec.routing_status is not null and ec.routing_status not in ('CLEARED', 'APPROVED') then ec.routing_status
    else 'Eligibility verified'
  end as status_detail
from rcm.eligibility_requests er
left join rcm.eligibility_checks ec on ec.id = er.primary_check_id
left join estimate_summary es on es.eligibility_check_id = ec.id;

grant select, update on rcm.eligibility_agent_settings to anon, authenticated;
grant select, update on public.eligibility_agent_settings to anon, authenticated, service_role;
grant select, insert, update, delete on rcm.eligibility_agent_settings to service_role;
grant select on public.eligibility_dashboard_rows to anon, authenticated, service_role;

alter table rcm.eligibility_agent_settings enable row level security;

drop policy if exists "eligibility_agent_settings_all_anon" on rcm.eligibility_agent_settings;
create policy "eligibility_agent_settings_all_anon"
  on rcm.eligibility_agent_settings for all to anon
  using (true)
  with check (true);

drop policy if exists "eligibility_agent_settings_all_authenticated" on rcm.eligibility_agent_settings;
create policy "eligibility_agent_settings_all_authenticated"
  on rcm.eligibility_agent_settings for all to authenticated
  using (true)
  with check (true);

do $realtime$
begin
  alter publication supabase_realtime add table rcm.eligibility_agent_settings;
exception
  when others then
    null;
end
$realtime$;

commit;
