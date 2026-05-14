-- Chat conversation history for the dashboard chatbot.
-- Kept separate from vector `public.documents` so retrieval behavior is not polluted.
-- Access pattern: backend uses service_role (bypasses RLS). Frontend calls FastAPI /chat, not Supabase directly.

create table if not exists public.chat_messages (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null,
    role text not null check (role in ('user', 'assistant', 'system')),
    content text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists chat_messages_session_created_at_idx
    on public.chat_messages (session_id, created_at asc);

create index if not exists chat_messages_created_at_idx
    on public.chat_messages (created_at desc);

create index if not exists chat_messages_metadata_idx
    on public.chat_messages using gin (metadata);

comment on table public.chat_messages is
    'Chatbot conversation turns. Access via service_role from FastAPI backend; do not expose to anon role.';

-- RLS: deny by default; backend uses service_role which bypasses RLS.
alter table public.chat_messages enable row level security;

-- If you later need authenticated dashboard users to read their own threads,
-- add policies keyed on a user_id column scoped via auth.uid(). Do NOT read
-- identity from user_metadata / raw_user_meta_data for authorization.
