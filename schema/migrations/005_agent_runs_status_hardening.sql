-- =============================================================================
-- Neon 005 — agent_runs status hardening (Prior-Auth lifecycle)
-- =============================================================================
-- State machine (terminal states are immutable):
--   pending_review -> approved | denied | expired | superseded
--
-- Status CHECK and idx_agent_runs_status are defined in 003; this migration
-- re-documents transitions, ensures indexes exist, and adds a DB helper for
-- atomic resolution when called from SQL/admin tooling.
-- =============================================================================

begin;

set local lock_timeout = '5s';

comment on column rcm.agent_runs.status is
  'Lifecycle: pending_review -> approved|denied|expired|superseded. '
  'Terminal states (approved, denied, expired, superseded) cannot change.';

create index if not exists idx_agent_runs_status
  on rcm.agent_runs (practice_id, status, created_at desc);

create index if not exists idx_agent_runs_agent_status
  on rcm.agent_runs (practice_id, agent, status, created_at desc);

create or replace function rcm.agent_run_transition(
  p_run_id uuid,
  p_practice_id text,
  p_new_status text
)
returns rcm.agent_runs
language plpgsql
as $$
declare
  v_row rcm.agent_runs;
  v_old text;
begin
  select *
  into v_row
  from rcm.agent_runs
  where id = p_run_id
    and practice_id = p_practice_id
  for update;

  if not found then
    raise exception 'agent_run_not_found'
      using errcode = 'P0002';
  end if;

  v_old := v_row.status;

  if v_old = p_new_status then
    return v_row;
  end if;

  if v_old <> 'pending_review' then
    raise exception 'invalid_agent_run_transition: % -> %', v_old, p_new_status
      using errcode = 'P0001';
  end if;

  if p_new_status not in ('approved', 'denied', 'expired', 'superseded') then
    raise exception 'invalid_agent_run_status: %', p_new_status
      using errcode = 'P0001';
  end if;

  update rcm.agent_runs
  set status = p_new_status
  where id = p_run_id
  returning * into v_row;

  return v_row;
end;
$$;

comment on function rcm.agent_run_transition(uuid, text, text) is
  'Apply pending_review -> approved|denied|expired|superseded under row lock. '
  'Raises agent_run_not_found or invalid_agent_run_transition on failure.';

commit;
