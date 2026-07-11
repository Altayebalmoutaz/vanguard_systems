-- 010_worker_state.sql
-- Small shared key/value state for background workers (replica- and restart-safe),
-- e.g. DLQ alert dedupe (app/pipeline/dlq_monitor.py).

create table if not exists platform.worker_state (
  key        text primary key,
  value      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
