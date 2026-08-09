-- Solo coding agent system of record for scribe-driven suggest calls.
-- No encounter FK: scribe payloads arrive before/without a local encounters row.
-- Idempotency: unique (practice_id, request_id).

begin;

create table if not exists agents.coding_runs (
  id                  uuid primary key default gen_random_uuid(),
  practice_id         text not null,
  request_id          uuid not null,
  patient_id          text not null,
  provider_id         text not null,
  encounter_datetime  timestamptz not null,
  payer_id            text,
  request_payload     jsonb not null default '{}'::jsonb,
  response_payload    jsonb not null default '{}'::jsonb,
  status              text not null default 'pending_review'
                      check (status in ('pending_review', 'needs_info', 'approved', 'rejected')),
  overall_confidence  double precision not null default 0,
  created_at          timestamptz not null default now()
);

create unique index if not exists coding_runs_practice_request_uidx
  on agents.coding_runs (practice_id, request_id);

create index if not exists coding_runs_practice_created_idx
  on agents.coding_runs (practice_id, created_at desc);

create index if not exists coding_runs_status_created_idx
  on agents.coding_runs (status, created_at desc);

-- Public bridge view (PostgREST / dual-path writer).
create or replace view public.coding_runs as
  select * from agents.coding_runs;

grant select, insert, update, delete on agents.coding_runs to service_role;
grant select, insert, update, delete on public.coding_runs to service_role;

commit;
