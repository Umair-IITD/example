-- LEGACY (optional): FastAPI no longer inserts or reads this table as of 2026-05.
-- Teach-the-AI stores card metadata on `public.documents` rows
-- (`metadata.author` = 'chat_train', `metadata.chunk_index` = 0 for the head).
-- You may skip this migration on new installs, or DROP TABLE public.knowledge_cards
-- after confirming nothing else references it.
--
-- Historical description: knowledge cards authored via the "Teach the AI" chat flow.

create table if not exists public.knowledge_cards (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null,
    title text,
    summary text,
    content text not null,
    tags text[] not null default '{}',
    tenant text,
    access_scope text,
    suggested_questions jsonb not null default '[]'::jsonb,
    status text not null default 'committed' check (status in ('draft', 'committed', 'archived')),
    chunk_ids uuid[] not null default '{}',
    created_by text not null default 'chat_train',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists knowledge_cards_created_at_idx
    on public.knowledge_cards (created_at desc);

create index if not exists knowledge_cards_tenant_access_idx
    on public.knowledge_cards (tenant, access_scope);

create index if not exists knowledge_cards_status_idx
    on public.knowledge_cards (status);

create index if not exists knowledge_cards_session_idx
    on public.knowledge_cards (session_id, created_at desc);

comment on table public.knowledge_cards is
    'Curated knowledge captured via the /train chat flow. Chunks live in public.documents (source_type=manual); this table keeps card-level metadata, ownership, and the committed chunk IDs for clean deletes.';

-- RLS: deny by default; backend uses service_role which bypasses RLS.
alter table public.knowledge_cards enable row level security;

-- If you later need authenticated dashboard users to read their own cards,
-- add policies keyed on a user_id column scoped via auth.uid(). Do NOT read
-- identity from user_metadata / raw_user_meta_data for authorization.
