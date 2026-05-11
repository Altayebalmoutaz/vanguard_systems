-- RCM biller queue, audit trail, accepted claims (Next.js BFF + FastAPI dashboard).
-- Re-run safe for idempotent DDL where supported. Review RLS before production.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Tasks (one row per pipeline run queued for human review)
-- ---------------------------------------------------------------------------
create table if not exists public.rcm_tasks (
  id uuid primary key default gen_random_uuid(),
  backend_record_id text not null default '',
  backend_claim_id text not null default '',
  task_type text not null default 'Full RCM pipeline',
  patient_name text not null,
  patient_dob text,
  payer text,
  clinical_note text not null default '',
  demographics_block text,
  ai_codes text[] default '{}',
  ai_summary text,
  confidence double precision,
  status text not null default 'pending',
  biller_edited_codes text[],
  pipeline_json jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz
);

create index if not exists rcm_tasks_status_created_idx on public.rcm_tasks (status, created_at desc);

-- ---------------------------------------------------------------------------
-- Audit events (append-only)
-- ---------------------------------------------------------------------------
create table if not exists public.rcm_task_events (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references public.rcm_tasks (id) on delete cascade,
  event_type text not null,
  actor_label text not null default 'system',
  payload jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists rcm_task_events_task_created_idx
  on public.rcm_task_events (task_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Accepted / submitted snapshot (one row per task_id)
-- ---------------------------------------------------------------------------
create table if not exists public.accepted_claims (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references public.rcm_tasks (id) on delete cascade,
  backend_record_id text not null,
  backend_claim_id text not null,
  patient_name text not null,
  payer text,
  final_codes text[],
  final_summary text,
  confidence double precision,
  source_pipeline_json jsonb,
  accepted_at timestamptz not null default now(),
  unique (task_id)
);

create index if not exists accepted_claims_accepted_at_idx on public.accepted_claims (accepted_at desc);

-- ---------------------------------------------------------------------------
-- Realtime: biller UI live refresh (ignore error if already added)
-- ---------------------------------------------------------------------------
do $realtime$
begin
  alter publication supabase_realtime add table public.rcm_tasks;
exception
  when others then
    -- already a member of publication, or replication not available in this environment
    null;
end
$realtime$;

-- ---------------------------------------------------------------------------
-- RLS: open policies for anon + authenticated (no app auth yet — tighten later)
-- ---------------------------------------------------------------------------
alter table public.rcm_tasks enable row level security;
alter table public.rcm_task_events enable row level security;
alter table public.accepted_claims enable row level security;

drop policy if exists "rcm_tasks_all_anon" on public.rcm_tasks;
create policy "rcm_tasks_all_anon" on public.rcm_tasks for all to anon using (true) with check (true);

drop policy if exists "rcm_tasks_all_authenticated" on public.rcm_tasks;
create policy "rcm_tasks_all_authenticated" on public.rcm_tasks for all to authenticated using (true) with check (true);

drop policy if exists "rcm_task_events_all_anon" on public.rcm_task_events;
create policy "rcm_task_events_all_anon" on public.rcm_task_events for all to anon using (true) with check (true);

drop policy if exists "rcm_task_events_all_authenticated" on public.rcm_task_events;
create policy "rcm_task_events_all_authenticated" on public.rcm_task_events for all to authenticated using (true) with check (true);

drop policy if exists "accepted_claims_all_anon" on public.accepted_claims;
create policy "accepted_claims_all_anon" on public.accepted_claims for all to anon using (true) with check (true);

drop policy if exists "accepted_claims_all_authenticated" on public.accepted_claims;
create policy "accepted_claims_all_authenticated" on public.accepted_claims for all to authenticated using (true) with check (true);
