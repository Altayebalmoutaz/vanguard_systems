
create schema if not exists coding_agent;

create or replace function coding_agent.adjudicate_claim_line(
  p_payer_name text,
  p_code text,
  p_age int default null,
  p_pos_code text default null,
  p_other_billed_codes text[] default '{}'::text[],
  p_has_prior_auth boolean default false,
  p_has_supporting_docs boolean default false
)
returns jsonb
language plpgsql
stable
as $$
declare
  v_medicaid_rules jsonb := '[]'::jsonb;
  v_delta_rules jsonb := '[]'::jsonb;
  v_all_rules jsonb := '[]'::jsonb;

  v_processed_as text := null;
  v_conflicts text[] := '{}'::text[];
  v_unmet text[] := '{}'::text[];
  v_next_actions text[] := '{}'::text[];
  v_warnings text[] := '{}'::text[];

  v_has_deny boolean := false;
  v_requires_pa boolean := false;
  v_requires_docs boolean := false;
  v_not_billable_to_patient boolean := false;

  v_status text := 'allow';
begin
  -- Medicaid structured rules (for Medicaid payers).
  if lower(p_payer_name) like '%medicaid%' then
    select coalesce(
      jsonb_agg(
        jsonb_build_object(
          'source', 'medicaid_structured',
          'rule_type', r.rule_type,
          'code', r.code,
          'rule_text', r.rule_text,
          'requires_prior_auth', r.requires_prior_auth,
          'requires_report', r.requires_report,
          'not_billable_with_codes', coalesce(to_jsonb(r.not_billable_with_codes), '[]'::jsonb),
          'source_page', r.source_page
        )
      ),
      '[]'::jsonb
    )
    into v_medicaid_rules
    from coding_agent.cdt_payer_rules_structured r
    where r.code = p_code
      and lower(r.payer_name) = lower(p_payer_name)
      and (p_age is null or (coalesce(r.age_min, -1) <= p_age and coalesce(r.age_max, 999) >= p_age))
      and (p_pos_code is null or r.allowed_pos_codes is null or p_pos_code = any(r.allowed_pos_codes));
  end if;

  -- Delta and other payer rules from effective view.
  if lower(p_payer_name) like '%delta%' then
    select coalesce(
      jsonb_agg(
        jsonb_build_object(
          'source', 'payer_rules_effective',
          'rule_type', r.rule_type,
          'code', r.code,
          'transforms_to_code', r.transforms_to_code,
          'related_codes', coalesce(to_jsonb(r.related_codes), '[]'::jsonb),
          'rule_text', r.rule_text,
          'contract_override_note', r.contract_override_note,
          'source_page', r.source_page
        )
      ),
      '[]'::jsonb
    )
    into v_delta_rules
    from coding_agent.v_payer_rules_effective r
    where lower(r.payer_name) = lower(p_payer_name)
      and (r.code = p_code or r.code is null);
  end if;

  v_all_rules := v_medicaid_rules || v_delta_rules;

  -- High-level booleans from Medicaid side.
  if lower(p_payer_name) like '%medicaid%' then
    select
      coalesce(bool_or(r.rule_type = 'billing_exclusion'), false),
      coalesce(bool_or(r.requires_prior_auth), false),
      coalesce(bool_or(r.requires_report), false),
      coalesce(array_agg(distinct x.code) filter (where x.code is not null), '{}'::text[])
    into
      v_has_deny,
      v_requires_pa,
      v_requires_docs,
      v_conflicts
    from coding_agent.cdt_payer_rules_structured r
    left join lateral unnest(coalesce(r.not_billable_with_codes, '{}'::text[])) as x(code) on true
    where r.code = p_code
      and lower(r.payer_name) = lower(p_payer_name)
      and (p_age is null or (coalesce(r.age_min, -1) <= p_age and coalesce(r.age_max, 999) >= p_age))
      and (p_pos_code is null or r.allowed_pos_codes is null or p_pos_code = any(r.allowed_pos_codes));
  end if;

  -- High-level booleans from Delta side.
  if lower(p_payer_name) like '%delta%' then
    select
      coalesce(bool_or(r.rule_type = 'deny'), false),
      coalesce(bool_or(r.rule_type = 'prior_auth'), false),
      coalesce(bool_or(r.rule_type = 'documentation_required'), false),
      coalesce(bool_or(r.rule_type = 'not_billable_to_patient'), false),
      max(r.transforms_to_code) filter (where r.rule_type = 'processed_as' and r.transforms_to_code is not null),
      coalesce(array_agg(distinct rel.code) filter (where rel.code is not null), '{}'::text[]),
      coalesce(array_agg(distinct r.rule_text) filter (where r.rule_type = 'contract_override_notice'), '{}'::text[])
    into
      v_has_deny,
      v_requires_pa,
      v_requires_docs,
      v_not_billable_to_patient,
      v_processed_as,
      v_conflicts,
      v_warnings
    from coding_agent.v_payer_rules_effective r
    left join lateral unnest(coalesce(r.related_codes, '{}'::text[])) as rel(code) on true
    where lower(r.payer_name) = lower(p_payer_name)
      and (r.code = p_code or r.code is null);
  end if;

  -- Keep only conflict codes present on this claim line context.
  select coalesce(array_agg(distinct c), '{}'::text[])
  into v_conflicts
  from unnest(coalesce(v_conflicts, '{}'::text[])) as c
  where c = any(coalesce(p_other_billed_codes, '{}'::text[]));

  -- Unmet requirements.
  if v_requires_pa and not p_has_prior_auth then
    v_unmet := array_append(v_unmet, 'prior_auth_missing');
  end if;
  if v_requires_docs and not p_has_supporting_docs then
    v_unmet := array_append(v_unmet, 'supporting_docs_missing');
  end if;

  -- Determine status.
  if v_has_deny then
    v_status := 'deny';
  elsif array_length(v_unmet, 1) is not null then
    v_status := 'pend';
  elsif array_length(v_conflicts, 1) is not null then
    v_status := 'review';
  else
    v_status := 'allow';
  end if;

  -- Next actions.
  if v_status = 'deny' then
    v_next_actions := array_append(v_next_actions, 'do_not_submit_without_override_or_contract_check');
  end if;
  if 'prior_auth_missing' = any(v_unmet) then
    v_next_actions := array_append(v_next_actions, 'obtain_prior_auth');
  end if;
  if 'supporting_docs_missing' = any(v_unmet) then
    v_next_actions := array_append(v_next_actions, 'attach_supporting_documentation');
  end if;
  if array_length(v_conflicts, 1) is not null then
    v_next_actions := array_append(v_next_actions, 'review_code_conflicts');
  end if;
  if v_processed_as is not null and v_processed_as <> p_code then
    v_next_actions := array_append(v_next_actions, 'consider_code_transform');
  end if;
  if v_not_billable_to_patient then
    v_next_actions := array_append(v_next_actions, 'suppress_patient_balance_for_non_billable_component');
  end if;

  return jsonb_build_object(
    'input', jsonb_build_object(
      'payer_name', p_payer_name,
      'code', p_code,
      'age', p_age,
      'pos_code', p_pos_code,
      'other_billed_codes', coalesce(to_jsonb(p_other_billed_codes), '[]'::jsonb),
      'has_prior_auth', p_has_prior_auth,
      'has_supporting_docs', p_has_supporting_docs
    ),
    'status', v_status,
    'processed_as_code', v_processed_as,
    'requires_prior_auth', v_requires_pa,
    'requires_supporting_docs', v_requires_docs,
    'not_billable_to_patient', v_not_billable_to_patient,
    'conflicts_with_billed_codes', coalesce(to_jsonb(v_conflicts), '[]'::jsonb),
    'unmet_requirements', coalesce(to_jsonb(v_unmet), '[]'::jsonb),
    'warnings', coalesce(to_jsonb(v_warnings), '[]'::jsonb),
    'next_actions', coalesce(to_jsonb(v_next_actions), '[]'::jsonb),
    'triggered_rules', v_all_rules
  );
end;
$$;

grant execute on function coding_agent.adjudicate_claim_line(text, text, int, text, text[], boolean, boolean)
to service_role, authenticated, anon;
