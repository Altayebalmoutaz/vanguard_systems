-- Ground-truth capture for the coding agent: what the dentist actually did with
-- each suggested line (approved / edited / rejected / added). This is the source
-- of truth for CDT top-1 accuracy and the live scorecard.
--
-- One row per (practice_id, coding_run_id, line_id); re-submitting a decision for
-- the same line upserts (last write wins) so a dentist can change their mind.

begin;

create table if not exists agents.coding_decisions (
  id             uuid primary key default gen_random_uuid(),
  practice_id    text not null,
  coding_run_id  uuid not null,
  request_id     uuid,
  line_id        text not null,
  action         text not null
                 check (action in ('approved', 'edited', 'rejected', 'added')),
  suggested_cdt  text,
  final_cdt      text,
  edit_reason    text,
  payer_id       text,
  decided_by     text,
  decided_at     timestamptz not null default now(),
  created_at     timestamptz not null default now()
);

create unique index if not exists coding_decisions_run_line_uidx
  on agents.coding_decisions (practice_id, coding_run_id, line_id);

create index if not exists coding_decisions_practice_created_idx
  on agents.coding_decisions (practice_id, created_at desc);

create index if not exists coding_decisions_run_idx
  on agents.coding_decisions (coding_run_id);

-- Public bridge view (PostgREST / dual-path writer).
create or replace view public.coding_decisions as
  select * from agents.coding_decisions;

grant select, insert, update, delete on agents.coding_decisions to service_role;
grant select, insert on agents.coding_decisions to authenticated;
grant select, insert, update, delete on public.coding_decisions to service_role;
grant select, insert on public.coding_decisions to authenticated;

commit;
