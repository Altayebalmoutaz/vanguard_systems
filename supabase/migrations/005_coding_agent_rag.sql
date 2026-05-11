-- RAG storage for PDF manuals (CDT, ICD-10-CM, payer guides) under coding_agent schema.
-- Run in Supabase SQL Editor if you do not apply via psql.
-- Requires: extension vector (Dashboard → Database → Extensions → vector).

create extension if not exists vector;

-- Registered source PDFs / corpora
create table if not exists coding_agent.rag_documents (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  source_type text not null
    check (source_type in ('cdt', 'icd10_cm', 'delta_manual', 'ny_manual', 'other')),
  file_name text,
  created_at timestamptz not null default now()
);

comment on table coding_agent.rag_documents is 'Logical document (one PDF or corpus) for RAG retrieval.';

-- Chunked text + optional embedding (1536 dims = OpenAI text-embedding-3-small family)
create table if not exists coding_agent.rag_document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references coding_agent.rag_documents (id) on delete cascade,
  chunk_index int not null,
  page_start int,
  page_end int,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  unique (document_id, chunk_index)
);

comment on table coding_agent.rag_document_chunks is 'Overlapping text chunks from PDFs; embedding filled after ingest for similarity search.';

create index if not exists rag_document_chunks_document_id_idx
  on coding_agent.rag_document_chunks (document_id);

-- Similarity search (cosine). Partial index so rows with NULL embedding do not break ingest.
create index if not exists rag_document_chunks_embedding_hnsw
  on coding_agent.rag_document_chunks
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64)
  where (embedding is not null);

create or replace function coding_agent.match_rag_chunks(
  query_embedding vector(1536),
  match_count int default 8,
  filter_source_types text[] default null
)
returns table (
  chunk_id uuid,
  document_id uuid,
  slug text,
  source_type text,
  content text,
  metadata jsonb,
  page_start int,
  page_end int,
  similarity float
)
language sql
stable
parallel safe
as $$
  select
    c.id as chunk_id,
    c.document_id,
    d.slug,
    d.source_type,
    c.content,
    c.metadata,
    c.page_start,
    c.page_end,
    (1 - (c.embedding <=> query_embedding))::float as similarity
  from coding_agent.rag_document_chunks c
  join coding_agent.rag_documents d on d.id = c.document_id
  where c.embedding is not null
    and (filter_source_types is null or d.source_type = any (filter_source_types))
  order by c.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;

comment on function coding_agent.match_rag_chunks is 'Cosine similarity search over ingested chunk embeddings.';

-- Backend (service role) needs full CRUD; optional read for future dashboard clients.
grant select, insert, update, delete on table coding_agent.rag_documents to service_role;
grant select, insert, update, delete on table coding_agent.rag_document_chunks to service_role;
grant select on table coding_agent.rag_documents to authenticated, anon;
grant select on table coding_agent.rag_document_chunks to authenticated, anon;
grant execute on function coding_agent.match_rag_chunks to service_role, authenticated, anon;
