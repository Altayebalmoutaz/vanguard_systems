-- 049: Expose transcript_redacted on the public payer_verification_sessions view.
-- Migration 046 created the view without transcript_redacted, so service-role writes
-- that set the redacted transcript failed with PostgREST PGRST204 (column not in cache).
-- We add the column to the view but keep it out of the anon (browser) role for PHI safety.

create or replace view public.payer_verification_sessions as
 select id, practice_id, patient_id, payer_id, eligibility_check_id, request_id,
   status, missing_fields_target, cdt_codes, call_provider, call_sid, call_reference,
   call_duration_seconds, extracted_fields, merged_check_id, approved_by, approved_at,
   failure_code, failure_message, created_at, updated_at, transcript_redacted
 from rcm.payer_verification_sessions;

-- Keep the redacted transcript out of the anon role; service_role/authenticated retain
-- full table-level select (which already covers the new column).
revoke select on public.payer_verification_sessions from anon;
grant select (
  id, practice_id, patient_id, payer_id, eligibility_check_id, request_id,
  status, missing_fields_target, cdt_codes, call_provider, call_sid, call_reference,
  call_duration_seconds, extracted_fields, merged_check_id, approved_by, approved_at,
  failure_code, failure_message, created_at, updated_at
) on public.payer_verification_sessions to anon;

notify pgrst, 'reload schema';
