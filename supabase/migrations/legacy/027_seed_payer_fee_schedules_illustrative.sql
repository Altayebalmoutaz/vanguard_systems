-- Illustrative contracted fees for common Stedi mock / Anthem-style payer IDs (dev & demos).
-- Replace with real contracted rates in production. Safe to re-run via ON CONFLICT.

insert into public.payer_fee_schedules (payer_id, cdt_code, contracted_fee, effective_date)
values
  ('84103', 'D1110', 165.00, '2020-01-01'),
  ('84103', 'D2740', 1100.00, '2020-01-01'),
  ('040', 'D1110', 165.00, '2020-01-01'),
  ('040', 'D2740', 1100.00, '2020-01-01')
on conflict (payer_id, cdt_code, effective_date)
do update set contracted_fee = excluded.contracted_fee;
