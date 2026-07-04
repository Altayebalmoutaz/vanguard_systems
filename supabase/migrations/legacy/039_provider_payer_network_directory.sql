-- Provider / payer network directory for fee-path (contracted vs UCR) decisions.
-- Stedi's 271-derived network hints are not always reliable; production can use this
-- facility-level directory (NPI + payer + optional site) when computing Layer 5 estimates.

begin;

create table if not exists rcm.provider_payer_network (
  id uuid primary key default gen_random_uuid(),
  practice_id text not null,
  rendering_provider_npi text not null,
  payer_id text not null references rcm.payer_network (payer_id) on delete cascade,
  provider_service_location_key text,
  in_network_for_fees boolean not null,
  contract_label text,
  notes text,
  effective_from date not null default ((timezone('utc', now()))::date),
  effective_to date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint provider_payer_network_npi_check
    check (rendering_provider_npi ~ '^[0-9]{10}$'),
  constraint provider_payer_network_effective_check
    check (effective_to is null or effective_to >= effective_from)
);

comment on table rcm.provider_payer_network is
  'Practice directory: whether the rendering provider (NPI) participates in-network with a payer for fee / allowed-amount purposes. '
  'Independent of raw 271 network language; use for Layer 5 when Stedi INN/OON is untrusted.';

comment on column rcm.provider_payer_network.provider_service_location_key is
  'Optional clinic/site key (internal id, ZIP, etc.); NULL = default row for this practice + NPI + payer.';

comment on column rcm.provider_payer_network.in_network_for_fees is
  'TRUE: prefer contracted payer_fee_schedules path when present; FALSE: billed / UCR-style path.';

create index if not exists idx_provider_payer_network_lookup
  on rcm.provider_payer_network (practice_id, rendering_provider_npi, payer_id, effective_from desc);

create unique index if not exists idx_provider_payer_network_row_identity
  on rcm.provider_payer_network (
    practice_id,
    rendering_provider_npi,
    payer_id,
    coalesce(provider_service_location_key, ''),
    effective_from
  );

alter table rcm.provider_payer_network enable row level security;

drop policy if exists "provider_payer_network_select" on rcm.provider_payer_network;
create policy "provider_payer_network_select"
  on rcm.provider_payer_network for select
  to anon, authenticated
  using (true);

create or replace view public.provider_payer_network as
select * from rcm.provider_payer_network;

grant select on rcm.provider_payer_network to anon, authenticated;
grant select on public.provider_payer_network to anon, authenticated, service_role;
grant insert, update, delete on rcm.provider_payer_network to service_role;

commit;
