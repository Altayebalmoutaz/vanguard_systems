-- Refresh the public eligibility request view after adding product-intelligence columns.

begin;

create or replace view public.eligibility_requests as
select * from rcm.eligibility_requests;

grant select, insert on public.eligibility_requests to anon, authenticated;
grant select, insert, update on public.eligibility_requests to service_role;

commit;
