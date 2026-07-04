-- Baseline fee rows for payer AMTAS00425 (same 12 CDT set as other dental payers from 024).

begin;

insert into rcm.payer_fee_schedules (payer_id, cdt_code, contracted_fee, effective_date)
select 'AMTAS00425', cdt_code, contracted_fee, effective_date
from rcm.payer_fee_schedules
where payer_id = '52133'
on conflict (payer_id, cdt_code, effective_date)
do update
set contracted_fee = excluded.contracted_fee;

commit;
