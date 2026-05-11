-- Eligibility pipeline readiness foundation:
-- queue lifecycle, request events, and a stable dashboard read model.

begin;

alter table rcm.eligibility_requests
  add column if not exists attempt_count integer not null default 0,
  add column if not exists max_attempts integer not null default 3,
  add column if not exists started_at timestamptz,
  add column if not exists last_attempt_at timestamptz,
  add column if not exists locked_at timestamptz,
  add column if not exists locked_by text,
  add column if not exists next_retry_at timestamptz,
  add column if not exists failure_category text,
  add column if not exists status_reason text,
  add column if not exists idempotency_key text,
  add column if not exists parent_request_id uuid references rcm.eligibility_requests (id) on delete set null,
  add column if not exists agent_http_status integer,
  add column if not exists agent_duration_ms integer,
  add column if not exists edge_duration_ms integer;

do $constraint$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'eligibility_requests_failure_category_check'
      and conrelid = 'rcm.eligibility_requests'::regclass
  ) then
    alter table rcm.eligibility_requests
      add constraint eligibility_requests_failure_category_check
      check (
        failure_category is null or failure_category in (
          'config_error',
          'agent_error',
          'payer_error',
          'timeout',
          'validation_error',
          'unknown'
        )
      );
  end if;
end
$constraint$;

create unique index if not exists idx_eligibility_requests_idempotency
  on rcm.eligibility_requests (idempotency_key)
  where idempotency_key is not null;

create index if not exists idx_eligibility_requests_retry
  on rcm.eligibility_requests (status, next_retry_at, attempt_count);

create index if not exists idx_eligibility_requests_parent
  on rcm.eligibility_requests (parent_request_id);

create table if not exists rcm.eligibility_request_events (
  id uuid primary key default gen_random_uuid(),
  request_id uuid not null references rcm.eligibility_requests (id) on delete cascade,
  event_type text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_eligibility_request_events_request_created
  on rcm.eligibility_request_events (request_id, created_at desc);

create or replace view public.eligibility_request_events as
select * from rcm.eligibility_request_events;

create or replace view public.eligibility_requests as
select * from rcm.eligibility_requests;

create or replace view public.eligibility_dashboard_rows as
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
  er.failure_category,
  er.status_reason,
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
    when er.status = 'failed' then 'Failed'
    when ec.is_active is false then 'Inactive'
    when ec.id is null then 'Needs Attention'
    when ec.response_complete is false then 'Needs Attention'
    when coalesce(array_length(ec.missing_fields, 1), 0) > 0 then 'Needs Attention'
    when coalesce(array_length(ec.integrity_warnings, 1), 0) > 0 then 'Needs Attention'
    when ec.routing_status is not null and ec.routing_status not in ('CLEARED', 'APPROVED') then 'Needs Attention'
    else 'Verified'
  end as status_label,
  case
    when er.status in ('queued', 'processing') then er.status_reason
    when er.status = 'failed' then coalesce(er.error_message, er.status_reason, 'Processing failed')
    when ec.is_active is false then coalesce(ec.inactive_reason, 'Coverage inactive')
    when ec.response_complete is false then 'Payer response is incomplete'
    when coalesce(array_length(ec.missing_fields, 1), 0) > 0 then 'Missing normalized eligibility fields'
    when coalesce(array_length(ec.integrity_warnings, 1), 0) > 0 then 'Integrity warnings require review'
    when ec.routing_status is not null and ec.routing_status not in ('CLEARED', 'APPROVED') then ec.routing_status
    else 'Eligibility verified'
  end as status_detail
from rcm.eligibility_requests er
left join rcm.eligibility_checks ec on ec.id = er.primary_check_id;

grant select, insert, update on rcm.eligibility_requests to anon, authenticated;
grant select, insert, update, delete on rcm.eligibility_requests to service_role;
grant select, insert, update on public.eligibility_requests to anon, authenticated, service_role;
grant select, insert on rcm.eligibility_request_events to anon, authenticated;
grant select, insert, delete on rcm.eligibility_request_events to service_role;
grant select, insert on public.eligibility_request_events to anon, authenticated, service_role;
grant select on public.eligibility_dashboard_rows to anon, authenticated, service_role;

alter table rcm.eligibility_request_events enable row level security;

drop policy if exists "eligibility_request_events_all_anon" on rcm.eligibility_request_events;
create policy "eligibility_request_events_all_anon"
  on rcm.eligibility_request_events for all to anon
  using (true)
  with check (true);

drop policy if exists "eligibility_request_events_all_authenticated" on rcm.eligibility_request_events;
create policy "eligibility_request_events_all_authenticated"
  on rcm.eligibility_request_events for all to authenticated
  using (true)
  with check (true);

do $realtime$
begin
  alter publication supabase_realtime add table rcm.eligibility_request_events;
exception
  when others then
    null;
end
$realtime$;

commit;
