-- Let failed requests be retried by updating status back to queued.

begin;

drop trigger if exists trg_process_eligibility_request on rcm.eligibility_requests;
drop trigger if exists trg_retry_eligibility_request on rcm.eligibility_requests;

create trigger trg_process_eligibility_request
after insert on rcm.eligibility_requests
for each row
when (new.status = 'queued')
execute function rcm.invoke_eligibility_request_processor();

create trigger trg_retry_eligibility_request
after update of status on rcm.eligibility_requests
for each row
when (new.status = 'queued' and old.status is distinct from new.status)
execute function rcm.invoke_eligibility_request_processor();

commit;
