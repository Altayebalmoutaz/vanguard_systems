-- Final V1 hardening:
-- - Pass an Edge-only Supabase key from Vault for status/output updates.
-- - Keep browser access insert/select focused.
-- - Keep ngrok configurable through Vault until FastAPI is deployed.
--
-- Required Vault secrets:
--   eligibility_dashboard_edge_function_url
--   eligibility_dashboard_edge_function_anon_key
--   eligibility_dashboard_edge_function_service_role_key
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
  service_role_key text;
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

  select decrypted_secret into service_role_key
  from vault.decrypted_secrets
  where name = 'eligibility_dashboard_edge_function_service_role_key'
  limit 1;

  select decrypted_secret into agent_url
  from vault.decrypted_secrets
  where name = 'eligibility_agent_check_url'
  limit 1;

  if function_url is null or anon_key is null or service_role_key is null then
    update rcm.eligibility_requests
    set status = 'failed',
        error_message = 'Eligibility webhook is missing Edge Function Vault configuration.',
        failure_category = 'config_error',
        status_reason = 'Missing Edge Function Vault configuration'
    where id = new.id;
    return new;
  end if;

  if agent_url is null then
    update rcm.eligibility_requests
    set status = 'failed',
        error_message = 'Eligibility webhook is missing eligibility_agent_check_url Vault configuration.',
        failure_category = 'config_error',
        status_reason = 'Missing FastAPI URL Vault configuration'
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
      'agent_url', agent_url,
      'supabase_key', service_role_key
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

revoke update on rcm.eligibility_requests from anon, authenticated;
revoke update on public.eligibility_requests from anon, authenticated;

grant select, insert on rcm.eligibility_requests to anon, authenticated;
grant select, insert on public.eligibility_requests to anon, authenticated;
grant select, insert, update, delete on rcm.eligibility_requests to service_role;
grant select, insert, update on public.eligibility_requests to service_role;

commit;
