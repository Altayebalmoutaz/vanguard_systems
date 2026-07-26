-- 013_opendental_writeback_shadow.sql
-- Shadow-compare mode for Layer 3/4 (benefits grid + InsAdjust): propose diffs
-- without applying, for pilot clinics before enabling full writeback apply.

alter table rcm.opendental_connections
  add column if not exists writeback_shadow_compare boolean not null default false;

comment on column rcm.opendental_connections.writeback_shadow_compare is
  'When true with writeback_full, run benefits grid + InsAdjust in dry-run (diff only); notes/insverify/commlog still write.';

-- Recommended pre-appointment reverify window (48–72h ≈ 2–3 days). Clinics can override.
-- No schema change for poll_window_days (already 0–30); document default of 3 for pilots.
