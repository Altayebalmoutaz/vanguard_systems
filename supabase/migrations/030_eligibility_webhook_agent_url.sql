-- Let the eligibility request webhook pass the target FastAPI URL from Vault.
-- Required Vault secret:
--   eligibility_agent_check_url

begin;

create or replace function rcm.invoke_eligibility_request_processor()
returns trigger
language plpgsql
security definer
set search_path = rcm, public, vault, net, pg_temp
as $$
declare
  function_url text;
  anon_key text;
  agent_url text;
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

  select decrypted_secret into agent_url
  from vault.decrypted_secrets
  where name = 'eligibility_agent_check_url'
  limit 1;

  if function_url is null or anon_key is null then
    update rcm.eligibility_requests
    set status = 'failed',
        error_message = 'Eligibility webhook is missing Edge Function Vault configuration.'
    where id = new.id;
    return new;
  end if;

  if agent_url is null then
    update rcm.eligibility_requests
    set status = 'failed',
        error_message = 'Eligibility webhook is missing eligibility_agent_check_url Vault configuration.'
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
      'old_record', null,
      'agent_url', agent_url
    ),
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || anon_key,
      'apikey', anon_key
    ),
    timeout_milliseconds := 60000
  );

  return new;
end;
$$;

commit;
