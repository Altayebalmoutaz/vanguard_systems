-- =============================================================================
-- Neon 007 — Shadow pilot instrumentation (Wave 9)
-- =============================================================================
-- platform.pilot_shadow_events: agent predictions vs human outcomes for ROI.
-- =============================================================================

begin;

set local lock_timeout = '5s';

create table if not exists platform.pilot_shadow_events (
  id              uuid primary key default gen_random_uuid(),
  practice_id     text not null,
  event_type      text not null,
  source          text not null default 'system',
  patient_id      uuid,
  external_ref    text,
  agent_payload   jsonb not null default '{}'::jsonb,
  human_label     jsonb,
  match_status    text not null default 'pending',
  metadata        jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);

comment on table platform.pilot_shadow_events is
  'Shadow pilot: agent output vs human biller decisions for ROI and accuracy tracking.';

create index if not exists idx_pilot_shadow_events_practice_created
  on platform.pilot_shadow_events (practice_id, created_at desc);

create index if not exists idx_pilot_shadow_events_type
  on platform.pilot_shadow_events (event_type, created_at desc);

alter table platform.pilot_shadow_events enable row level security;
alter table platform.pilot_shadow_events force row level security;
drop policy if exists tenant_isolation on platform.pilot_shadow_events;
create policy tenant_isolation on platform.pilot_shadow_events
  for all
  using (platform.rls_bypass() or practice_id = platform.current_practice_id())
  with check (platform.rls_bypass() or practice_id = platform.current_practice_id());

commit;
