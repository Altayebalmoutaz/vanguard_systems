-- 009_opendental_connections.sql
-- Per-clinic OpenDental connectivity (hosted Remote API) + poller control/visibility.
--
-- One row per practice. The dashboard reads/writes this via the FastAPI BFF to
-- control polling and see connection health. Secrets are NOT stored here:
-- `customer_key_ref` names an environment variable on the backend host that holds
-- the clinic's Customer key (the Developer key is a single global env secret).

create table if not exists rcm.opendental_connections (
  id                     uuid primary key default gen_random_uuid(),
  practice_id            text not null unique,
  display_name           text,
  base_url               text not null default 'https://api.opendental.com/api/v1',
  customer_key_ref       text,                       -- env var name holding the clinic Customer key
  poll_enabled           boolean not null default false,
  poll_interval_seconds  numeric not null default 60,
  poll_window_days       integer not null default 0, -- 0 = today only
  cdt_codes              text not null default 'D1110',
  writeback_enabled      boolean not null default false,
  last_poll_at           timestamptz,
  last_poll_status       text,                       -- ok | error | skipped
  last_poll_appointments integer,
  last_error             text,
  health_status          text,                       -- ok | error | unknown
  health_checked_at      timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

drop trigger if exists trg_opendental_connections_updated_at on rcm.opendental_connections;
create trigger trg_opendental_connections_updated_at
  before update on rcm.opendental_connections
  for each row execute function platform.set_updated_at();

alter table rcm.opendental_connections enable row level security;
alter table rcm.opendental_connections force row level security;
drop policy if exists tenant_isolation on rcm.opendental_connections;
create policy tenant_isolation on rcm.opendental_connections
  using (
    current_setting('app.bypass_rls', true) = 'true'
    or practice_id = current_setting('app.practice_id', true)
  )
  with check (
    current_setting('app.bypass_rls', true) = 'true'
    or practice_id = current_setting('app.practice_id', true)
  );

-- Realtime: dashboard OD page live-updates on connection state changes.
drop trigger if exists trg_notify_opendental_connections on rcm.opendental_connections;
create trigger trg_notify_opendental_connections
  after insert or update on rcm.opendental_connections
  for each row execute function platform.notify_rcm_event();
