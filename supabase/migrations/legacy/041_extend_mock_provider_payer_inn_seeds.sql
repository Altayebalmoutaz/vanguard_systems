-- Extend mock Brooklyn provider_payer_network: Anthem 84103, Ameritas AMTAS00425, Cigna 62308,
-- MetLife 10134, UHC 52133 — all INN for fees (in_network_for_fees=true on those rows).

begin;

delete from rcm.provider_payer_network where practice_id = 'vgd_mock_brooklyn';

insert into rcm.provider_payer_network (
  practice_id,
  rendering_provider_npi,
  payer_id,
  provider_service_location_key,
  in_network_for_fees,
  contract_label,
  notes,
  effective_from,
  effective_to
)
values
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '84103',
    null,
    true,
    'Mock Anthem BCBS CA (Stedi)',
    'Primary payer id from Stedi doc-style scenarios',
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    'AMTAS00425',
    null,
    true,
    'Ameritas participating',
    'Ameritas / AMTAS00425 dental',
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '62308',
    null,
    true,
    'Mock Cigna PPO',
    'Primary rendering NPI fee network',
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '10134',
    null,
    true,
    'MetLife Dental Family',
    null,
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '52133',
    null,
    true,
    'UnitedHealthcare Dental',
    null,
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '60054',
    null,
    true,
    'Mock Aetna Advantage',
    'Participating general dentist — Brooklyn Heights',
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '77777',
    'site_main',
    true,
    'Mock Delta Dental CA — office key',
    'Use provider_service_location_key=site_main on request',
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1104023674',
    '64246',
    null,
    false,
    null,
    'Regression: Guardian OON for fees at this site — UCR/billed path',
    date '2026-01-01',
    null
  ),
  (
    'vgd_mock_brooklyn',
    '1982654321',
    '62308',
    null,
    true,
    'Mock Cigna — associate provider',
    'Associate NPI at same practice',
    date '2026-01-01',
    null
  );

commit;
