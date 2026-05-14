create extension if not exists vector;

create table if not exists public.kb_chunks (
    id uuid primary key,
    content text not null,
    embedding vector(1536),
    source_type text not null,
    metadata jsonb not null default '{}'::jsonb
);

create index if not exists kb_chunks_embedding_idx
on public.kb_chunks
using hnsw (embedding vector_cosine_ops)
with (m = 16, ef_construction = 64);

create index if not exists kb_chunks_metadata_idx
on public.kb_chunks using gin (metadata);

create or replace function public.match_documents(
    query_embedding vector(1536),
    match_count integer default 5,
    match_threshold double precision default 0.0
)
returns table (
    id uuid,
    content text,
    metadata jsonb,
    similarity double precision
)
language sql
stable
as $$
    select
        kb_chunks.id,
        kb_chunks.content,
        kb_chunks.metadata,
        1 - (kb_chunks.embedding <=> query_embedding) as similarity
    from public.kb_chunks
    where 1 - (kb_chunks.embedding <=> query_embedding) >= match_threshold
    order by kb_chunks.embedding <=> query_embedding
    limit match_count;
$$;
