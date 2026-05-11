-- Compatibility layer: expose expected master name, even when public.cdt_codes schema varies.
do $$
declare
  has_subcategory boolean;
  has_effective_date boolean;
  has_status boolean;
  has_notes boolean;
  has_source_file boolean;
  has_updated_at boolean;
begin
  if to_regclass('public.cdt_codes') is null then
    create table public.cdt_codes (
      code text primary key,
      description text not null,
      category text,
      subcategory text,
      effective_date date,
      status text,
      notes text,
      source_file text,
      updated_at timestamptz not null default now()
    );
  end if;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'cdt_codes' and column_name = 'subcategory'
  ) into has_subcategory;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'cdt_codes' and column_name = 'effective_date'
  ) into has_effective_date;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'cdt_codes' and column_name = 'status'
  ) into has_status;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'cdt_codes' and column_name = 'notes'
  ) into has_notes;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'cdt_codes' and column_name = 'source_file'
  ) into has_source_file;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'cdt_codes' and column_name = 'updated_at'
  ) into has_updated_at;

  execute format(
    'create or replace view public.cdt_codes_master as
     select
       code,
       description,
       category,
       %s as subcategory,
       %s as effective_date,
       %s as status,
       %s as notes,
       %s as source_file,
       %s as updated_at
     from public.cdt_codes',
    case when has_subcategory then 'subcategory' else 'null::text' end,
    case when has_effective_date then 'effective_date' else 'null::date' end,
    case when has_status then 'status' else 'null::text' end,
    case when has_notes then 'notes' else 'null::text' end,
    case when has_source_file then 'source_file' else 'null::text' end,
    case when has_updated_at then 'updated_at' else 'null::timestamptz' end
  );
end
$$;
