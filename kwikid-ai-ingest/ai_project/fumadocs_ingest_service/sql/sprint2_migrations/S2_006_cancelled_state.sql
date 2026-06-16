-- S2_006_cancelled_state.sql
-- Sprint 2.14: Human Recovery & Operational Control
--
-- ============================================================================
-- WHAT THIS MIGRATION DOES
-- ============================================================================
--
-- 1. Adds CANCELLED to the ag_state_check CHECK constraint on action_gateway.
--
-- 2. Adds CANCELLED to agt_from_state_check and agt_to_state_check CHECK
--    constraints on action_gateway_transitions.
--
-- 3. Adds the cancelled_at column to action_gateway.
--
-- 4. Adds a partial index for cancelled actions.
--
-- 5. FIXES a latent bug in S2_003: the ae_event_type_check constraint on
--    audit_events was missing ACTION_DEAD_LETTERED and ACTION_AUDIT_READ
--    (added in AuditEventType in Sprint 2.11 but never included in the DB
--    constraint). This caused silent RULE-suppressed failures when writing
--    those event types via SupabaseAuditRepository. The constraint is
--    replaced here with the full correct list through Sprint 2.14.
--
-- ============================================================================
-- PREREQUISITES
-- ============================================================================
-- S2_001, S2_003, S2_004, S2_005 must be applied first.
-- Apply via Supabase SQL editor (paste entire file, run as one transaction).
--
-- ============================================================================
-- CANCELLED STATE SEMANTICS
-- ============================================================================
-- CANCELLED is a terminal state for actions that were voluntarily stopped
-- by an operator before execution began. It is semantically distinct from:
--   REJECTED  — a human declined to approve the action
--   EXPIRED   — the SLA deadline elapsed before the action was picked up
--
-- Valid source states (per action_state.py ALLOWED_ACTION_TRANSITIONS):
--   PROPOSED          → CANCELLED
--   AWAITING_APPROVAL → CANCELLED
--   APPROVED          → CANCELLED   (only before EXECUTING is claimed)
--
-- Not cancellable once EXECUTING has begun. The operator must wait for
-- the execution to complete and then trigger a rollback if needed.
-- ============================================================================

BEGIN;

-- ─── Step 1: Add cancelled_at column to action_gateway ───────────────────────

ALTER TABLE action_gateway
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

COMMENT ON COLUMN action_gateway.cancelled_at IS
    'Sprint 2.14: Set when an operator cancels the action before execution '
    'begins. NULL for all other states. Enables direct time-window queries '
    'without scanning transitions.';


-- ─── Step 2: Update ag_state_check to include CANCELLED ──────────────────────

ALTER TABLE action_gateway
    DROP CONSTRAINT IF EXISTS ag_state_check;

ALTER TABLE action_gateway
    ADD CONSTRAINT ag_state_check CHECK (
        current_state IN (
            'PROPOSED',
            'AWAITING_APPROVAL',
            'APPROVED',
            'REJECTED',
            'EXPIRED',
            'CANCELLED',
            'EXECUTING',
            'EXECUTED',
            'FAILED',
            'TIMED_OUT',
            'ROLLING_BACK',
            'ROLLED_BACK',
            'ROLLBACK_FAILED',
            'DEAD_LETTER'
        )
    );


-- ─── Step 3: Update transition table state constraints ───────────────────────

ALTER TABLE action_gateway_transitions
    DROP CONSTRAINT IF EXISTS agt_from_state_check;

ALTER TABLE action_gateway_transitions
    ADD CONSTRAINT agt_from_state_check CHECK (
        from_state IN (
            'PROPOSED', 'AWAITING_APPROVAL', 'APPROVED',
            'REJECTED', 'EXPIRED', 'CANCELLED',
            'EXECUTING', 'EXECUTED', 'FAILED', 'TIMED_OUT',
            'ROLLING_BACK', 'ROLLED_BACK', 'ROLLBACK_FAILED',
            'DEAD_LETTER'
        )
    );

ALTER TABLE action_gateway_transitions
    DROP CONSTRAINT IF EXISTS agt_to_state_check;

ALTER TABLE action_gateway_transitions
    ADD CONSTRAINT agt_to_state_check CHECK (
        to_state IN (
            'PROPOSED', 'AWAITING_APPROVAL', 'APPROVED',
            'REJECTED', 'EXPIRED', 'CANCELLED',
            'EXECUTING', 'EXECUTED', 'FAILED', 'TIMED_OUT',
            'ROLLING_BACK', 'ROLLED_BACK', 'ROLLBACK_FAILED',
            'DEAD_LETTER'
        )
    );


-- ─── Step 4: Add partial index for cancelled actions ─────────────────────────

CREATE INDEX IF NOT EXISTS idx_ag_cancelled
    ON action_gateway (client, cancelled_at ASC)
    WHERE current_state = 'CANCELLED';

COMMENT ON INDEX idx_ag_cancelled IS
    'Sprint 2.14: Per-client cancelled-action scan. Ordered by cancelled_at '
    'ASC for chronological review.';


-- ─── Step 5: Fix latent ae_event_type_check bug from S2_003 ──────────────────
-- ACTION_DEAD_LETTERED and ACTION_AUDIT_READ were added to AuditEventType
-- in Sprint 2.11 but never added to this DB constraint, causing silent
-- INSERT failures via SupabaseAuditRepository. Sprint 2.14 adds those plus
-- the four new recovery event types in one atomic replacement.

ALTER TABLE audit_events
    DROP CONSTRAINT IF EXISTS ae_event_type_check;

ALTER TABLE audit_events
    ADD CONSTRAINT ae_event_type_check CHECK (
        event_type IN (
            -- Original Sprint 2.8/2.9 types
            'ACTION_APPROVED',
            'ACTION_REJECTED',
            'ACTION_EXPIRED',
            'ACTION_EXECUTION_STARTED',
            'ACTION_EXECUTED',
            'ACTION_FAILED',
            'ACTION_ROLLED_BACK',
            'ACTION_ROLLBACK_FAILED',
            -- Sprint 2.11 types (were missing from DB constraint — bug fix)
            'ACTION_DEAD_LETTERED',
            'ACTION_AUDIT_READ',
            -- Sprint 2.14 recovery types
            'ACTION_CANCELLED',
            'ACTION_RECOVERED_FROM_DEAD_LETTER',
            'ACTION_MANUALLY_EXPIRED',
            'ACTION_ROLLBACK_TRIGGERED'
        )
    );

COMMENT ON CONSTRAINT ae_event_type_check ON audit_events IS
    'Sprint 2.14: Updated to include Sprint 2.11 types (ACTION_DEAD_LETTERED, '
    'ACTION_AUDIT_READ — latent bug fix) and Sprint 2.14 recovery event types. '
    'See AuditEventType in audit/models.py for the canonical list.';


COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- VERIFICATION QUERIES
-- ════════════════════════════════════════════════════════════════════════════
--
-- V1: Confirm cancelled_at column exists
-- SELECT column_name FROM information_schema.columns
-- WHERE table_name = 'action_gateway' AND column_name = 'cancelled_at';
-- Expected: 1 row
--
-- V2: Confirm CANCELLED in action_gateway state CHECK
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
-- WHERE conname = 'ag_state_check';
-- Expected: includes 'CANCELLED'
--
-- V3: Confirm CANCELLED in transitions from_state CHECK
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
-- WHERE conname = 'agt_from_state_check';
-- Expected: includes 'CANCELLED'
--
-- V4: Confirm audit_events constraint includes recovery types
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
-- WHERE conname = 'ae_event_type_check';
-- Expected: includes 'ACTION_CANCELLED', 'ACTION_DEAD_LETTERED'
--
-- V5: Confirm cancelled index exists
-- SELECT indexname FROM pg_indexes
-- WHERE tablename = 'action_gateway' AND indexname = 'idx_ag_cancelled';
-- Expected: 1 row
