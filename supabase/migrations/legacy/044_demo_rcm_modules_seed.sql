-- Demo seed data for the unified RCM dashboard modules (coding, prior auth,
-- claims, denials). Field names mirror the backend Pydantic schemas so the
-- frontend can read these via public views without remapping. Demo-only data;
-- safe to re-run. Patient identities are consistent across modules so a single
-- patient can be traced through the full revenue-cycle journey.

begin;

create schema if not exists rcm;

-- ── Coding ───────────────────────────────────────────────────────────────────
create table if not exists rcm.demo_coding_cases (
  id                  text primary key,
  encounter_id        text not null,
  patient_name        text not null,
  dob                 date,
  provider_name       text,
  payer               text,
  clinical_note       text,
  cdt_codes           text[] not null default '{}',
  icd10_codes         text[] not null default '{}',
  confidence          numeric(4, 3) not null default 0,
  justification       text,
  payer_flags         text[] not null default '{}',
  payer_rules_matched jsonb not null default '[]'::jsonb,
  status              text not null default 'pending_review',
  created_at          timestamptz not null default now()
);

truncate rcm.demo_coding_cases;
insert into rcm.demo_coding_cases
  (id, encounter_id, patient_name, dob, provider_name, payer, clinical_note, cdt_codes, icd10_codes, confidence, justification, payer_flags, payer_rules_matched, status, created_at)
values
  ('cd-1001', 'ENC-77421', 'Sarah Mitchell', '1988-04-12', 'Dr. Alan Reyes', 'Anthem BCBS',
   $$Fractured cusp on tooth #14 with deep distal caries. Pulp vitality confirmed. Full-coverage PFM crown indicated.$$,
   '{D2750,D2950}', '{K02.9,K08.89}', 0.95,
   $$PFM crown (D2750) on #14 with core buildup (D2950) supported by extensive coronal destruction.$$,
   '{}', '[{"rule":"crown_frequency_5yr","detail":"No crown billed on #14 in prior 60 months."}]'::jsonb,
   'pending_review', now() - interval '8 minutes'),
  ('cd-1002', 'ENC-77433', 'Priya Nair', '1979-09-21', 'Dr. Alan Reyes', 'MetLife',
   $$Recall exam. Generalized moderate gingival inflammation, BOP 28%. Heavy subgingival calculus.$$,
   '{D4341,D0180}', '{K05.10}', 0.62,
   $$SRP (D4341) suggested, but periodontal charting and radiographic bone loss not documented.$$,
   '{perio_charting_required,low_confidence}',
   '[{"rule":"srp_documentation","detail":"MetLife requires 6-point pocket charting for D4341."}]'::jsonb,
   'pending_review', now() - interval '22 minutes'),
  ('cd-1003', 'ENC-77390', 'James Okafor', '1992-02-03', 'Dr. Naomi Patel', 'Cigna',
   $$Tooth #30 irreversible pulpitis confirmed via cold test and percussion. Periapical radiolucency present.$$,
   '{D3330}', '{K04.0}', 0.91,
   $$Molar endodontic therapy (D3330) on #30 supported by pulpitis (K04.0) and periapical findings.$$,
   '{prior_auth_recommended}',
   '[{"rule":"endo_preauth","detail":"Cigna recommends pre-treatment estimate for molar endo."}]'::jsonb,
   'approved', now() - interval '54 minutes'),
  ('cd-1005', 'ENC-77299', 'Marcus Webb', '1965-11-30', 'Dr. Alan Reyes', 'Humana',
   $$Non-restorable tooth #19 with vertical root fracture. Surgical extraction with socket preservation planned.$$,
   '{D7210,D7953}', '{K08.89}', 0.88,
   $$Surgical extraction (D7210) and socket graft (D7953) supported by non-restorable fracture.$$,
   '{prior_auth_required,graft_narrative_required}',
   '[{"rule":"surgical_extraction_auth","detail":"Humana requires prior auth + radiograph for D7210."}]'::jsonb,
   'pending_review', now() - interval '35 minutes');

-- ── Prior Authorization ──────────────────────────────────────────────────────
create table if not exists rcm.demo_prior_auth_cases (
  id                 text primary key,
  patient_name       text not null,
  dob                date,
  procedure          text,
  procedure_label    text,
  payer              text,
  requires_auth      boolean not null default false,
  required_documents text[] not null default '{}',
  payer_rules        text[] not null default '{}',
  risk_level         text not null default 'low',
  risk_reason        text,
  status             text not null default 'pending_review',
  created_at         timestamptz not null default now()
);

truncate rcm.demo_prior_auth_cases;
insert into rcm.demo_prior_auth_cases
  (id, patient_name, dob, procedure, procedure_label, payer, requires_auth, required_documents, payer_rules, risk_level, risk_reason, status, created_at)
values
  ('pa-2001', 'Marcus Webb', '1965-11-30', 'D7210', 'Surgical extraction (erupted tooth)', 'Humana', true,
   '{"Periapical radiograph (#19)","Surgical narrative","Treatment plan"}',
   '{"Prior auth required for surgical extractions","Radiograph dated within 6 months"}',
   'high', $$High-cost surgical procedure with graft; payer denies without complete radiographic evidence.$$,
   'pending_review', now() - interval '34 minutes'),
  ('pa-2002', 'James Okafor', '1992-02-03', 'D3330', 'Endodontic therapy, molar', 'Cigna', true,
   '{"Pre-treatment periapical","Pulp vitality test results"}',
   '{"Pre-treatment estimate recommended for molar endo"}',
   'medium', $$Documentation present but pre-treatment estimate not yet acknowledged by payer.$$,
   'submitted', now() - interval '52 minutes'),
  ('pa-2003', 'Sarah Mitchell', '1988-04-12', 'D2750', 'Crown - porcelain fused to high noble metal', 'Anthem BCBS', false,
   '{}', '{"Crown covered at 50% after deductible","5-year replacement clause satisfied"}',
   'low', $$Frequency and documentation requirements satisfied; no authorization needed.$$,
   'approved', now() - interval '7 minutes'),
  ('pa-2004', 'Linda Park', '1974-05-08', 'D4910', 'Periodontal maintenance', 'Guardian', false,
   '{"Periodontal history"}', '{"Limited to 4 per year","Frequency check pending"}',
   'medium', $$Patient approaching annual frequency limit; next visit may exceed payer allowance.$$,
   'pending_review', now() - interval '90 minutes');

-- ── Claims ───────────────────────────────────────────────────────────────────
create table if not exists rcm.demo_claims (
  claim_id            text primary key,
  patient_name        text not null,
  dob                 date,
  payer               text,
  provider_name       text,
  status              text not null default 'draft',
  submission_channel  text not null default 'none',
  diagnosis_codes     text[] not null default '{}',
  service_lines       jsonb not null default '[]'::jsonb,
  total_charge_amount numeric(10, 2) not null default 0,
  blockers            text[] not null default '{}',
  available_actions   text[] not null default '{}',
  created_at          timestamptz not null default now()
);

truncate rcm.demo_claims;
insert into rcm.demo_claims
  (claim_id, patient_name, dob, payer, provider_name, status, submission_channel, diagnosis_codes, service_lines, total_charge_amount, blockers, available_actions, created_at)
values
  ('CLM-50412', 'Emily Chen', '1990-07-19', 'Delta Dental', 'Dr. Naomi Patel', 'submitted', 'stedi_mock',
   '{Z01.20}',
   '[{"cdt_code":"D1110","description":"Prophylaxis - adult","charge_amount":120},{"cdt_code":"D0120","description":"Periodic oral evaluation","charge_amount":65}]'::jsonb,
   185, '{}', '{edit,submit}', now() - interval '118 minutes'),
  ('CLM-50418', 'James Okafor', '1992-02-03', 'Cigna', 'Dr. Naomi Patel', 'submitted', 'stedi_mock',
   '{K04.0}',
   '[{"cdt_code":"D3330","description":"Endodontic therapy, molar","charge_amount":1180}]'::jsonb,
   1180, '{}', '{edit,submit}', now() - interval '48 minutes'),
  ('CLM-50421', 'Sarah Mitchell', '1988-04-12', 'Anthem BCBS', 'Dr. Alan Reyes', 'draft', 'none',
   '{K02.9,K08.89}',
   '[{"cdt_code":"D2750","description":"Crown - porcelain fused to high noble metal","charge_amount":1240},{"cdt_code":"D2950","description":"Core buildup, including any pins","charge_amount":285}]'::jsonb,
   1525, '{}', '{edit,submit}', now() - interval '6 minutes'),
  ('CLM-50425', 'Marcus Webb', '1965-11-30', 'Humana', 'Dr. Alan Reyes', 'pending_auth', 'none',
   '{K08.89}',
   '[{"cdt_code":"D7210","description":"Surgical extraction, erupted tooth","charge_amount":410},{"cdt_code":"D7953","description":"Bone graft, socket preservation","charge_amount":520}]'::jsonb,
   930, '{"Prior authorization not yet approved","Surgical narrative missing"}', '{edit}', now() - interval '30 minutes');

-- ── Denials ──────────────────────────────────────────────────────────────────
create table if not exists rcm.demo_denials (
  claim_id             text primary key,
  patient_name         text not null,
  dob                  date,
  payer                text,
  status               text not null default 'denied',
  reason               text,
  reason_label         text,
  next_action          text,
  amount_at_risk       numeric(10, 2) not null default 0,
  resubmission_steps   text[] not null default '{}',
  required_evidence    text[] not null default '{}',
  reasoning_summary    text,
  appeal_letter        text,
  requires_human_review boolean not null default false,
  created_at           timestamptz not null default now()
);

truncate rcm.demo_denials;
insert into rcm.demo_denials
  (claim_id, patient_name, dob, payer, status, reason, reason_label, next_action, amount_at_risk, resubmission_steps, required_evidence, reasoning_summary, appeal_letter, requires_human_review, created_at)
values
  ('CLM-50301', 'Robert Hughes', '1958-03-14', 'UnitedHealthcare', 'denied', 'missing_xray', 'Missing radiograph', 'upload_xray_and_resubmit', 980,
   '{"Attach pre-operative periapical radiograph for tooth #3","Verify image date is within payer window","Resubmit corrected claim via clearinghouse"}',
   '{"Periapical radiograph (#3)"}',
   $$Payer denied for missing supporting radiograph. Deterministic mapping and LLM agree at 0.93 confidence.$$,
   $$Re: Claim CLM-50301 - Robert Hughes

To the UnitedHealthcare Dental Review Team,

We are submitting supporting documentation for the crown procedure (D2750) performed on tooth #3. The claim was denied for a missing radiograph. Enclosed is the pre-operative periapical radiograph dated within the eligible window, demonstrating medical necessity for the restoration.

We respectfully request reprocessing of this claim.

Sincerely,
Bright Smiles Dental Billing$$,
   false, now() - interval '4 hours'),
  ('CLM-50288', 'Linda Park', '1974-05-08', 'Guardian', 'denied', 'frequency_limit', 'Frequency limitation', 'notify_patient', 142,
   '{"Confirm last periodontal maintenance date","Notify patient of frequency limitation and patient responsibility","Offer self-pay estimate"}',
   '{"Prior D4910 service date"}',
   $$Periodontal maintenance exceeds payer frequency allowance (4/year). Not appealable; patient notification recommended.$$,
   '', true, now() - interval '5 hours'),
  ('CLM-50276', 'Dilan Rivera', '1985-12-22', 'Cigna', 'denied', 'not_covered', 'Procedure not covered', 'review_contract_and_patient_balance', 320,
   '{"Review plan exclusions for D9972","Confirm cosmetic exclusion in patient contract","Move balance to patient responsibility"}',
   '{"Plan benefit booklet excerpt"}',
   $$Cosmetic procedure excluded under plan. Deterministic mapping confident; balance should move to patient.$$,
   '', false, now() - interval '7 hours'),
  ('CLM-50264', 'Sofia Almeida', '1996-08-02', 'Aetna', 'partial', 'invalid_code', 'Invalid / downcoded procedure', 'correct_code_and_resubmit', 240,
   '{"Review downcode from D2393 to D2392","Verify surface count in clinical note","Resubmit with corrected procedure code and narrative"}',
   '{"Operative note with surface detail"}',
   $$Payer downcoded a 3-surface posterior composite to 2-surface. Narrative supports original code; appeal recommended.$$,
   $$Re: Claim CLM-50264 - Sofia Almeida

To the Aetna Dental Claims Department,

This claim for a three-surface posterior composite (D2393) on tooth #19 was downcoded to D2392. Our operative note documents involvement of the mesial, occlusal, and distal surfaces, supporting the original three-surface restoration. We request reprocessing at the originally billed code.

Sincerely,
Bright Smiles Dental Billing$$,
   false, now() - interval '8 hours');

-- ── Public read views for the dashboard (anon key) ───────────────────────────
create or replace view public.demo_coding_cases as select * from rcm.demo_coding_cases;
create or replace view public.demo_prior_auth_cases as select * from rcm.demo_prior_auth_cases;
create or replace view public.demo_claims as select * from rcm.demo_claims;
create or replace view public.demo_denials as select * from rcm.demo_denials;

grant select on public.demo_coding_cases to anon, authenticated;
grant select on public.demo_prior_auth_cases to anon, authenticated;
grant select on public.demo_claims to anon, authenticated;
grant select on public.demo_denials to anon, authenticated;

commit;
