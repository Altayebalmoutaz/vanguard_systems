-- =============================================================================
-- 045 — Eligibility agent consolidation
-- =============================================================================
-- First per-agent consolidation (see docs/agent-consolidation-roadmap.md).
-- Closes three operational gaps on the eligibility flow without regressing the
-- live dashboard:
--
--   1. WEBHOOK SIGNING (deploy-blocking fix). The deployed Edge Function
--      `process-eligibility-request` REQUIRES an `X-Webhook-Signature` header,
--      but the baseline (033-era) trigger sends an UNSIGNED net.http_post — so
--      DB-driven calls are rejected with HTTP 401 and requests strand at
--      'queued'. This re-introduces the signed dispatcher (formerly migration
--      038) as the go-forward posture.
--
--   2. PHI SURFACE REDUCTION (non-breaking). The raw 270/271 payload
--      (`eligibility_checks.raw_response`) was reachable by the `anon` role.
--      The dashboard never queries `eligibility_checks` directly (it reads the
--      `eligibility_dashboard_rows` view, which runs with the view owner's
--      privileges), so we can revoke anon's direct table grant AND null the raw
--      payload out of the anon-facing view. The detail panel degrades
--      gracefully (it already guards `check?.raw_response`).
--
-- PREREQUISITE (item 1): set the Vault secret BEFORE applying on an environment
-- whose Edge Function enforces signatures, and match it to the function's
-- WEBHOOK_SECRET env var:
--   eligibility_dashboard_edge_function_signing_secret  (32+ random bytes)
-- If this secret is absent, the trigger fails the request with a clear
-- 'Missing webhook signing secret' config_error rather than calling unsigned.
--
-- NOT included here (intentionally deferred — requires dashboard auth / Phase 1):
--   * Enabling RLS-deny + revoking anon SELECT on the rest of the eligibility
--     tables and adding `created_by = auth.uid()` tenant scoping (the prepared
--     change lives in legacy/037_eligibility_rls_hardening.sql). Applying it now
--     would break the anon, login-less dashboard. Ship it WITH Supabase Auth.
--
-- Idempotent. Schema-compatible: the view keeps the exact same column list and
-- types, so `create or replace view` succeeds and dependents (e.g. the daily
-- KPI RPC) keep working.
-- =============================================================================

begin;

set local lock_timeout = '5s';
set local search_path = rcm, public, extensions;

-- ---------------------------------------------------------------------------
-- 1. Signed eligibility-request dispatcher (HMAC-SHA256).
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto with schema extensions;  -- hmac()/encode()

create or replace function rcm.invoke_eligibility_request_processor()
returns trigger
language plpgsql
security definer
set search_path = rcm, public, extensions, vault, net, pg_temp
as $$
declare
  function_url text;
  anon_key text;
  service_role_key text;
  agent_url text;
  signing_secret text;
  body jsonb;
  body_text text;
  signature_hex text;
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
  select decrypted_secret into signing_secret from vault.decrypted_secrets
   where name = 'eligibility_dashboard_edge_function_signing_secret' limit 1;

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

  if signing_secret is null then
    update rcm.eligibility_requests
       set status = 'failed',
           error_message = 'Eligibility webhook is missing eligibility_dashboard_edge_function_signing_secret Vault configuration.',
           failure_category = 'config_error',
           status_reason = 'Missing webhook signing secret'
     where id = new.id;
    return new;
  end if;

  body := jsonb_build_object(
    'type', 'INSERT',
    'table', tg_table_name,
    'schema', tg_table_schema,
    'record', to_jsonb(new),
    'old_record', null,
    'agent_url', agent_url,
    'supabase_key', service_role_key
  );

  -- Sign the EXACT body the function will verify.
  body_text := body::text;
  signature_hex := encode(
    extensions.hmac(body_text::bytea, signing_secret::bytea, 'sha256'),
    'hex'
  );

  perform net.http_post(
    url := function_url,
    body := body,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || anon_key,
      'apikey', anon_key,
      'X-Webhook-Signature', 'sha256=' || signature_hex
    ),
    timeout_milliseconds := 60000
  );

  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. PHI surface reduction — stop exposing the raw 270/271 to the anon role.
-- ---------------------------------------------------------------------------
-- The dashboard reads through the view (owner privileges), never the table.
revoke select on rcm.eligibility_checks from anon;
revoke select on public.eligibility_checks from anon;

-- Recreate the dashboard read model with raw_response blanked. Same column set
-- and types as the baseline view (raw_response stays jsonb), so create-or-replace
-- is accepted and the daily KPI RPC that reads this view is unaffected.
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
  ec.integrity_warnings,
  -- PHI reduction: raw payer 270/271 is no longer surfaced to anon dashboard
  -- clients. Fetch it server-side (service_role / BFF) when troubleshooting.
  null::jsonb as raw_response,
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

commit;
