-- Deterministic overrides for high-impact Delta Dental policies.
-- Run after 009_delta_dentist_handbook_rules.sql

create schema if not exists coding_agent;

-- Clear previous manual overrides for idempotent reruns.
delete from coding_agent.payer_rules
where payer_name = 'Delta Dental'
  and payer_plan_scope = 'manual_override';

with src as (
  select id
  from coding_agent.rule_sources
  where source_slug = 'delta_dentist_handbook_2026'
)
insert into coding_agent.payer_rules (
  payer_name,
  payer_plan_scope,
  rule_type,
  code,
  transforms_to_code,
  related_codes,
  rule_text,
  conditions,
  contract_override_note,
  source_id,
  source_page,
  evidence_text
)
values
(
  'Delta Dental',
  'manual_override',
  'processed_as',
  'D0120',
  'D0190',
  '{D0190}'::text[],
  'When D0120 is performed without intent to provide dental services to meet patient needs, benefit is processed as D0190.',
  '{"override":"manual_high_impact","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  6,
  'Benefits for D0120 performed without intent ... processed as D0190.'
),
(
  'Delta Dental',
  'manual_override',
  'billing_exclusion',
  'D0145',
  null,
  '{D0425,D1330}'::text[],
  'When D0425 or D1330 are performed on same date as D0145, those additional fees are not billable to patient.',
  '{"override":"manual_high_impact","same_date":true,"source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  7,
  'For D0145, D0425 and D1330 same-date fees not billable to patient.'
),
(
  'Delta Dental',
  'manual_override',
  'processed_as',
  'D0150',
  'D0145',
  '{D0145,D0160,D0180}'::text[],
  'For patients under age 3, submitted D0150, D0160, or D0180 are payable as D0145 and excess fees are not billable to patient.',
  '{"override":"manual_high_impact","age_max":2,"source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  7,
  'Under age 3 comprehensive eval codes are payable as D0145.'
),
(
  'Delta Dental',
  'manual_override',
  'frequency_limit',
  'D0210',
  null,
  null,
  'Comprehensive intraoral series subject to frequency limits by contract; duplicative same-office imaging not separately billable in restricted scenarios.',
  '{"override":"manual_high_impact","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  11,
  'D0210 policy text includes frequency and non-billable duplication constraints.'
),
(
  'Delta Dental',
  'manual_override',
  'bundling_rule',
  'D0340',
  null,
  null,
  'Cephalometric radiographic image is benefited only in conjunction with orthodontic treatment; non-ortho use denied unless contract covers.',
  '{"override":"manual_high_impact","requires_context":"orthodontic","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  17,
  'D0340 requires orthodontic context for benefit.'
),
(
  'Delta Dental',
  'manual_override',
  'processed_as',
  'D0380',
  'D0364',
  '{D0364}'::text[],
  'When policy conditions are met, D0380 may be processed as D0364; D0380 fee itself not separately billable to patient in those scenarios.',
  '{"override":"manual_high_impact","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  18,
  'D0380 processed as D0364 policy.'
),
(
  'Delta Dental',
  'manual_override',
  'deny',
  'D0364',
  null,
  '{D0365,D0366,D0367}'::text[],
  'Cone beam CT submissions may be denied by sequencing/combination policy unless contract-specific criteria are met.',
  '{"override":"manual_high_impact","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  17,
  'D0364-D0367 combination denial language.'
),
(
  'Delta Dental',
  'manual_override',
  'not_billable_to_patient',
  'D9995',
  null,
  '{D9996}'::text[],
  'Telehealth-related case-management/admin style services are not billable to patient under policy unless contract terms differ.',
  '{"override":"manual_high_impact","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  186,
  'D9995/D9996 not billable language in miscellaneous section.'
),
(
  'Delta Dental',
  'manual_override',
  'not_billable_to_patient',
  'D9996',
  null,
  '{D9995}'::text[],
  'Telehealth-related case-management/admin style services are not billable to patient under policy unless contract terms differ.',
  '{"override":"manual_high_impact","source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  186,
  'D9995/D9996 not billable language in miscellaneous section.'
),
(
  'Delta Dental',
  'manual_override',
  'contract_override_notice',
  null,
  null,
  null,
  'Model handbook policies can be superseded by group/individual contract terms; agent outputs must flag when contract-specific lookup is required.',
  '{"override":"manual_high_impact","applies_globally":true,"source_slug":"delta_dentist_handbook_2026"}'::jsonb,
  true,
  (select id from src),
  1,
  'Handbook states contract terms take precedence over model policies.'
);

-- Preferred rule view: prioritize manual overrides over model-policy rows.
create or replace view coding_agent.v_payer_rules_effective as
select distinct on (payer_name, coalesce(code, ''), rule_type, coalesce(transforms_to_code, ''))
  id,
  payer_name,
  payer_plan_scope,
  rule_type,
  code,
  transforms_to_code,
  related_codes,
  rule_text,
  conditions,
  contract_override_note,
  source_id,
  source_page,
  evidence_text,
  created_at
from coding_agent.payer_rules
order by
  payer_name,
  coalesce(code, ''),
  rule_type,
  coalesce(transforms_to_code, ''),
  case when payer_plan_scope = 'manual_override' then 0 else 1 end,
  id desc;

grant select on coding_agent.v_payer_rules_effective to service_role, authenticated, anon;
