-- Database-managed webhook for eligibility dashboard requests.
-- Required Vault secrets:
--   eligibility_dashboard_edge_function_url
--   eligibility_dashboard_edge_function_anon_key

begin;

create extension if not exists pg_net with schema extensions;

create or replace function rcm.invoke_eligibility_request_processor()
returns trigger
language plpgsql
security definer
set search_path = rcm, public, vault, net, pg_temp
as $$
declare
  function_url text;
  anon_key text;
begin
  if new.status <> 'queued' then
    return new;
  end if;

  select decrypted_secret into function_url
  from vault.decrypted_secrets
  where name = 'eligibility_dashboard_edge_function_url'
  limit 1;

  select decrypted_secret into anon_key
  from vault.decrypted_secrets
  where name = 'eligibility_dashboard_edge_function_anon_key'
  limit 1;

  if function_url is null or anon_key is null then
    update rcm.eligibility_requests
    set status = 'failed',
        error_message = 'Eligibility webhook is missing Vault configuration.'
    where id = new.id;
    return new;
  end if;

  perform net.http_post(
    url := function_url,
    body := jsonb_build_object(
      'type', 'INSERT',
      'table', tg_table_name,
      'schema', tg_table_schema,
      'record', to_jsonb(new),
      'old_record', null
    ),
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || anon_key,
      'apikey', anon_key
    ),
    timeout_milliseconds := 10000
  );

  return new;
end;
$$;

drop trigger if exists trg_process_eligibility_request on rcm.eligibility_requests;

create trigger trg_process_eligibility_request
after insert on rcm.eligibility_requests
for each row
when (new.status = 'queued')
execute function rcm.invoke_eligibility_request_processor();

commit;
