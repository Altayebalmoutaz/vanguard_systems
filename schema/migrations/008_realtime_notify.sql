-- 008_realtime_notify.sql
-- Realtime dashboard events: pg_notify on eligibility/pipeline state changes.
--
-- A FastAPI background task LISTENs on the 'rcm_events' channel and fans events
-- out to connected SSE dashboard clients (see app/realtime/bus.py). Payloads are
-- intentionally small "something changed" signals — the dashboard refetches via
-- the BFF; no PHI beyond row ids travels through NOTIFY.

create or replace function platform.notify_rcm_event() returns trigger
language plpgsql as $$
declare
  rec jsonb := to_jsonb(new);
  payload text;
begin
  payload := jsonb_build_object(
    'source', tg_table_schema || '.' || tg_table_name,
    'op', tg_op,
    'practice_id', rec->>'practice_id',
    'id', rec->>'id',
    'request_id', coalesce(rec->>'request_id', rec->>'id'),
    'status', rec->>'status',
    'event_type', rec->>'event_type',
    'run_type', rec->>'run_type'
  )::text;
  -- NOTIFY payloads are capped at ~8000 bytes; ours are tiny, but guard anyway.
  if octet_length(payload) < 7900 then
    perform pg_notify('rcm_events', payload);
  end if;
  return new;
end
$$;

drop trigger if exists trg_notify_eligibility_requests on rcm.eligibility_requests;
create trigger trg_notify_eligibility_requests
  after insert or update on rcm.eligibility_requests
  for each row execute function platform.notify_rcm_event();

drop trigger if exists trg_notify_eligibility_request_events on rcm.eligibility_request_events;
create trigger trg_notify_eligibility_request_events
  after insert on rcm.eligibility_request_events
  for each row execute function platform.notify_rcm_event();

drop trigger if exists trg_notify_pipeline_runs on platform.pipeline_runs;
create trigger trg_notify_pipeline_runs
  after insert or update on platform.pipeline_runs
  for each row execute function platform.notify_rcm_event();
