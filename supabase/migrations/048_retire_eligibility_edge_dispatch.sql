-- Wave 7: retire Supabase Edge Function dispatch for eligibility requests.
-- Apply on the **non-PHI Supabase project** after Neon pipeline worker is live
-- and dashboard BFF creates requests on Neon (rcm.eligibility_requests).
--
-- Prerequisite: PIPELINE_WORKER_ENABLED=true and NEON_DATABASE_URL set on FastAPI.
-- Dashboard create_eligibility_request already enqueues platform.pipeline_runs.

drop trigger if exists trg_process_eligibility_request on public.eligibility_requests;
drop trigger if exists trg_retry_eligibility_request on public.eligibility_requests;

-- Optional: keep the function for rollback; uncomment to remove entirely.
-- drop function if exists rcm.invoke_eligibility_request_processor();

comment on function rcm.invoke_eligibility_request_processor() is
  'DEPRECATED (Wave 7): eligibility dispatch moved to Neon platform.pipeline_runs worker.';
