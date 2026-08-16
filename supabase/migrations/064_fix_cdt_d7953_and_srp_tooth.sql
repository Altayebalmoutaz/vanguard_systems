-- D7953 was labeled as a sinus lift (D7952 descriptor). Official CDT:
-- bone replacement graft for ridge preservation, per site.
-- D4341/D4342 are quadrant codes; do not require a tooth number.

begin;

update analytics.cdt_codes
set description = 'Bone replacement graft for ridge preservation - per site'
where code = 'D7953';

update public.cdt_codes
set description = 'Bone replacement graft for ridge preservation - per site'
where code = 'D7953';

update analytics.cdt_codes
set requires_tooth = false
where code in ('D4341', 'D4342');

update public.cdt_codes
set requires_tooth = false
where code in ('D4341', 'D4342');

commit;
