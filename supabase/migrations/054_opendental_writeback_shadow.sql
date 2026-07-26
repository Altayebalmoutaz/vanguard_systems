-- Mirror schema/migrations/013_opendental_writeback_shadow.sql for Supabase bridge.
alter table if exists rcm.opendental_connections
  add column if not exists writeback_shadow_compare boolean not null default false;

comment on column rcm.opendental_connections.writeback_shadow_compare is
  'When true with writeback_full, run benefits grid + InsAdjust in dry-run (diff only).';
