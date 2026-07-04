-- Step 1: Canonical payer identity — directory is rcm.payer_network (payer_id PK,
-- trading_partner_service_id = Stedi clearinghouse id in our seeds).
-- This table maps human / fuzzy insurance strings to payer_id for RCM rules and future PA flows.

begin;

create table if not exists rcm.payer_identity_alias (
  id uuid primary key default gen_random_uuid(),
  payer_id text not null references rcm.payer_network (payer_id) on delete cascade,
  alias_normalized text not null,
  created_at timestamptz not null default now(),
  constraint payer_identity_alias_alias_unique unique (alias_normalized)
);

create index if not exists idx_payer_identity_alias_payer_id
  on rcm.payer_identity_alias (payer_id);

comment on table rcm.payer_identity_alias is
  'Maps normalized free-text insurance labels to canonical payer_id (same family as trading_partner_service_id for Stedi).';

-- PostgREST / app compatibility (same pattern as other rcm tables).
create or replace view public.payer_identity_alias as
select * from rcm.payer_identity_alias;

grant select, insert, update, delete on rcm.payer_identity_alias to service_role;
grant select on public.payer_identity_alias to anon, authenticated, service_role;

commit;
