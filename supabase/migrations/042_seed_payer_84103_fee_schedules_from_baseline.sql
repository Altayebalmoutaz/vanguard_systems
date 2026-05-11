-- Seed payer_fee_schedules for payer 84103 (Anthem BCBS CA) by copying baseline rows from 52133.
-- Remote DB had 168 rows across other payers but 0 for 84103 — Layer 5 had no contracted amounts.

begin;

insert into rcm.payer_fee_schedules (payer_id, cdt_code, contracted_fee, effective_date)
select '84103', cdt_code, contracted_fee, effective_date
from rcm.payer_fee_schedules
where payer_id = '52133'
on conflict (payer_id, cdt_code, effective_date)
do update
set contracted_fee = excluded.contracted_fee;

commit;
