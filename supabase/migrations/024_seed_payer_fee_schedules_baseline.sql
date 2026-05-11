-- Baseline fee schedules for Layer-5 estimation.
-- NOTE: These are starter benchmark values for operational readiness/tests.
-- Replace with contracted payer-specific fee schedules before financial go-live.

with dental_payers as (
  select payer_id
  from rcm.payer_network
  where coverage_type = 'dental'
),
baseline_fees as (
  select *
  from (
    values
      ('D0120'::text, 55.00::numeric),
      ('D0150'::text, 95.00::numeric),
      ('D0220'::text, 45.00::numeric),
      ('D0274'::text, 65.00::numeric),
      ('D1110'::text, 120.00::numeric),
      ('D2330'::text, 185.00::numeric),
      ('D2391'::text, 210.00::numeric),
      ('D2740'::text, 980.00::numeric),
      ('D4910'::text, 145.00::numeric),
      ('D7140'::text, 210.00::numeric),
      ('D7210'::text, 330.00::numeric),
      ('D8080'::text, 4200.00::numeric)
  ) as t(cdt_code, contracted_fee)
),
effective as (
  select date '2026-01-01' as effective_date
)
insert into rcm.payer_fee_schedules (payer_id, cdt_code, contracted_fee, effective_date)
select p.payer_id, f.cdt_code, f.contracted_fee, e.effective_date
from dental_payers p
cross join baseline_fees f
cross join effective e
on conflict (payer_id, cdt_code, effective_date)
do update
set contracted_fee = excluded.contracted_fee;
