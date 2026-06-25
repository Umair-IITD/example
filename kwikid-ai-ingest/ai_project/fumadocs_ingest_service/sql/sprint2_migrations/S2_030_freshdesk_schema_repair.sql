-- S2_030_freshdesk_schema_repair.sql
-- Sprint 2.30: Freshdesk schema repair migration.
--
-- WHY THIS EXISTS:
--   S2_028_freshdesk_foundation.sql fails with:
--     ERROR: column "client_id" does not exist
--   Root cause: the `support_conversation_state` table was created by an earlier
--   mechanism (manual SQL or an older migration) WITHOUT the columns defined in
--   S2_028. `CREATE TABLE IF NOT EXISTS` silently skips creation (table exists),
--   but `CREATE INDEX ... ON support_conversation_state(client_id)` fails because
--   `client_id` is not a column in the existing table.
--
-- WHAT THIS MIGRATION DOES:
--   1. Adds ALL missing columns to the existing `support_conversation_state` table
--      using `ADD COLUMN IF NOT EXISTS` — safe to re-run; no-ops if columns exist.
--   2. Creates `freshdesk_webhook_events` from scratch (does not exist at all).
--   3. Creates all indexes with `IF NOT EXISTS` — safe to re-run.
--
-- APPLY VIA: Supabase Dashboard → SQL Editor → paste and execute.
-- SAFE TO RE-RUN: All statements use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

-- ── Step 1: Repair support_conversation_state ─────────────────────────────────
--
-- The table exists but may be missing Sprint 2.28 columns.
-- client_id is the confirmed missing column (from production error logs).
-- All other columns are added defensively.

ALTER TABLE support_conversation_state
    ADD COLUMN IF NOT EXISTS client_id               TEXT        NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lifecycle_state         TEXT        NOT NULL DEFAULT 'OPEN',
    ADD COLUMN IF NOT EXISTS clarification_pending   BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS awaiting_customer       BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS awaiting_human_approval BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS clarification_count     INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS case_id                 TEXT,
    ADD COLUMN IF NOT EXISTS resolved_at             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb;

-- Add lifecycle_state CHECK constraint if not already present.
-- Using DO block to avoid error on duplicate constraint name.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'support_conversation_state'
          AND constraint_name = 'scs_lifecycle_state_check'
    ) THEN
        ALTER TABLE support_conversation_state
            ADD CONSTRAINT scs_lifecycle_state_check
            CHECK (lifecycle_state IN ('OPEN', 'PENDING', 'CLARIFICATION', 'RESOLVED', 'CLOSED', 'ESCALATED'));
    END IF;
END $$;

-- Add clarification_count CHECK constraint if not already present.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'support_conversation_state'
          AND constraint_name = 'scs_clarification_count_non_negative'
    ) THEN
        ALTER TABLE support_conversation_state
            ADD CONSTRAINT scs_clarification_count_non_negative
            CHECK (clarification_count >= 0);
    END IF;
END $$;

-- Indexes on support_conversation_state
CREATE INDEX IF NOT EXISTS idx_scs_client_id
    ON support_conversation_state(client_id);

CREATE INDEX IF NOT EXISTS idx_scs_lifecycle_state
    ON support_conversation_state(lifecycle_state);

CREATE INDEX IF NOT EXISTS idx_scs_clarification_pending
    ON support_conversation_state(clarification_pending)
    WHERE clarification_pending = TRUE;

-- ── Step 2: Create freshdesk_webhook_events ───────────────────────────────────
--
-- This table does not exist. Creating from scratch.

CREATE TABLE IF NOT EXISTS freshdesk_webhook_events (
    idempotency_key     TEXT        PRIMARY KEY,
    ticket_id           TEXT        NOT NULL,
    event_type          TEXT        NOT NULL,
    event_timestamp     TIMESTAMPTZ NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_status   TEXT        NOT NULL DEFAULT 'RECEIVED',
    case_id             TEXT,
    error_detail        TEXT,

    CONSTRAINT fwe_processing_status_check
        CHECK (processing_status IN ('RECEIVED', 'PROCESSING', 'COMPLETED', 'FAILED'))
);

COMMENT ON TABLE freshdesk_webhook_events IS
    'Idempotency store for Freshdesk webhook events. One row per unique (ticket_id, event_type, event_timestamp) triple.';

CREATE INDEX IF NOT EXISTS idx_fwe_ticket_id
    ON freshdesk_webhook_events(ticket_id);

CREATE INDEX IF NOT EXISTS idx_fwe_received_at
    ON freshdesk_webhook_events(received_at);

CREATE INDEX IF NOT EXISTS idx_fwe_processing_status
    ON freshdesk_webhook_events(processing_status)
    WHERE processing_status IN ('RECEIVED', 'PROCESSING');

-- ── Step 3: Verify result ─────────────────────────────────────────────────────

SELECT
    table_name,
    'EXISTS' AS status
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('freshdesk_webhook_events', 'support_conversation_state')
ORDER BY table_name;

SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'support_conversation_state'
ORDER BY ordinal_position;
