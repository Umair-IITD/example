-- S2_028_freshdesk_foundation.sql
-- Sprint 2.28.1: Freshdesk Foundation Layer — idempotency and conversation state tables.
--
-- Table 1: freshdesk_webhook_events
--   Idempotency store: prevents duplicate processing of the same webhook event.
--   Primary key: {ticket_id}:{event_type}:{event_timestamp} — one row per unique event.
--
-- Table 2: support_conversation_state
--   Conversation lifecycle per ticket: survives service restarts (Supabase-backed).
--   Single row per ticket_id; updated in place as state transitions occur.
--
-- Run idempotently: all CREATE statements use IF NOT EXISTS.

-- ── freshdesk_webhook_events ──────────────────────────────────────────────────

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

-- ── support_conversation_state ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS support_conversation_state (
    ticket_id               TEXT        PRIMARY KEY,
    client_id               TEXT        NOT NULL,
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
    'Per-ticket conversation lifecycle state. Survives restarts. Updated in place on every state transition.';

CREATE INDEX IF NOT EXISTS idx_scs_client_id
    ON support_conversation_state(client_id);

CREATE INDEX IF NOT EXISTS idx_scs_lifecycle_state
    ON support_conversation_state(lifecycle_state);

CREATE INDEX IF NOT EXISTS idx_scs_clarification_pending
    ON support_conversation_state(clarification_pending)
    WHERE clarification_pending = TRUE;

-- ── Cleanup policy (advisory — enforce via cron job) ─────────────────────────
-- freshdesk_webhook_events: retain 30 days (COMPLETED), 7 days (FAILED).
-- support_conversation_state: retain indefinitely; archive via resolved_at > 90d.
