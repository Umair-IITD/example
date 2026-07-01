-- S2_031_schema_fix.sql
-- Sprint 2.31: Fix support_conversation_state table schema drift.
--
-- ROOT CAUSE:
--   The table was originally created by sql/support_conversation_state.sql
--   (old n8n/Telegram schema) with:
--       id uuid primary key, channel text, external_id text, ...
--   No ticket_id, no client_id, no lifecycle_state, no created_at column.
--
--   S2_028 CREATE TABLE IF NOT EXISTS silently no-oped (table already existed).
--   S2_030 ADD COLUMN IF NOT EXISTS added lifecycle_state, client_id, etc.
--   but MISSED adding ticket_id and created_at — the two columns that caused:
--       "column support_conversation_state.ticket_id does not exist"
--       "created_at column missing"
--
-- WHAT THIS MIGRATION DOES:
--   1. Rename the broken table to support_conversation_state_legacy_v0.
--      (Preserves any existing n8n session data; does not delete it.)
--   2. Create a new support_conversation_state with the canonical Sprint 2.28
--      schema (ticket_id TEXT PRIMARY KEY, created_at, all Sprint 2.28 columns).
--   3. Recreate all Sprint 2.28 indexes on the new table.
--
-- SAFE TO RUN IF:
--   - S2_030 was previously applied (legacy columns may exist — no problem).
--   - S2_028 was previously applied (table existed — no problem).
--   - Neither was applied (raw n8n table — no problem).
--
-- NOT SAFE TO RE-RUN:
--   This migration is NOT idempotent.  Running it twice will fail at RENAME
--   (target name already exists).  Gate with:
--       DO $$ BEGIN
--           IF NOT EXISTS (SELECT 1 FROM information_schema.columns
--                         WHERE table_name='support_conversation_state'
--                           AND column_name='ticket_id') THEN
--               -- run migration
--           END IF;
--       END $$;
--   OR run manually once and verify before applying to production.
--
-- APPLY VIA: Supabase Dashboard → SQL Editor → paste and execute.

-- ── Step 1: Rename existing (broken) table to _legacy_v0 ─────────────────────

ALTER TABLE support_conversation_state
    RENAME TO support_conversation_state_legacy_v0;

-- ── Step 2: Create new support_conversation_state with correct schema ─────────

CREATE TABLE support_conversation_state (
    ticket_id               TEXT        PRIMARY KEY,
    client_id               TEXT        NOT NULL DEFAULT '',
    lifecycle_state         TEXT        NOT NULL DEFAULT 'OPEN',
    clarification_pending   BOOLEAN     NOT NULL DEFAULT FALSE,
    awaiting_customer       BOOLEAN     NOT NULL DEFAULT FALSE,
    awaiting_human_approval BOOLEAN     NOT NULL DEFAULT FALSE,
    clarification_count     INTEGER     NOT NULL DEFAULT 0,
    case_id                 TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at             TIMESTAMPTZ,
    metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT scs_lifecycle_state_check
        CHECK (lifecycle_state IN ('OPEN', 'PENDING', 'CLARIFICATION', 'RESOLVED', 'CLOSED', 'ESCALATED')),
    CONSTRAINT scs_clarification_count_non_negative
        CHECK (clarification_count >= 0)
);

COMMENT ON TABLE support_conversation_state IS
    'Per-ticket conversation lifecycle state for Freshdesk webhook processing. '
    'Survives restarts. Updated in place on every state transition. '
    'Sprint 2.28.1 schema. Replaces old n8n session state table (now in _legacy_v0).';

-- ── Step 3: Create indexes on new table ──────────────────────────────────────

CREATE INDEX idx_scs_client_id
    ON support_conversation_state(client_id);

CREATE INDEX idx_scs_lifecycle_state
    ON support_conversation_state(lifecycle_state);

CREATE INDEX idx_scs_clarification_pending
    ON support_conversation_state(clarification_pending)
    WHERE clarification_pending = TRUE;

CREATE INDEX idx_scs_updated_at
    ON support_conversation_state(updated_at DESC);

-- ── Step 4: Enable RLS (service_role bypasses RLS — same as legacy table) ────

ALTER TABLE support_conversation_state ENABLE ROW LEVEL SECURITY;

-- ── Step 5: Verify ────────────────────────────────────────────────────────────

SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'support_conversation_state'
ORDER BY ordinal_position;
