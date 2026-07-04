-- Supabase RPC compatibility wrapper.
-- Exposes adjudication function in public schema for PostgREST lookup.

create or replace function public.adjudicate_claim_line(
  p_payer_name text,
  p_code text,
  p_age int default null,
  p_pos_code text default null,
  p_other_billed_codes text[] default '{}'::text[],
  p_has_prior_auth boolean default false,
  p_has_supporting_docs boolean default false
)
returns jsonb
language sql
stable
as $$
  select coding_agent.adjudicate_claim_line(
    p_payer_name => p_payer_name,
    p_code => p_code,
    p_age => p_age,
    p_pos_code => p_pos_code,
    p_other_billed_codes => p_other_billed_codes,
    p_has_prior_auth => p_has_prior_auth,
    p_has_supporting_docs => p_has_supporting_docs
  );
$$;

grant execute on function public.adjudicate_claim_line(text, text, int, text, text[], boolean, boolean)
to service_role, authenticated, anon;
