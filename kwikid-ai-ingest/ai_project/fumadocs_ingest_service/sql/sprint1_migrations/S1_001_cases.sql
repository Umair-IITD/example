-- S1_001_cases.sql
-- Sprint 1: Create cases table.
--
-- This is the primary Case/Ticket record — the unit of work for Phase 2.
-- cases.ticket_id + cases.client is unique: one active case per ticket per tenant.
--
-- CANONICAL TYPE POLICY (Sprint 1.2 — schema rectification):
--   Internal system IDs (case_id, transition_id, audit_id) → UUID
--   External identifiers (ticket_id, client, actor)        → TEXT
--   Timestamps                                              → TIMESTAMPTZ
--
-- Apply in Supabase SQL editor.
-- Rollback: DROP TABLE IF EXISTS cases CASCADE;

CREATE TABLE IF NOT EXISTS cases (
    case_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id     TEXT        NOT NULL,
    client        TEXT        NOT NULL,
    topic         TEXT,                      -- classified topic key (NULL until classified)
    confidence    FLOAT,                     -- classifier confidence 0.0–1.0 (NULL until classified)
    current_state TEXT        NOT NULL DEFAULT 'NEW',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at     TIMESTAMPTZ,               -- set when current_state = CLOSED
    sla_breach_at TIMESTAMPTZ,               -- SLA deadline set by Level 2 SLA watchdog

    CONSTRAINT cases_ticket_client_unique UNIQUE (ticket_id, client),
    CONSTRAINT cases_state_check CHECK (
        current_state IN (
            'NEW', 'CLASSIFYING', 'TRIAGE_COMPLETE',
            'AWAITING_INPUT', 'WORKFLOW_ACTIVE', 'ACTION_PENDING',
            'ESCALATED', 'RESOLVED', 'FAILED', 'CLOSED'
        )
    )
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Webhook retry lookup: get existing case for (ticket_id, client)
CREATE INDEX IF NOT EXISTS idx_cases_ticket_client
    ON cases (ticket_id, client);

-- Open-queue monitoring per client+state (Level 1 dashboard, Level 2 queue watcher)
CREATE INDEX IF NOT EXISTS idx_cases_client_state
    ON cases (client, current_state)
    WHERE closed_at IS NULL;

-- Escalation queue: open cases per state
CREATE INDEX IF NOT EXISTS idx_cases_current_state
    ON cases (current_state)
    WHERE closed_at IS NULL;

-- Age-based queries and dashboard ordering
CREATE INDEX IF NOT EXISTS idx_cases_created_at
    ON cases (created_at DESC);

-- SLA breach monitoring (Sprint 2 SLA watchdog)
CREATE INDEX IF NOT EXISTS idx_cases_sla_breach
    ON cases (sla_breach_at)
    WHERE sla_breach_at IS NOT NULL;

-- ── Comments ──────────────────────────────────────────────────────────────────

COMMENT ON TABLE  cases IS 'Phase 2 Case/Ticket records — one per Freshdesk ticket per tenant. PRIMARY UNIT OF WORK.';
COMMENT ON COLUMN cases.case_id       IS 'Internal system UUID. Never exposed to end-users.';
COMMENT ON COLUMN cases.ticket_id     IS 'Freshdesk ticket identifier (external, TEXT, may be numeric string).';
COMMENT ON COLUMN cases.client        IS 'Tenant slug (e.g., unity_bank, axis_bank).';
COMMENT ON COLUMN cases.current_state IS 'CaseState enum value. Transitions logged in case_transitions.';
COMMENT ON COLUMN cases.topic         IS 'TopicKey from classifier (NULL before CLASSIFYING state).';
COMMENT ON COLUMN cases.confidence    IS 'Classifier confidence 0.0–1.0 (NULL before CLASSIFYING state).';
COMMENT ON COLUMN cases.sla_breach_at IS 'SLA deadline. NULL until Level 2 SLA watchdog sets it (Sprint 2).';
