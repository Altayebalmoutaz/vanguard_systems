-- =============================================================================
-- 051 — Fix match_cdt_codes billing_exclusion rule_type typo
-- =============================================================================
-- Baseline 000 defined rule_type = 'billing_ exclusion' (stray space), so the
-- billing_exclusions JSON bucket was always empty. Payer rules use
-- 'billing_exclusion' (see rcm.payer_rules and legacy seeds).

create or replace function public.match_cdt_codes(
  query_embedding vector,
  match_threshold double precision default 0.3,
  match_count integer default 5,
  payer_filter text default 'Delta Dental'::text
)
returns table(
  code text, description text, category text, subcategory text,
  requires_tooth boolean, requires_surfaces boolean, requires_radiograph boolean,
  similarity double precision, deny_rules jsonb, coverage_rules jsonb,
  bundling_rules jsonb, frequency_limits jsonb, documentation_required jsonb,
  billing_exclusions jsonb, processed_as_rules jsonb, not_billable_to_patient jsonb
)
language sql stable as $$
  select
    c.code, c.description, c.category, c.subcategory,
    c.requires_tooth, c.requires_surfaces, c.requires_radiograph,
    1 - (c.embedding <=> query_embedding) as similarity,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='deny') as deny_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text,'contract_note',r.contract_override_note)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='coverage_rule') as coverage_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'transforms_to',r.transforms_to_code,'related_codes',r.related_codes,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='bundling_rule') as bundling_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='frequency_limit') as frequency_limits,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='documentation_required') as documentation_required,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'related_codes',r.related_codes,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='billing_exclusion') as billing_exclusions,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'transforms_to',r.transforms_to_code,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='processed_as') as processed_as_rules,
    (select jsonb_agg(jsonb_build_object('rule_text',r.rule_text,'conditions',r.conditions,'evidence',r.evidence_text)) from payer_rules r where r.code=c.code and r.payer_name=payer_filter and r.rule_type='not_billable_to_patient') as not_billable_to_patient
  from cdt_codes c
  where c.embedding is not null
    and 1 - (c.embedding <=> query_embedding) > match_threshold
  order by similarity desc
  limit match_count;
$$;
