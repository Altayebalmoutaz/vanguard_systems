-- Backfill CDT documentation-requirement flags (requires_tooth/surfaces/radiograph),
-- which were 100% NULL and made the coding agent's gap gate fall back to blunt
-- code-prefix heuristics (root cause of crown SURFACE_MISSING false positives).
--
-- analytics.cdt_codes is authoritative; public.cdt_codes is kept as a synced
-- mirror because the pgvector RPC public.match_cdt_codes and the app both bind
-- to public (public holds the vector index; do NOT convert it to a view).
--
-- The code-range CASE below mirrors app/coding/cdt_requirements.py
-- (code_range_requirements). OpenDental procedurecode.TreatArea refines
-- tooth/surface authoritatively via scripts/import_od_treatarea.py.

begin;

-- Staging table for the OpenDental procedure catalog (a code dictionary, no PHI).
create table if not exists analytics.od_procedurecode_catalog (
  proc_code   text primary key,
  descript    text,
  abbr_desc   text,
  treat_area  text,
  imported_at timestamptz not null default now()
);

-- Deterministic code-range baseline for both authoritative copies.
with nums as (
  select code,
         case when code ~ '^D[0-9]{4}' then substring(code from 2 for 4)::int end as n
  from analytics.cdt_codes
)
update analytics.cdt_codes c
set
  requires_tooth = case
    when n between 2000 and 2999 then true
    when n between 3000 and 3999 then true
    when (n between 4210 and 4249) or n in (4341, 4342) then true
    when n between 6000 and 6999 then true
    when n between 7000 and 7999 then true
    when n in (1351, 1352, 1353, 1354) then true
    else false end,
  requires_surfaces = case
    when (n between 2140 and 2161) or (n between 2330 and 2394) then true
    else false end,
  requires_radiograph = case
    when n between 210 and 367 then true
    when n between 2510 and 2799 then true
    when n between 2900 and 2999 then true
    when n between 3000 and 3999 then true
    when (n between 4210 and 4249) or n in (4341, 4342) then true
    when n between 7000 and 7999 then true
    else false end
from nums
where nums.code = c.code and nums.n is not null;

-- Sync flags analytics -> public (mirror only; preserves public's vector index).
update public.cdt_codes p
set requires_tooth      = a.requires_tooth,
    requires_surfaces   = a.requires_surfaces,
    requires_radiograph = a.requires_radiograph
from analytics.cdt_codes a
where a.code = p.code;

commit;
