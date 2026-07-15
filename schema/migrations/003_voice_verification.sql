-- =============================================================================
-- Neon 003 — Voice payer verification (parity with Supabase 046)
-- =============================================================================

begin;

set local lock_timeout = '5s';

alter table rcm.payer_network
  add column if not exists eligibility_phone text,
  add column if not exists voice_escalation_enabled boolean not null default false;

alter table rcm.eligibility_checks
  add column if not exists source_check_id uuid references rcm.eligibility_checks(id) on delete set null,
  add column if not exists verification_source text
    check (verification_source is null or verification_source in ('stedi', 'voice_verification'));

create table if not exists rcm.payer_verification_sessions (
  id                    uuid primary key default gen_random_uuid(),
  practice_id           text not null,
  patient_id            uuid not null,
  payer_id              text not null,
  eligibility_check_id  uuid not null references rcm.eligibility_checks(id) on delete cascade,
  request_id            uuid references rcm.eligibility_requests(id) on delete set null,
  status                text not null default 'queued'
    check (status in (
      'queued', 'calling', 'completed', 'failed', 'pending_review',
      'approved', 'rejected', 'cancelled'
    )),
  missing_fields_target text[] not null default '{}'::text[],
  cdt_codes             text[] not null default '{}'::text[],
  call_provider         text not null default 'twilio',
  call_sid              text,
  call_reference        text,
  call_duration_seconds integer,
  transcript_redacted   text,
  extracted_fields      jsonb,
  merged_check_id       uuid references rcm.eligibility_checks(id) on delete set null,
  approved_by           text,
  approved_at           timestamptz,
  failure_code          text,
  failure_message       text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists idx_payer_verification_sessions_status_created
  on rcm.payer_verification_sessions (practice_id, status, created_at);

alter table rcm.eligibility_agent_settings
  add column if not exists voice_verification_enabled boolean not null default false,
  add column if not exists voice_verification_auto_queue boolean not null default true;

commit;
