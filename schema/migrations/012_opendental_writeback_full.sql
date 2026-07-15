-- 012_opendental_writeback_full.sql
-- Persist full writeback (insadjust + benefits grid) on the connection row.
-- Code already reads/writes this column via connections_store + post_check;
-- 009 omitted it. Idempotent for databases that already have the column.

alter table rcm.opendental_connections
  add column if not exists writeback_full boolean not null default false;

comment on column rcm.opendental_connections.writeback_full is
  'When true with writeback_enabled, enqueue OD writeback with insadjust + benefits grid on.';
