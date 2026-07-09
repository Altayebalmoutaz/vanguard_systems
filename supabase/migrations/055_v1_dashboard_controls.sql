-- v1 dashboard controls: full OD writeback toggle per connection.

begin;

alter table rcm.opendental_connections
  add column if not exists writeback_full boolean not null default false;

comment on column rcm.opendental_connections.writeback_full is
  'When writeback_enabled is true, enables all write targets (notes, commlog, verifies, insadjust, benefits grid).';

commit;
