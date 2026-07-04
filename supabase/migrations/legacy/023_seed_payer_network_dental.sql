-- Seed common dental payers for Layer-1 deterministic validation.
-- Source references: Stedi Payer Network pages (primary payer IDs).

insert into rcm.payer_network (payer_id, trading_partner_service_id, display_name, coverage_type)
values
  ('60054', '60054', 'Aetna', 'dental'),
  ('62308', '62308', 'Cigna', 'dental'),
  ('61101', '61101', 'Humana', 'dental'),
  ('64246', '64246', 'Guardian', 'dental'),
  ('10134', '10134', 'MetLife Dental Family', 'dental'),
  ('52133', '52133', 'UnitedHealthcare Dental', 'dental'),
  ('CX014', 'CX014', 'DentaQuest - Individual', 'dental'),
  ('47009', '47009', 'Ameritas Life Insurance Corporation', 'dental'),
  ('84105', '84105', 'Anthem Blue Cross Blue Shield Dental', 'dental'),
  ('BCAFD', 'BCAFD', 'Blue Cross Blue Shield FEP BlueDental', 'dental'),
  ('77777', '77777', 'Delta Dental of California', 'dental'),
  ('22189', '22189', 'Delta Dental of New Jersey', 'dental'),
  ('CX013', 'CX013', 'United Concordia - Dental Plus', 'dental'),
  ('00143MC', '00143MC', 'Principal Financial Group', 'dental')
on conflict (payer_id)
do update
set
  trading_partner_service_id = excluded.trading_partner_service_id,
  display_name = excluded.display_name,
  coverage_type = excluded.coverage_type;
