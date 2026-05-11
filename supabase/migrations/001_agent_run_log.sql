-- Run in Supabase SQL editor or via CLI after linking the project.
create extension if not exists "pgcrypto";

create table if not exists public.agent_run_log (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_id text not null,
  step text,
  payload jsonb,
  practice_id text
);

comment on table public.agent_run_log is 'Audit trail for agent runs (optional; backend may use service role).';

alter table public.agent_run_log enable row level security;

-- No policies by default: only service_role (server) or postgres can write.
-- Add SELECT/INSERT policies when you expose Supabase directly to the frontend.
