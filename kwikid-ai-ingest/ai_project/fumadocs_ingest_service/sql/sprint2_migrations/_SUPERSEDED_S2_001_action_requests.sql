-- S2_001_action_requests.sql
-- Sprint 2.1: Action Execution Foundation — Action Requests Table
--
-- ============================================================================
-- ARCHITECTURE
-- ============================================================================
-- The Action Gateway implements a Proposal/Validation/Execution split:
--
--   AI proposes → human validates (REVERSIBLE/IRREVERSIBLE) → executor runs
--
-- The AI NEVER writes directly to production systems. Every external action
-- passes through this table as the single source of truth for:
--   - What was proposed
--   - Who approved or rejected it
--   - What the outcome was
--   - How to roll it back (for REVERSIBLE actions)
--
-- Risk tiers (from 07_GOVERNANCE_POLICY_AND_HANDOFF.md):
--   SAFE         — Read-only or purely informational; auto-approved immediately.
--                  Examples: post_resolution_note, lookup_customer_status.
--   REVERSIBLE   — State-changing but compensable; human approval required.
--                  Examples: close_ticket, trigger_otp_resend, reset_vkyc_session.
--   IRREVERSIBLE — Financial, legal, or permanently mutating; strict approval,
--                  single execution attempt, no auto-retry.
--                  Examples: initiate_refund, mark_vkyc_complete, flag_account.
--
-- ============================================================================
-- STATE MACHINE
-- ============================================================================
-- PROPOSED ─┬─→ AWAITING_APPROVAL ─┬─→ APPROVED ─→ EXECUTING ─┬─→ EXECUTED
--            │    (REVERSIBLE/       │   (all)                   ├─→ FAILED
--            │     IRREVERSIBLE)     ├─→ REJECTED                └─→ TIMED_OUT
--            │                       └─→ EXPIRED
--            └─→ APPROVED                          EXECUTED ─→ ROLLING_BACK ─┬─→ ROLLED_BACK
--                (SAFE, auto)                                                  └─→ ROLLBACK_FAILED
--
-- Terminal states: REJECTED, EXPIRED, ROLLED_BACK, ROLLBACK_FAILED
-- Pseudo-terminal: EXECUTED (REVERSIBLE can transition to ROLLING_BACK)
-- Retry path: FAILED / TIMED_OUT → APPROVED (if attempt < max_attempts)
--
-- ============================================================================
-- IDEMPOTENCY
-- ============================================================================
-- idempotency_key = SHA-256(case_id + action_type + action_namespace +
--                            canonical_json(action_params))
-- The UNIQUE constraint on idempotency_key prevents duplicate proposals for
-- the same intended action within the same case. Computed by the caller
-- (ActionGateway.propose()) before INSERT.
--
-- For execution-level deduplication (target system), the executor uses
-- action_id (UUID) as the idempotency header when calling external APIs.
--
-- ============================================================================
-- DESIGN DECISIONS
-- ============================================================================
-- 1. case_id FK uses ON DELETE RESTRICT — a case with pending/active actions
--    cannot be deleted. Action records are compliance artefacts.
-- 2. rollback_action_id is a self-referential FK to the compensating request.
--    ON DELETE RESTRICT — you cannot delete a rollback action that a primary
--    action points to.
-- 3. action_params and rollback_params MUST NOT contain unmasked PII.
--    The caller is responsible for masking before INSERT (same policy as
--    case_audit_log.action_detail).
-- 4. max_attempts for IRREVERSIBLE is enforced at 1 by the gateway service.
--    The DB allows up to 10; the service layer adds the business constraint.
-- 5. executor_id tracks which worker owns the EXECUTING action. Used by the
--    stuck-execution watchdog (Sprint 2.2) to detect zombie executions.
--
-- Prerequisites: S1_001_cases.sql (cases table with UUID case_id)
-- Apply via Supabase SQL editor.
-- Rollback: DROP TABLE IF EXISTS action_requests CASCADE;

-- ── Table ──────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS action_requests (

    -- ── Identity ──────────────────────────────────────────────────────────────
    action_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id             UUID        NOT NULL
                            REFERENCES cases (case_id) ON DELETE RESTRICT,
    ticket_id           TEXT        NOT NULL,   -- denormalized for direct FD lookup
    client              TEXT        NOT NULL,   -- tenant slug

    -- ── Action classification ─────────────────────────────────────────────────
    action_type         TEXT        NOT NULL,   -- e.g. 'POST_RESOLUTION_NOTE', 'INITIATE_REFUND'
    action_namespace    TEXT        NOT NULL,   -- e.g. 'freshdesk', 'payment_gateway', 'kyc_platform'
    risk_level          TEXT        NOT NULL,   -- 'SAFE' | 'REVERSIBLE' | 'IRREVERSIBLE'

    -- ── State machine ─────────────────────────────────────────────────────────
    current_state       TEXT        NOT NULL DEFAULT 'PROPOSED',

    -- ── Proposal metadata ─────────────────────────────────────────────────────
    proposed_by         TEXT        NOT NULL DEFAULT 'system',
                                               -- 'system' | 'human:<agent_id>'
    proposed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Action parameters ─────────────────────────────────────────────────────
    -- Sanitized input to the target system. MUST NOT contain unmasked PII.
    -- The gateway service is responsible for masking before storage.
    action_params       JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- ── Compensation parameters (REVERSIBLE actions only) ─────────────────────
    -- Stored at proposal time. If the action is executed and later needs to be
    -- undone, these params drive the compensating action_request.
    -- NULL for SAFE (no rollback needed) and IRREVERSIBLE (no safe rollback).
    rollback_action_type TEXT,
    rollback_params      JSONB,

    -- ── Human approval ────────────────────────────────────────────────────────
    approval_required   BOOLEAN     NOT NULL DEFAULT TRUE,
    approval_deadline   TIMESTAMPTZ,           -- NULL for SAFE; set by gateway on proposal
    approved_by         TEXT,                  -- agent_id | 'system' (auto-approval for SAFE)
    approval_decision_at TIMESTAMPTZ,          -- when approved_by made the decision
    approval_notes      TEXT,                  -- agent's free-text reasoning for SOC trail

    -- ── Execution tracking ────────────────────────────────────────────────────
    executor_id         TEXT,                  -- worker ID holding the EXECUTING lock
    execution_started_at  TIMESTAMPTZ,
    execution_completed_at TIMESTAMPTZ,
    -- execution_attempt starts at 0; incremented before each execution attempt
    execution_attempt   SMALLINT    NOT NULL DEFAULT 0,
    max_attempts        SMALLINT    NOT NULL DEFAULT 3,

    -- ── Execution result ──────────────────────────────────────────────────────
    -- Sanitized response from the target system (no raw API responses with PII).
    execution_result    JSONB,
    failure_code        TEXT,                  -- machine-readable code for routing decisions
    failure_reason      TEXT,                  -- human-readable description for agents

    -- ── Idempotency ───────────────────────────────────────────────────────────
    -- SHA-256 hex of (case_id + action_type + action_namespace + canonical params).
    -- Uniqueness is global — case_id is already globally unique (UUID), so
    -- the hash namespace is naturally tenant-scoped via the case.
    idempotency_key     TEXT        NOT NULL,

    -- ── Rollback linkage ──────────────────────────────────────────────────────
    -- Set to TRUE when a compensating action has been EXECUTED for this request.
    is_rolled_back      BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Points to the action_request that compensates this one.
    -- Self-referential FK: rollback action cannot be deleted while primary exists.
    rollback_action_id  UUID        REFERENCES action_requests (action_id) ON DELETE RESTRICT,

    -- ── Timestamps ────────────────────────────────────────────────────────────
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Constraints ───────────────────────────────────────────────────────────
    CONSTRAINT ar_risk_level_check CHECK (
        risk_level IN ('SAFE', 'REVERSIBLE', 'IRREVERSIBLE')
    ),

    CONSTRAINT ar_state_check CHECK (
        current_state IN (
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

    CONSTRAINT ar_max_attempts_range CHECK (
        max_attempts BETWEEN 1 AND 10
    ),

    CONSTRAINT ar_attempt_lte_max CHECK (
        execution_attempt <= max_attempts
    ),

    CONSTRAINT ar_attempt_nonneg CHECK (
        execution_attempt >= 0
    ),

    -- Idempotency: one proposal per intended action (global — case_id in hash)
    CONSTRAINT ar_idempotency_unique UNIQUE (idempotency_key),

    -- Rollback consistency: only REVERSIBLE actions may have rollback params
    -- (enforced at service layer; DB constraint omitted for flexibility in
    --  testing and schema evolution — add via ALTER TABLE when stable)

    -- Rolled-back flag requires rollback_action_id to be set
    CONSTRAINT ar_rollback_linkage_check CHECK (
        (is_rolled_back = FALSE)
        OR (is_rolled_back = TRUE AND rollback_action_id IS NOT NULL)
    )
);

-- ── updated_at trigger ────────────────────────────────────────────────────────
-- Automatically keeps updated_at current on every UPDATE.
-- Required because action_requests IS mutable (state machine updates in-place).

CREATE OR REPLACE FUNCTION _ar_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ar_updated_at ON action_requests;
CREATE TRIGGER trg_ar_updated_at
    BEFORE UPDATE ON action_requests
    FOR EACH ROW
    EXECUTE FUNCTION _ar_set_updated_at();

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Per-case action history (primary lookup by the workflow engine)
CREATE INDEX IF NOT EXISTS idx_ar_case_id_time
    ON action_requests (case_id, created_at DESC);

-- Per-ticket lookup for Freshdesk webhook handlers
CREATE INDEX IF NOT EXISTS idx_ar_ticket_client
    ON action_requests (ticket_id, client);

-- Open-queue monitoring: actions awaiting human approval, by client
-- Partial: only rows in states that require human attention
CREATE INDEX IF NOT EXISTS idx_ar_awaiting_approval
    ON action_requests (client, approval_deadline ASC)
    WHERE current_state = 'AWAITING_APPROVAL';

-- Executor pickup queue: approved actions ready to execute, by risk level
-- (risk_level ordering: SAFE → REVERSIBLE → IRREVERSIBLE for priority)
CREATE INDEX IF NOT EXISTS idx_ar_approved_queue
    ON action_requests (client, risk_level, proposed_at ASC)
    WHERE current_state = 'APPROVED';

-- Stuck-execution watchdog (Sprint 2.2 SLA monitor)
-- Finds actions that have been EXECUTING longer than the timeout window
CREATE INDEX IF NOT EXISTS idx_ar_executing
    ON action_requests (execution_started_at ASC)
    WHERE current_state = 'EXECUTING';

-- SLA breach monitoring: expired approval deadlines
CREATE INDEX IF NOT EXISTS idx_ar_approval_deadline
    ON action_requests (approval_deadline ASC)
    WHERE current_state = 'AWAITING_APPROVAL'
      AND approval_deadline IS NOT NULL;

-- Rollback eligibility: EXECUTED REVERSIBLE actions that haven't been rolled back
CREATE INDEX IF NOT EXISTS idx_ar_rollback_eligible
    ON action_requests (case_id, execution_completed_at DESC)
    WHERE current_state = 'EXECUTED'
      AND is_rolled_back = FALSE
      AND risk_level = 'REVERSIBLE';

-- Retry queue: failed/timed-out actions eligible for retry
CREATE INDEX IF NOT EXISTS idx_ar_retry_eligible
    ON action_requests (client, updated_at ASC)
    WHERE current_state IN ('FAILED', 'TIMED_OUT');

-- Per-client compliance reporting: all non-terminal actions
CREATE INDEX IF NOT EXISTS idx_ar_client_state
    ON action_requests (client, current_state, created_at DESC);

-- Global timeline for SOC reporting
CREATE INDEX IF NOT EXISTS idx_ar_created_at
    ON action_requests (created_at DESC);

-- ── Row-level security ────────────────────────────────────────────────────────
-- service_role bypasses RLS (full access for the AI ingest service).
-- Authenticated agents: SELECT only for their client's actions.
-- No direct INSERT/UPDATE/DELETE from authenticated role — all writes
-- must go through the service_role API (ActionGateway service methods).
-- Anon: deny all.

ALTER TABLE action_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ar_deny_anon ON action_requests;
CREATE POLICY ar_deny_anon
    ON action_requests
    AS RESTRICTIVE
    FOR ALL
    TO anon
    USING (false);

-- Authenticated agents may SELECT actions for their client.
-- The client claim path in the JWT must match your Supabase auth provider
-- configuration. Adjust the claim path if different.
DROP POLICY IF EXISTS ar_agent_select ON action_requests;
CREATE POLICY ar_agent_select
    ON action_requests
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

COMMENT ON TABLE action_requests IS
    'Sprint 2.1 Action Gateway: every external action proposed by the AI. '
    'Primary unit of the Proposal/Validation/Execution pattern. '
    'AI never writes directly to production systems — all writes pass through here.';

COMMENT ON COLUMN action_requests.action_id IS
    'Internal system UUID. Never exposed to end-users or target systems.';

COMMENT ON COLUMN action_requests.action_type IS
    'Logical action name. e.g. POST_RESOLUTION_NOTE, INITIATE_REFUND, CLOSE_TICKET. '
    'Not constrained by CHECK — new types added at service layer without schema migration.';

COMMENT ON COLUMN action_requests.action_namespace IS
    'Target system identifier. e.g. freshdesk, payment_gateway, kyc_platform, internal. '
    'Used by the executor to route to the correct integration adapter.';

COMMENT ON COLUMN action_requests.risk_level IS
    'SAFE: auto-approved read-only. REVERSIBLE: human approval + compensable. '
    'IRREVERSIBLE: strict approval, single attempt, no auto-retry.';

COMMENT ON COLUMN action_requests.action_params IS
    'Sanitized input to the target system. MUST NOT contain unmasked PII. '
    'Caller is responsible for masking (same policy as case_audit_log.action_detail).';

COMMENT ON COLUMN action_requests.rollback_action_type IS
    'Compensating action type for REVERSIBLE actions. Stored at proposal time '
    'so rollback can proceed without re-deriving the compensation logic.';

COMMENT ON COLUMN action_requests.rollback_params IS
    'Parameters for the compensating action. NULL for SAFE and IRREVERSIBLE. '
    'Computed from the original action_params at proposal time.';

COMMENT ON COLUMN action_requests.approval_deadline IS
    'SLA deadline for human approval decision. NULL for SAFE (auto-approved). '
    'REVERSIBLE: NOW() + 4h. IRREVERSIBLE: NOW() + 24h. Monitored by SLA watchdog.';

COMMENT ON COLUMN action_requests.executor_id IS
    'Worker/process ID that acquired the EXECUTING lock. Used by stuck-execution '
    'watchdog to identify zombie executions (Sprint 2.2).';

COMMENT ON COLUMN action_requests.execution_attempt IS
    'Starts at 0; incremented before each execution attempt. When execution_attempt '
    'reaches max_attempts, FAILED becomes terminal (no further retry).';

COMMENT ON COLUMN action_requests.max_attempts IS
    'Default 3 for SAFE/REVERSIBLE; enforced at 1 for IRREVERSIBLE by service layer. '
    'DB allows 1–10; business constraint is in ActionGateway.propose().';

COMMENT ON COLUMN action_requests.idempotency_key IS
    'SHA-256 hex of (case_id + action_type + action_namespace + canonical_json(action_params)). '
    'Prevents duplicate proposals. For execution dedup against target systems, '
    'the executor uses action_id (UUID) as the external idempotency header.';

COMMENT ON COLUMN action_requests.rollback_action_id IS
    'UUID of the compensating action_request. Set when a rollback action is proposed '
    'for this primary action. Self-referential FK prevents accidental deletion.';

COMMENT ON COLUMN action_requests.is_rolled_back IS
    'Set to TRUE when a compensating action has been EXECUTED for this request. '
    'Only valid for REVERSIBLE risk_level. Enforced by rollback_linkage_check constraint.';
