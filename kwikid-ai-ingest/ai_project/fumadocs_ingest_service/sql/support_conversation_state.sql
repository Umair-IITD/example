-- Session state for multi-turn support (n8n / Freshdesk / Telegram).
-- Use from Supabase SQL editor. Do NOT store chat state in vector `documents`.

create table if not exists public.support_conversation_state (
    id uuid primary key default gen_random_uuid(),
    channel text not null check (channel in ('telegram', 'freshdesk')),
    external_id text not null,
    issue_key text,
    issue_category text,
    collected_fields jsonb not null default '{}'::jsonb,
    pending_questions jsonb not null default '[]'::jsonb,
    updated_at timestamptz not null default now(),
    unique (channel, external_id)
);

create index if not exists support_conversation_state_updated_at_idx
    on public.support_conversation_state (updated_at desc);

comment on table public.support_conversation_state is
    'Non-vector support automation state; accessed by n8n via service role (PostgREST).';

-- RLS: enable and deny by default; n8n should use service_role (bypasses RLS).
alter table public.support_conversation_state enable row level security;

-- Optional: allow authenticated dashboard users read-only (adjust to your roles).
-- create policy "service_full_access" on public.support_conversation_state
--   for all using (auth.role() = 'service_role');
