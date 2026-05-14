-- Re-align match_documents RPC to the existing public.documents table.
-- This removes ambiguous overloads and keeps one active signature.

drop function if exists public.match_documents(public.vector, integer, double precision);
drop function if exists public.match_documents(public.vector, integer);

create or replace function public.match_documents(
    query_embedding public.vector(1536),
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
        d.id,
        d.content,
        d.metadata,
        1 - (d.embedding <=> query_embedding) as similarity
    from public.documents as d
    where 1 - (d.embedding <=> query_embedding) >= match_threshold
    order by d.embedding <=> query_embedding
    limit match_count;
$$;
