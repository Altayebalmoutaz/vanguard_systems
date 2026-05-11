-- Align existing public.cdt_codes table to expected loader schema for 013.

create table if not exists public.cdt_codes (
  code text primary key,
  description text not null
);

alter table public.cdt_codes
  add column if not exists category text,
  add column if not exists subcategory text,
  add column if not exists effective_date date,
  add column if not exists status text,
  add column if not exists notes text,
  add column if not exists source_file text,
  add column if not exists updated_at timestamptz not null default now();

-- Backfill updated_at where null.
update public.cdt_codes
set updated_at = now()
where updated_at is null;
