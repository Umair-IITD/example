-- S2_002_action_transition_log.sql
-- Sprint 2.1: Action Execution Foundation — Action Transition Log
--
-- ============================================================================
-- PURPOSE
-- ============================================================================
-- Immutable, append-only audit trail of every state transition on every
-- action_request. One row per transition event. Never updated or deleted.
--
-- This table is the compliance record for the Action Gateway. SOC and
-- compliance auditors use it to reconstruct the complete lifecycle of any
-- action, including who approved it, when it was executed, and whether it
-- was rolled back.
--
-- ============================================================================
-- DESIGN: NO FK on action_id (intentional — same rationale as case_audit_log)
-- ============================================================================
-- action_id is UUID NOT NULL with NO FOREIGN KEY constraint.
--
-- Rationale: In a regulated banking environment, action transition records
-- must outlive their parent action_request for the following reasons:
--   1. DPDP Act compliance: action_requests may be anonymised or archived
--      to cold storage after a retention period. The transition log must
--      remain in hot storage for SOC review.
--   2. Forensic reconstruction: if an action_request is ever modified or
--      deleted (e.g., data correction under court order), the transition log
--      provides the original state history.
--   3. Consistency with case_audit_log: both audit tables follow the same
--      design pattern (no FK) for architectural uniformity.
--
-- The alternative designs and why they were rejected:
--   ON DELETE CASCADE: destroys the audit trail when action is deleted — unacceptable.
--   ON DELETE SET NULL: loses the action linkage needed for reconstruction.
--   ON DELETE RESTRICT: prevents deletion of action_requests, which is desirable
--     but creates a dependency cycle (action_requests already has RESTRICT FKs).
--
-- ============================================================================
-- APPEND-ONLY ENFORCEMENT
-- ============================================================================
-- PostgreSQL RULES are used to intercept and discard any UPDATE or DELETE
-- statement on this table. This provides a tamper-evident audit trail at the
-- database level — not just at the application level.
--
-- NOTE FOR FUTURE MIGRATIONS: Any migration that needs to modify rows in this
-- table MUST first DROP the RULE, perform the modification, then re-CREATE
-- the RULE — within the same atomic DO block (same pattern as S1_006 Step 8).
--
-- Prerequisites: S2_001_action_requests.sql (action_requests table)
-- Apply via Supabase SQL editor.
-- Rollback: DROP TABLE IF EXISTS action_transition_log;

-- ── Table ──────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS action_transition_log (

    -- ── Identity ──────────────────────────────────────────────────────────────
    log_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Intentionally NO FK — audit log must outlive action_requests.
    -- See design note above.
    action_id       UUID        NOT NULL,

    -- Denormalized for efficient per-case and per-ticket queries without joins.
    case_id         UUID        NOT NULL,
    ticket_id       TEXT        NOT NULL,
    client          TEXT        NOT NULL,

    -- ── Transition ────────────────────────────────────────────────────────────
    from_state      TEXT        NOT NULL,
    to_state        TEXT        NOT NULL,

    -- Actor who triggered this transition:
    --   'system'               — automated gateway / watchdog
    --   'auto_approval'        — gateway auto-approved a SAFE action
    --   'human:<agent_id>'     — human agent approved/rejected
    --   'executor:<worker_id>' — execution worker reporting result
    actor           TEXT        NOT NULL DEFAULT 'system',
    reason          TEXT,                      -- machine-readable reason string

    -- Structured context for this transition (sanitized — no PII).
    -- Examples:
    --   PROPOSED→AWAITING_APPROVAL: {"approval_deadline": "..."}
    --   EXECUTING→FAILED: {"failure_code": "TIMEOUT", "attempt": 2}
    --   EXECUTED→ROLLING_BACK: {"rollback_action_id": "..."}
    detail          JSONB       NOT NULL DEFAULT '{}'::jsonb,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Constraints ───────────────────────────────────────────────────────────
    CONSTRAINT atl_from_state_check CHECK (
        from_state IN (
            'PROPOSED',
            'AWAITING_APPROVAL',
            'APPROVED',
            'REJECTED',
            'EXPIRED',
            'EXECUTING',
            'EXECUTED',
            'FAILED',
            'TIMED_OUT',
            'ROLLING_BACK',
            'ROLLED_BACK',
            'ROLLBACK_FAILED'
        )
    ),

    CONSTRAINT atl_to_state_check CHECK (
        to_state IN (
            'PROPOSED',
            'AWAITING_APPROVAL',
            'APPROVED',
            'REJECTED',
            'EXPIRED',
            'EXECUTING',
            'EXECUTED',
            'FAILED',
            'TIMED_OUT',
            'ROLLING_BACK',
            'ROLLED_BACK',
            'ROLLBACK_FAILED'
        )
    )
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Per-action timeline: "show full lifecycle of action X, in order"
-- Primary access pattern for SOC investigation and executor state queries
CREATE INDEX IF NOT EXISTS idx_atl_action_id_time
    ON action_transition_log (action_id, created_at ASC);

-- Per-case action history: all transitions across all actions for a case
CREATE INDEX IF NOT EXISTS idx_atl_case_id_time
    ON action_transition_log (case_id, created_at DESC);

-- Per-client compliance reporting (SOC time-window queries)
CREATE INDEX IF NOT EXISTS idx_atl_client_time
    ON action_transition_log (client, created_at DESC);

-- State arrival queries: "all actions that entered FAILED state this week"
CREATE INDEX IF NOT EXISTS idx_atl_to_state_time
    ON action_transition_log (to_state, created_at DESC);

-- Global timeline for full cross-client SOC audit
CREATE INDEX IF NOT EXISTS idx_atl_created_at
    ON action_transition_log (created_at DESC);

-- ── Append-only enforcement ───────────────────────────────────────────────────
-- UPDATE and DELETE silently no-op at the database level.
-- Application code must use INSERT only; any UPDATE/DELETE is eaten here.
--
-- IMPORTANT: If a data-fix migration needs to UPDATE rows in this table,
-- it MUST: DROP RULE → UPDATE → CREATE RULE, within one atomic DO block.
-- See S1_006 Step 8 for the reference implementation of this pattern.

CREATE RULE no_update_action_transition_log AS
    ON UPDATE TO action_transition_log DO INSTEAD NOTHING;

CREATE RULE no_delete_action_transition_log AS
    ON DELETE TO action_transition_log DO INSTEAD NOTHING;

-- ── Row-level security ────────────────────────────────────────────────────────
-- Same policy as action_requests: service_role has full access;
-- authenticated agents can SELECT for their client; anon denied.

ALTER TABLE action_transition_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS atl_deny_anon ON action_transition_log;
CREATE POLICY atl_deny_anon
    ON action_transition_log
    AS RESTRICTIVE
    FOR ALL
    TO anon
    USING (false);

DROP POLICY IF EXISTS atl_agent_select ON action_transition_log;
CREATE POLICY atl_agent_select
    ON action_transition_log
    AS PERMISSIVE
    FOR SELECT
    TO authenticated
    USING (
        client = (
            COALESCE(
                auth.jwt() -> 'user_metadata' ->> 'client',
                auth.jwt() -> 'app_metadata' ->> 'client'
            )
        )
    );

-- ── Comments ──────────────────────────────────────────────────────────────────

COMMENT ON TABLE action_transition_log IS
    'Sprint 2.1 Action Gateway: immutable audit trail of every action state '
    'transition. Append-only (RULE-enforced). NO FK on action_id — log outlives '
    'parent action_request for DPDP/SOC compliance.';

COMMENT ON COLUMN action_transition_log.action_id IS
    'UUID reference to action_requests.action_id. NO FK — audit log must survive '
    'action archival or deletion. Same design as case_audit_log.case_id.';

COMMENT ON COLUMN action_transition_log.actor IS
    'Who triggered this transition. Format: ''system'' | ''auto_approval'' | '
    '''human:<agent_id>'' | ''executor:<worker_id>'' | ''watchdog:sla''.';

COMMENT ON COLUMN action_transition_log.detail IS
    'Structured context for this transition. Must not contain unmasked PII. '
    'Sanitized before INSERT by the ActionGateway service.';
