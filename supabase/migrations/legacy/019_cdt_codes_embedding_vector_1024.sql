-- Jina jina-embeddings-v5-text-small outputs up to 1024 dims (Matryoshka); 1536 is invalid for that model.
-- If you used OpenAI-class vector(1536) here before, re-embed after this migration (stored values are dropped).

create extension if not exists vector;

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'cdt_codes'
      and column_name = 'embedding'
  ) then
    alter table public.cdt_codes drop column embedding;
  end if;
end
$$;

alter table public.cdt_codes
  add column if not exists embedding vector(1024);

create index if not exists cdt_codes_embedding_hnsw
  on public.cdt_codes
  using hnsw (embedding vector_cosine_ops)
  where embedding is not null;

comment on column public.cdt_codes.embedding is 'Jina v5 passage embedding (1024); scripts/embed_cdt_jina_backfill.py';
