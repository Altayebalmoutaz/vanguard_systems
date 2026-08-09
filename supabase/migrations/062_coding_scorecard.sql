-- Live accuracy scorecard for the coding agent. Joins each suggested line
-- (agents.coding_runs.response_payload) to the dentist's decision
-- (agents.coding_decisions) so we can report CDT top-1 accuracy @ coverage,
-- needs_info rate, and false-gap rate sliced by payer and CDT family.

begin;

-- Line-level outcomes: one row per suggested recommendation, with its decision.
create or replace view analytics.coding_line_outcomes as
select
  r.practice_id,
  r.id                                                    as coding_run_id,
  r.payer_id,
  r.status                                                as run_status,
  (r.created_at at time zone 'utc')::date                 as run_date,
  rec->>'line_id'                                         as line_id,
  upper(coalesce(nullif(rec->>'cdt_code', ''), ''))       as suggested_cdt,
  case when nullif(rec->>'cdt_code', '') is not null
       then left(upper(rec->>'cdt_code'), 3) end          as cdt_family,
  coalesce((rec->>'confidence')::numeric, 0)              as confidence,
  jsonb_array_length(coalesce(rec->'missing_info', '[]'::jsonb)) > 0 as line_had_gap,
  d.action                                                as decision_action,
  upper(coalesce(nullif(d.final_cdt, ''), ''))            as final_cdt,
  d.decided_at
from agents.coding_runs r
cross join lateral jsonb_array_elements(
  coalesce(r.response_payload->'recommendations', '[]'::jsonb)
) as rec
left join agents.coding_decisions d
  on  d.practice_id   = r.practice_id
  and d.coding_run_id = r.id
  and d.line_id       = rec->>'line_id';

-- Aggregate scorecard sliced by practice / payer / CDT family / day.
create or replace view analytics.coding_scorecard as
select
  practice_id,
  coalesce(payer_id, 'unknown')                                       as payer_id,
  coalesce(cdt_family, 'none')                                        as cdt_family,
  run_date,
  count(*)                                                            as lines_total,
  count(*) filter (where suggested_cdt <> '')                         as lines_proposed,
  count(decision_action)                                             as lines_decided,
  count(*) filter (
    where decision_action is not null
      and (decision_action = 'approved'
           or (final_cdt <> '' and final_cdt = suggested_cdt))
  )                                                                   as top1_hits,
  count(*) filter (where line_had_gap)                                as lines_with_gap,
  count(*) filter (where line_had_gap and decision_action = 'approved') as false_gaps,
  count(*) filter (where run_status = 'needs_info')                   as lines_in_needs_info
from analytics.coding_line_outcomes
group by practice_id, coalesce(payer_id, 'unknown'),
         coalesce(cdt_family, 'none'), run_date;

grant select on analytics.coding_line_outcomes to service_role;
grant select on analytics.coding_scorecard to service_role;

commit;
