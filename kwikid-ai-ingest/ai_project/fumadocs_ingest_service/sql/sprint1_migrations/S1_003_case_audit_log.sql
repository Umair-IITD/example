-- S1_003_case_audit_log.sql
-- Sprint 1: Create case_audit_log table.
--
-- Append-only audit log for ALL case events:
--   STATE_TRANSITION, ACTION_PROPOSED, ACTION_EXECUTED, ACTION_REJECTED,
--   ESCALATION_TRIGGERED, NOTE_POSTED, RAG_CALLED, SLOT_FILLED,
--   CLASSIFICATION, ERROR
--
-- Schema follows 07_GOVERNANCE_POLICY_AND_HANDOFF.md §6.
--
-- This is NOT the same as case_transitions (which is state changes only).
-- case_audit_log captures ALL events — retrieval, notes, errors, etc.
--
-- DESIGN DECISION: case_id is UUID with NO FK constraint.
-- Rationale: the audit log must outlive cases. If a case is deleted for
-- DPDP/GDPR compliance, its audit trail must be preserved for SOC review.
-- A FK with ON DELETE CASCADE would destroy the audit record. A FK with
-- ON DELETE SET NULL would lose the case linkage. UUID with no FK preserves
-- both the audit record and the (now-orphaned) case reference for reconstruction.
--
-- Apply after S1_002_case_transitions.sql.
-- Rollback: DROP TABLE IF EXISTS case_audit_log;

CREATE TABLE IF NOT EXISTS case_audit_log (
    audit_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         UUID        NOT NULL,    -- intentionally NO FK (audit outlives cases)
    ticket_id       TEXT        NOT NULL,
    client          TEXT        NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor           TEXT        NOT NULL DEFAULT 'system',
    action_type     TEXT        NOT NULL,    -- AuditEventType enum value
    action_detail   JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- sanitized event params (no PII)
    outcome         TEXT,
    error_code      TEXT,
    idempotency_key TEXT,                   -- SHA-256 key for action execution dedup

    CONSTRAINT case_audit_action_type_check CHECK (
        action_type IN (
            'STATE_TRANSITION', 'ACTION_PROPOSED', 'ACTION_EXECUTED', 'ACTION_REJECTED',
            'ESCALATION_TRIGGERED', 'NOTE_POSTED', 'RAG_CALLED', 'SLOT_FILLED',
            'CLASSIFICATION', 'ERROR'
        )
    )
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Per-case timeline: "show me all audit events for case X, newest first"
CREATE INDEX IF NOT EXISTS idx_case_audit_case_id_time
    ON case_audit_log (case_id, event_timestamp DESC);

-- Ticket-level audit (for agents looking up by Freshdesk ticket ID)
CREATE INDEX IF NOT EXISTS idx_case_audit_ticket_id
    ON case_audit_log (ticket_id, event_timestamp DESC);

-- Per-client compliance reporting
CREATE INDEX IF NOT EXISTS idx_case_audit_client_time
    ON case_audit_log (client, event_timestamp DESC);

-- Event-type filtering (e.g., "all ESCALATION_TRIGGERED events this week")
CREATE INDEX IF NOT EXISTS idx_case_audit_action_time
    ON case_audit_log (action_type, event_timestamp DESC);

-- Time-range queries across all events
CREATE INDEX IF NOT EXISTS idx_case_audit_timestamp
    ON case_audit_log (event_timestamp DESC);

-- Idempotency key dedup lookup
CREATE INDEX IF NOT EXISTS idx_case_audit_idempotency
    ON case_audit_log (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ── Append-only enforcement ───────────────────────────────────────────────────

CREATE RULE no_update_case_audit AS
    ON UPDATE TO case_audit_log DO INSTEAD NOTHING;

CREATE RULE no_delete_case_audit AS
    ON DELETE TO case_audit_log DO INSTEAD NOTHING;

-- ── Comments ──────────────────────────────────────────────────────────────────

COMMENT ON TABLE  case_audit_log IS 'Append-only audit log for all case events. Schema per 07_GOVERNANCE_POLICY_AND_HANDOFF.md §6.';
COMMENT ON COLUMN case_audit_log.case_id         IS 'UUID reference to cases.case_id. NO FK — audit outlives cases (compliance requirement).';
COMMENT ON COLUMN case_audit_log.action_detail    IS 'JSONB event payload — must not contain unmasked PII. NOT NULL, defaults to empty object.';
COMMENT ON COLUMN case_audit_log.idempotency_key  IS 'SHA-256 key for action execution events (dedup guard). NULL for non-action events.';
