-- Daily KPI buckets for dashboard sparklines (public RPC; respects RLS on underlying view).

begin;

create or replace function public.eligibility_daily_kpi_buckets(p_days integer default 7)
returns table(bucket_date date, total_count bigint, verified_count bigint)
language sql
stable
security invoker
set search_path = public
as $$
  select
    (created_at at time zone 'UTC')::date as bucket_date,
    count(*)::bigint as total_count,
    count(*) filter (where status_label = 'Verified')::bigint as verified_count
  from public.eligibility_dashboard_rows
  where created_at >= ((timezone('UTC', now()))::date - p_days)
  group by 1
  order by 1 asc;
$$;

grant execute on function public.eligibility_daily_kpi_buckets(integer) to anon, authenticated, service_role;

commit;
