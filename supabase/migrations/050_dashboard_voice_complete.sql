-- 050: Dashboard voice-complete copy + join voice sessions after primary_check_id moves to merged check.

drop view if exists public.eligibility_dashboard_rows cascade;

create view public.eligibility_dashboard_rows as
with estimate_summary as (
  select eligibility_check_id,
         sum(coalesce(patient_responsibility, 0)) as estimated_patient_responsibility
  from rcm.procedure_estimates
  group by eligibility_check_id
),
voice_latest as (
  select distinct on (eligibility_check_id)
    eligibility_check_id,
    id as voice_session_id,
    status as voice_session_status,
    merged_check_id as voice_merged_check_id,
    extracted_fields as voice_extracted_fields,
    call_reference as voice_call_reference
  from rcm.payer_verification_sessions
  order by eligibility_check_id, created_at desc
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
  ec.integrity_warnings,
  null::jsonb as raw_response,
  vl.voice_session_id,
  vl.voice_session_status,
  vl.voice_merged_check_id,
  vl.voice_extracted_fields,
  vl.voice_call_reference,
  case
    when er.status = 'queued' then 'Queued'
    when er.status = 'processing' then 'Processing'
    when er.status = 'retrying' then 'Retrying'
    when er.status = 'failed' then 'Failed'
    when er.status = 'needs_attention' then 'Needs Attention'
    when vl.voice_session_status = 'pending_review' then 'Needs Attention'
    when vl.voice_session_status in ('queued', 'calling') then 'Processing'
    when ec.is_active is false then 'Inactive'
    when ec.id is null then 'Needs Attention'
    when ec.response_complete is false then 'Needs Attention'
    when coalesce(array_length(ec.missing_fields, 1), 0) > 0
         and vl.voice_session_status is distinct from 'approved' then 'Needs Attention'
    when coalesce(array_length(ec.integrity_warnings, 1), 0) > 0 then 'Needs Attention'
    when ec.routing_status is not null and (ec.routing_status <> all (array['CLEARED', 'APPROVED'])) then 'Needs Attention'
    else 'Verified'
  end as status_label,
  case
    when vl.voice_session_status = 'pending_review' then 'Voice verification pending staff review'
    when vl.voice_session_status in ('queued', 'calling') then 'Voice agent resolving missing benefits'
    when vl.voice_session_status = 'approved'
         and ec.response_complete is true
         and coalesce(array_length(ec.missing_fields, 1), 0) = 0
      then 'Eligibility complete (Stedi + voice verification)'
    when vl.voice_session_status = 'approved' then 'Voice verification approved'
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
left join estimate_summary es on es.eligibility_check_id = ec.id
left join voice_latest vl on vl.eligibility_check_id = ec.id
  or vl.voice_merged_check_id = ec.id;

grant select on public.eligibility_dashboard_rows to authenticated, service_role;

notify pgrst, 'reload schema';
