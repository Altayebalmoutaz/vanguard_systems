-- Sign every eligibility-request webhook with HMAC-SHA256.
--
-- The edge function `process-eligibility-request` was hardened in this same
-- iteration to require an `X-Webhook-Signature: sha256=<hex>` header on every
-- request. This migration extends the Postgres trigger so the database itself
-- computes and sends the signature. Without this migration the function will
-- reject all DB-driven calls with HTTP 401.
--
-- Required Vault secret (in addition to the ones added in migration 033):
--   eligibility_dashboard_edge_function_signing_secret
--     32+ random bytes, hex- or base64-encoded as a plain UTF-8 string.
--     Rotate alongside the function's WEBHOOK_SECRET environment variable.

begin;

-- pgcrypto provides hmac() / encode() and is already enabled on Supabase, but
-- we ensure it's present for self-hosted environments.
create extension if not exists pgcrypto with schema extensions;

create or replace function rcm.invoke_eligibility_request_processor()
returns trigger
language plpgsql
security definer
set search_path = rcm, public, extensions, vault, net, pg_temp
as $$
declare
  function_url text;
  anon_key text;
  service_role_key text;
  agent_url text;
  signing_secret text;
  body jsonb;
  body_text text;
  signature_hex text;
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

  select decrypted_secret into signing_secret
  from vault.decrypted_secrets
  where name = 'eligibility_dashboard_edge_function_signing_secret'
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

  if signing_secret is null then
    update rcm.eligibility_requests
    set status = 'failed',
        error_message = 'Eligibility webhook is missing eligibility_dashboard_edge_function_signing_secret Vault configuration.',
        failure_category = 'config_error',
        status_reason = 'Missing webhook signing secret'
    where id = new.id;
    return new;
  end if;

  body := jsonb_build_object(
    'type', 'INSERT',
    'table', tg_table_name,
    'schema', tg_table_schema,
    'record', to_jsonb(new),
    'old_record', null,
    'agent_url', agent_url,
    'supabase_key', service_role_key
  );

  -- The signature is computed over the *exact* body the function will see.
  body_text := body::text;
  signature_hex := encode(
    extensions.hmac(body_text::bytea, signing_secret::bytea, 'sha256'),
    'hex'
  );

  perform net.http_post(
    url := function_url,
    body := body,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || anon_key,
      'apikey', anon_key,
      'X-Webhook-Signature', 'sha256=' || signature_hex
    ),
    timeout_milliseconds := 60000
  );

  return new;
end;
$$;

commit;
