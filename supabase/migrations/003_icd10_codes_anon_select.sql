-- Required if the FastAPI app uses the Supabase ANON key: RLS must allow SELECT on reference ICD data.
-- Run in Supabase SQL editor after 002_icd10_codes.sql.
drop policy if exists "icd10_codes_select_anon_authenticated" on public.icd10_codes;

create policy "icd10_codes_select_anon_authenticated"
  on public.icd10_codes
  for select
  to anon, authenticated
  using (true);
