-- Mock clinic registry + seed: one demo practice, provider–payer fee-network rows, and FK from provider_payer_network.

begin;

-- ---------------------------------------------------------------------------
-- 1. Practices (clinic / tenant directory; no PHI)
-- ---------------------------------------------------------------------------
create table if not exists rcm.practices (
  practice_id text primary key,
  display_name text not null,
  billing_npi text,
  city text,
  state_code text,
  postal_code text,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint practices_billing_npi_ck check (billing_npi is null or billing_npi ~ '^[0-9]{10}$'),
  constraint practices_state_ck check (state_code is null or char_length(state_code) = 2)
);

comment on table rcm.practices is
  'Dental practice / clinic registry. Links to provider_payer_network for fee-path (INN/OON) independent of Stedi 271.';

alter table rcm.practices enable row level security;

drop policy if exists "practices_select" on rcm.practices;
create policy "practices_select"
  on rcm.practices for select
  to anon, authenticated
  using (true);

create or replace view public.practices as
select * from rcm.practices;

grant select on rcm.practices to anon, authenticated;
grant select on public.practices to anon, authenticated, service_role;
grant insert, update, delete on rcm.practices to service_role;

-- ---------------------------------------------------------------------------
-- 2. FK: provider network rows must reference an existing practice
-- ---------------------------------------------------------------------------
alter table rcm.provider_payer_network
  drop constraint if exists provider_payer_network_practice_id_fkey;

alter table rcm.provider_payer_network
  add constraint provider_payer_network_practice_id_fkey
  foreign key (practice_id)
  references rcm.practices (practice_id)
  on delete cascade;

-- ---------------------------------------------------------------------------
-- 3. Seed: one mock Brooklyn clinic + provider fee-network directory
-- ---------------------------------------------------------------------------
-- Mock rendering NPI / practice id are synthetic (non-production). Fee rows align with payer_network seeds (023).
insert into rcm.practices (
  practice_id,
  display_name,
  billing_npi,
  city,
  state_code,
  postal_code,
  notes
)
values (
  'vgd_mock_brooklyn',
  'VanguardDental Mock — Brooklyn Heights',
  '1999999984',
  'Brooklyn',
  'NY',
  '11201',
  'Demo clinic for integration tests: use practice_id + rendering_provider_npi on POST /eligibility/check.'
)
on conflict (practice_id) do update
set
  display_name = excluded.display_name,
  billing_npi = excluded.billing_npi,
  city = excluded.city,
  state_code = excluded.state_code,
  postal_code = excluded.postal_code,
  notes = excluded.notes,
  updated_at = now();

-- Idempotent demo rows: wipe then reload for mock practice id only.
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
    '52133',
    null,
    true,
    'Mock UHC Dental PPO',
    'In-network for fee schedule',
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
    'Same NPI; location key matches provider_service_location_key on request',
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
    'Guardian: out-of-network for fees at this site — estimates use UCR/billed path',
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
    'Second rendering NPI at same practice (hygiene / associate)',
    date '2026-01-01',
    null
  );

commit;
