-- Retire Supabase Edge Function dispatch for eligibility requests (Wave 7 fix).
--
-- 048 dropped triggers on public.eligibility_requests (a view/bridge), but the
-- live dispatch triggers are on rcm.eligibility_requests. Until this migration
-- runs, INSERT still net.http_post's to process-eligibility-request, which
-- writes events without practice_id and fails every check.
--
-- Prerequisite: PIPELINE_WORKER_ENABLED=true on FastAPI; create_eligibility_request
-- enqueues platform.pipeline_runs; eligibility retry_worker re-queues retrying rows.

begin;

drop trigger if exists trg_process_eligibility_request on rcm.eligibility_requests;
drop trigger if exists trg_retry_eligibility_request on rcm.eligibility_requests;

-- Idempotent with 047/048 (bridge/public names).
drop trigger if exists trg_process_eligibility_request on public.eligibility_requests;
drop trigger if exists trg_retry_eligibility_request on public.eligibility_requests;

comment on function rcm.invoke_eligibility_request_processor() is
  'DEPRECATED (Wave 7): eligibility dispatch moved to Neon platform.pipeline_runs worker. Triggers dropped in 054.';

commit;
