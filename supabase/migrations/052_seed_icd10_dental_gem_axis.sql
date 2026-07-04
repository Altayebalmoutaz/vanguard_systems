-- =============================================================================
-- 052 — Seed analytics.icd10_dental_gem_axis (Wave 1D coding consolidation)
-- =============================================================================
-- Minimal dental ICD-10 GEM rows for coding-agent validation and tests.
-- Full GEM crosswalk can be loaded out-of-band; this unblocks K02.9 / K04.0 paths.

insert into analytics.icd10_dental_gem_axis (
  record_id,
  icd10_code_compact,
  icd10_code,
  icd10_description,
  icd9_code_compact,
  icd9_code,
  icd9_description,
  axis_group,
  flag_1,
  flag_2,
  flag_3,
  flag_4,
  flag_5,
  gem_axis,
  combined_line,
  notes
) values
  (
    'seed-k029',
    'K029',
    'K02.9',
    'Dental caries, unspecified',
    '5210',
    '521.0',
    'Dental caries',
    'dental',
    '0', '0', '0', '0', '0',
    'forward',
    'K02.9 Dental caries, unspecified -> 521.0 Dental caries',
    'Wave 1D minimal seed for coding validation'
  ),
  (
    'seed-k040',
    'K040',
    'K04.0',
    'Pulpitis',
    '5220',
    '522.0',
    'Pulpitis',
    'dental',
    '0', '0', '0', '0', '0',
    'forward',
    'K04.0 Pulpitis -> 522.0 Pulpitis',
    'Wave 1D minimal seed for coding validation'
  )
on conflict (record_id) do update set
  icd10_code_compact = excluded.icd10_code_compact,
  icd10_code = excluded.icd10_code,
  icd10_description = excluded.icd10_description,
  icd9_code_compact = excluded.icd9_code_compact,
  icd9_code = excluded.icd9_code,
  icd9_description = excluded.icd9_description,
  axis_group = excluded.axis_group,
  flag_1 = excluded.flag_1,
  flag_2 = excluded.flag_2,
  flag_3 = excluded.flag_3,
  flag_4 = excluded.flag_4,
  flag_5 = excluded.flag_5,
  gem_axis = excluded.gem_axis,
  combined_line = excluded.combined_line,
  notes = excluded.notes;

-- Guard: callable smoke check that the validator table is seeded.
create or replace function public.check_icd10_dental_gem_axis_nonempty()
returns boolean
language sql
stable
security invoker
as $$
  select exists (
    select 1
    from analytics.icd10_dental_gem_axis
    limit 1
  );
$$;

comment on function public.check_icd10_dental_gem_axis_nonempty() is
  'Returns true when analytics.icd10_dental_gem_axis has at least one row.';

grant execute on function public.check_icd10_dental_gem_axis_nonempty()
  to anon, authenticated, service_role;
