-- Seed aliases into rcm.payer_identity_alias (025). Superseded at runtime by 027, which
-- merges these rows into rcm.payer_network.aliases and drops payer_identity_alias.

begin;

insert into rcm.payer_identity_alias (payer_id, alias_normalized)
values
  ('60054', 'aetna'),
  ('60054', 'aetna dental'),
  ('62308', 'cigna'),
  ('62308', 'cigna dental'),
  ('61101', 'humana'),
  ('61101', 'humana dental'),
  ('64246', 'guardian'),
  ('64246', 'guardian dental'),
  ('10134', 'metlife'),
  ('10134', 'metlife dental'),
  ('52133', 'united healthcare dental'),
  ('52133', 'united healthcare'),
  ('52133', 'uhc dental'),
  ('52133', 'uhc'),
  ('CX014', 'dentaquest'),
  ('47009', 'ameritas'),
  ('84105', 'anthem'),
  ('84105', 'anthem blue cross'),
  ('BCAFD', 'fep bluedental'),
  ('BCAFD', 'federal employee'),
  ('77777', 'delta dental california'),
  ('77777', 'delta dental of california'),
  ('77777', 'delta ca'),
  ('22189', 'delta dental new jersey'),
  ('22189', 'delta dental of new jersey'),
  ('22189', 'delta nj'),
  ('CX013', 'united concordia'),
  ('CX013', 'united concordia dental'),
  ('00143MC', 'principal'),
  ('00143MC', 'principal financial')
on conflict (alias_normalized) do nothing;

commit;
