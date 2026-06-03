-- S2_001_action_gateway.sql
-- Sprint 2.1.5: Action Gateway Persistence — Definitive Schema
--
-- ============================================================================
-- SUPERSESSION NOTICE
-- ============================================================================
-- This file supersedes two previously-designed (but NEVER APPLIED) migrations:
--
--   S2_001_action_requests.sql   — designed Sprint 2.1; superseded here
--   S2_002_action_transition_log.sql — designed Sprint 2.1; superseded here
--
-- DO NOT apply S2_001_action_requests.sql or S2_002_action_transition_log.sql.
-- Apply THIS FILE ONLY.
--
-- OPERATOR ACTION REQUIRED: Rename the superseded files to prevent confusion:
--   mv S2_001_action_requests.sql    _SUPERSEDED_S2_001_action_requests.sql
--   mv S2_002_action_transition_log.sql _SUPERSEDED_S2_002_action_transition_log.sql
--
-- ============================================================================
-- ARCHITECTURE: TWO TABLES
-- ============================================================================
--
-- TABLE 1: action_gateway
--   Authoritative source of truth for every action proposed by the AI system.
--   Mutable: state machine updates in-place. updated_at trigger tracks changes.
--   This table IS the action's complete operational state — not just metadata.
--
-- TABLE 2: action_gateway_transitions
--   Immutable, append-only audit trail of every state change.
--   One row per transition event. RULES enforce append-only at DB level.
--   Survives the parent action's lifetime.
--
-- ============================================================================
-- PROPOSAL / VALIDATION / EXECUTION SPLIT
-- ============================================================================
--
--   AI proposes → human validates (REVERSIBLE / IRREVERSIBLE) → executor runs
--
-- The AI NEVER writes directly to production systems. Every external action
-- passes through action_gateway as the single source of truth.
--
-- Risk tiers:
--   SAFE         — Read-only or purely informational. Auto-approved immediately.
--                  max_attempts = 3. No approval required.
--   REVERSIBLE   — State-changing but compensable. Human approval required.
--                  expires_at = NOW() + 4h. Rollback path stored at proposal time.
--   IRREVERSIBLE — Financial, legal, or permanently mutating. Strict approval.
--                  expires_at = NOW() + 24h. max_attempts = 1. No auto-retry.
--
-- ============================================================================
-- STATE MACHINE (matches ActionState enum in case_engine/action_state.py)
-- ============================================================================
--
--  PROPOSED ──┬──→ AWAITING_APPROVAL ──┬──→ APPROVED ──→ EXECUTING ──┬──→ EXECUTED
--             │    (REVERSIBLE /        │    (all)                    ├──→ FAILED
--             │     IRREVERSIBLE)       ├──→ REJECTED (terminal)      └──→ TIMED_OUT
--             │                         └──→ EXPIRED  (terminal)
--             └──→ APPROVED                                    │
--                  (SAFE, auto)                                ↓
--                                              EXECUTED → ROLLING_BACK ──┬──→ ROLLED_BACK     (terminal)
--                                                                         └──→ ROLLBACK_FAILED (terminal)
--
--  Retry path: FAILED / TIMED_OUT → APPROVED (if execution_attempt < max_attempts)
--  Terminal states: REJECTED, EXPIRED, ROLLED_BACK, ROLLBACK_FAILED
--
-- ============================================================================
-- IDEMPOTENCY DESIGN
-- ============================================================================
--
-- idempotency_key = SHA-256(case_id | action_type | action_namespace |
--                            canonical_json(action_payload))
--
-- Computed by ActionGateway.propose() before INSERT. The UNIQUE constraint
-- on idempotency_key is the DB-level backstop against race conditions:
-- even if two concurrent proposals pass the Python duplicate check, only one
-- INSERT will succeed; the second will raise a UniqueViolation that the
-- ActionRepository converts to DuplicateActionError.
--
-- ============================================================================
-- ROLLBACK DESIGN
-- ============================================================================
--
-- rollback_action_id is a self-referential FK (RESTRICT). Rationale:
--   - action_gateway is a LIVE operational table; rows are never deleted.
--   - ON DELETE RESTRICT is effectively inert in normal operations.
--   - The FK guarantees rollback_action_id always resolves to a real action.
--   - Insertion order in propose_rollback(): compensation INSERT first,
--     then original row UPDATE — FK is satisfied at every point.
--
-- ============================================================================
-- PYTHON MODEL ALIGNMENT REQUIREMENT (Sprint 2.1.5 follow-up)
-- ============================================================================
--
-- The ActionRequest.to_db_row() method currently produces column names that
-- DO NOT match this schema. The following renames are required in the Python
-- model before connecting the gateway to this table:
--
--   Python field name          →  DB column name (this schema)
--   ─────────────────────────────────────────────────────────
--   action_params              →  action_payload
--   approved_by                →  approver
--   approval_decision_at       →  approved_at
--   approval_deadline          →  expires_at
--
-- New columns with no Python equivalent (set by gateway methods, not propose()):
--   rejected_at                (set by reject())
--   execution_failed_at        (set by record_failure(), record_timeout())
--   rollback_completed_at      (set by record_rollback_success())
--
-- ============================================================================
-- PREREQUISITES
-- ============================================================================
-- S1_001_cases.sql must be applied first (provides cases.case_id UUID PK).
-- Python ActionRequest model must be updated to use new column names.
--
-- Apply via Supabase SQL editor (paste entire file, run as one transaction).
-- Rollback: DROP TABLE IF EXISTS action_gateway_transitions;
--           DROP TABLE IF EXISTS action_gateway;

-- ═══════════════════════════════════════════════════════════════════════════════
-- TABLE: action_gateway
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS action_gateway (

    -- ── Identity ──────────────────────────────────────────────────────────────
    action_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK to cases: case must exist before an action can be proposed.
    -- ON DELETE RESTRICT: a case with pending or active actions cannot be closed.
    case_id             UUID        NOT NULL
                            REFERENCES cases (case_id) ON DELETE RESTRICT,

    -- Denormalized from case for direct lookups without joining cases.
    ticket_id           TEXT        NOT NULL,   -- Freshdesk ticket number
    client              TEXT        NOT NULL,   -- tenant slug (e.g. 'unity_bank')

    -- ── Action classification ─────────────────────────────────────────────────
    -- Logical action name registered in the action registry.
    -- e.g. 'POST_RESOLUTION_NOTE', 'INITIATE_REFUND', 'CLOSE_TICKET'.
    -- Not constrained by CHECK: new action types added at service layer.
    action_type         TEXT        NOT NULL,

    -- Target system identifier. Routes to the correct integration adapter.
    -- e.g. 'freshdesk', 'payment_gateway', 'kyc_platform', 'internal'.
    -- Required: executor cannot dispatch without knowing the target namespace.
    action_namespace    TEXT        NOT NULL,

    -- Risk tier. Determines approval path, SLA deadline, and max_attempts.
    risk_level          TEXT        NOT NULL,

    -- ── State machine ─────────────────────────────────────────────────────────
    current_state       TEXT        NOT NULL DEFAULT 'PROPOSED',

    -- ── Payload (PII-sanitized) ───────────────────────────────────────────────
    -- Input to the target system. MUST NOT contain unmasked PII.
    -- The gateway service masks all PII before storage (same policy as
    -- case_audit_log.action_detail). Callers are responsible for masking.
    action_payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- ── Compensation spec (REVERSIBLE actions only) ───────────────────────────
    -- Stored at proposal time. If the action is executed and later needs to be
    -- rolled back, these drive the compensating action without re-deriving logic.
    -- NULL for SAFE (no rollback) and IRREVERSIBLE (no safe compensation path).
    rollback_action_type TEXT,                  -- compensation action_type
    rollback_params      JSONB,                 -- compensation action_payload

    -- ── Proposal metadata ─────────────────────────────────────────────────────
    -- Who initiated this proposal. 'system' for AI-generated; 'human:<id>' for
    -- agent-initiated proposals (manual override path, Sprint 2.2).
    proposed_by         TEXT        NOT NULL DEFAULT 'system',

    -- When the proposal was submitted. Distinct from created_at: creation may
    -- be delayed by queue processing; proposed_at is the user-facing timestamp
    -- for SLA measurement.
    proposed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Human approval ────────────────────────────────────────────────────────
    approval_required   BOOLEAN     NOT NULL DEFAULT TRUE,

    -- When this action expires if not acted upon. Serves two roles:
    --   AWAITING_APPROVAL: approval SLA deadline (4h REVERSIBLE, 24h IRREVERSIBLE)
    --   APPROVED: executor pickup deadline (executor must begin before this)
    -- NULL for SAFE actions (auto-approved, no approval window needed).
    expires_at          TIMESTAMPTZ,

    -- Agent who approved or auto-approved this action. Set by approve() or
    -- by propose() for SAFE actions (approver = 'auto_approval').
    approver            TEXT,

    -- When the approval decision was recorded.
    approved_at         TIMESTAMPTZ,

    -- When the rejection decision was recorded. Mutually exclusive with approved_at.
    rejected_at         TIMESTAMPTZ,

    -- Agent's free-text justification. SOC audit trail value.
    approval_notes      TEXT,

    -- ── Execution tracking ────────────────────────────────────────────────────
    -- Worker/process ID that acquired the EXECUTING lock. Used by the
    -- stuck-execution watchdog (Sprint 2.2) to identify zombie executions.
    executor_id         TEXT,

    execution_started_at    TIMESTAMPTZ,
    execution_completed_at  TIMESTAMPTZ,

    -- When execution entered the FAILED or TIMED_OUT state.
    -- Redundant with transitions table but enables single-row compliance queries.
    execution_failed_at     TIMESTAMPTZ,

    -- Starts at 0. Incremented before each execution attempt in begin_execution().
    -- When execution_attempt reaches max_attempts, FAILED becomes terminal.
    execution_attempt   SMALLINT    NOT NULL DEFAULT 0,

    -- Default 3 for SAFE/REVERSIBLE; service layer enforces 1 for IRREVERSIBLE.
    -- DB allows 1–10; business constraint lives in ActionGateway.propose().
    max_attempts        SMALLINT    NOT NULL DEFAULT 3,

    -- Sanitized response from the target system on success. No raw API responses,
    -- no PII. The executor is responsible for sanitizing before storage.
    execution_result    JSONB,

    -- ── Failure details ───────────────────────────────────────────────────────
    -- Machine-readable code for automated routing: 'NETWORK_TIMEOUT',
    -- 'TARGET_SYSTEM_REJECT', 'AUTHORIZATION_FAILED', etc.
    failure_code        TEXT,

    -- Human-readable description for agent dashboard and SOC review.
    failure_reason      TEXT,

    -- ── Rollback linkage ──────────────────────────────────────────────────────
    -- UUID of the compensating action_gateway row. Self-referential FK.
    -- Set by propose_rollback() after the compensation action is inserted.
    -- ON DELETE RESTRICT: compensation action cannot be deleted while this row
    -- references it. Safe because action rows are never deleted in this platform.
    rollback_action_id  UUID        REFERENCES action_gateway (action_id)
                                        ON DELETE RESTRICT,

    -- When the rollback compensation action completed successfully.
    -- Set by record_rollback_success(). Redundant with transitions table
    -- but enables direct is-rollback-done queries on the main table.
    rollback_completed_at   TIMESTAMPTZ,

    -- Explicit rollback flag. True when compensation has been EXECUTED.
    -- Faster to query than deriving from rollback_completed_at IS NOT NULL.
    is_rolled_back      BOOLEAN     NOT NULL DEFAULT FALSE,

    -- ── Idempotency ───────────────────────────────────────────────────────────
    -- SHA-256 hex of (case_id + action_type + action_namespace +
    --                 canonical_json(action_payload)).
    -- DB UNIQUE constraint is the backstop against concurrent duplicate proposals.
    idempotency_key     TEXT        NOT NULL,

    -- ── Timestamps ────────────────────────────────────────────────────────────
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ────────────────────────────────────────────────────────────────────────
    -- CHECK CONSTRAINTS
    -- ────────────────────────────────────────────────────────────────────────

    CONSTRAINT ag_risk_level_check CHECK (
        risk_level IN ('SAFE', 'REVERSIBLE', 'IRREVERSIBLE')
    ),

    CONSTRAINT ag_state_check CHECK (
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

    -- Retry ceiling: 1–10 attempts. Service layer enforces 1 for IRREVERSIBLE.
    CONSTRAINT ag_max_attempts_range CHECK (
        max_attempts BETWEEN 1 AND 10
    ),

    -- Attempt counter must not exceed the ceiling.
    CONSTRAINT ag_attempt_lte_max CHECK (
        execution_attempt <= max_attempts
    ),

    -- Attempt counter must be non-negative.
    CONSTRAINT ag_attempt_nonneg CHECK (
        execution_attempt >= 0
    ),

    -- One proposal per logical action (case_id is in the hash, so this is
    -- implicitly per-case-per-action-type-per-params).
    CONSTRAINT ag_idempotency_unique UNIQUE (idempotency_key),

    -- An action cannot be both approved and rejected.
    CONSTRAINT ag_approval_decision_mutex CHECK (
        NOT (approved_at IS NOT NULL AND rejected_at IS NOT NULL)
    ),

    -- Rollback flag requires a linked rollback action.
    CONSTRAINT ag_rollback_flag_integrity CHECK (
        is_rolled_back = FALSE
        OR (is_rolled_back = TRUE AND rollback_action_id IS NOT NULL)
    ),

    -- Rollback timestamp requires a linked rollback action.
    CONSTRAINT ag_rollback_timestamp_integrity CHECK (
        rollback_completed_at IS NULL
        OR rollback_action_id IS NOT NULL
    ),

    -- IRREVERSIBLE actions must not carry rollback specs — there is no safe
    -- compensation path for irreversible operations.
    CONSTRAINT ag_irreversible_no_rollback CHECK (
        risk_level != 'IRREVERSIBLE'
        OR rollback_action_type IS NULL
    )
);

-- ── updated_at trigger ────────────────────────────────────────────────────────
-- Keeps updated_at current on every UPDATE. Required because action_gateway
-- is mutable (state machine updates in-place). Named distinctly to avoid
-- collision with the superseded _ar_set_updated_at function.

CREATE OR REPLACE FUNCTION _ag_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ag_updated_at ON action_gateway;
CREATE TRIGGER trg_ag_updated_at
    BEFORE UPDATE ON action_gateway
    FOR EACH ROW
    EXECUTE FUNCTION _ag_set_updated_at();

-- ── Indexes: action_gateway ───────────────────────────────────────────────────
--
-- Index design principle: partial indexes wherever the WHERE clause matches
-- a high-selectivity condition (e.g. specific state values), because
-- production queues are dominated by terminal-state rows over time.
-- A full-table index on (client, current_state) would scan mostly terminal
-- rows for any active-state query — partial indexes avoid that cost.

-- [IDX-1] Per-case action history.
-- Primary lookup for the workflow engine: "show all actions for case X".
-- DESC order: most recent actions surface first.
CREATE INDEX IF NOT EXISTS idx_ag_case_id_time
    ON action_gateway (case_id, created_at DESC);

-- [IDX-2] Per-ticket lookup for Freshdesk webhook handlers.
-- When a new webhook arrives for TKT-001, we need the active action quickly.
CREATE INDEX IF NOT EXISTS idx_ag_ticket_client
    ON action_gateway (ticket_id, client);

-- [IDX-3] Pending approval queue — SLA watchdog primary index.
-- Query: "all AWAITING_APPROVAL actions for client X, ordered by SLA deadline".
-- Partial: only scans the small subset of rows awaiting human decision.
-- expires_at ASC: earliest-expiring (most urgent) actions first.
CREATE INDEX IF NOT EXISTS idx_ag_pending_approval
    ON action_gateway (client, expires_at ASC)
    WHERE current_state = 'AWAITING_APPROVAL';

-- [IDX-4] Executor pickup queue — approved actions ready for execution.
-- Query: "next APPROVED action to execute for client X" (FIFO within risk tier).
-- risk_level included: SAFE actions can be deprioritised behind REVERSIBLE
-- (which have human approval invested) in the executor's scheduling logic.
-- created_at ASC: FIFO ordering within each (client, risk_level) bucket.
-- Partial: only scans the active APPROVED subset.
CREATE INDEX IF NOT EXISTS idx_ag_approved_queue
    ON action_gateway (client, risk_level, created_at ASC)
    WHERE current_state = 'APPROVED';

-- [IDX-5] Stuck-execution watchdog — finds zombie executions.
-- Query: "all EXECUTING actions where execution_started_at < NOW() - timeout".
-- Sprint 2.2: watchdog scans this index periodically.
-- execution_started_at ASC: longest-running executions surface first.
-- Partial: only scans the very small EXECUTING subset.
CREATE INDEX IF NOT EXISTS idx_ag_executing
    ON action_gateway (execution_started_at ASC)
    WHERE current_state = 'EXECUTING';

-- [IDX-6] Global expiry sweep — unified SLA deadline scan.
-- Query: "all actions past their expires_at in AWAITING_APPROVAL or APPROVED".
-- Used by the SLA watchdog to expire overdue actions in a single scan.
-- expires_at ASC: most-overdue actions first.
CREATE INDEX IF NOT EXISTS idx_ag_expires_at_sweep
    ON action_gateway (expires_at ASC)
    WHERE expires_at IS NOT NULL
      AND current_state IN ('AWAITING_APPROVAL', 'APPROVED');

-- [IDX-7] Rollback eligibility — EXECUTED REVERSIBLE actions not yet rolled back.
-- Query: "find the rollback candidate for case X".
-- execution_completed_at DESC: most-recently-executed candidates first.
-- Partial: this subset is very small; most actions are terminal, not EXECUTED.
CREATE INDEX IF NOT EXISTS idx_ag_rollback_eligible
    ON action_gateway (case_id, execution_completed_at DESC)
    WHERE current_state = 'EXECUTED'
      AND risk_level = 'REVERSIBLE'
      AND rollback_action_id IS NULL;

-- [IDX-8] Per-client compliance reporting.
-- Query: "all actions for client X in state Y, most-recent first".
-- Used for the agent dashboard and SOC time-window reports.
CREATE INDEX IF NOT EXISTS idx_ag_client_state_time
    ON action_gateway (client, current_state, created_at DESC);

-- [IDX-9] Rollback action lookup.
-- Query: "given rollback_action_id, find the original action".
-- Sparse partial: only rows with a linked compensation action.
CREATE INDEX IF NOT EXISTS idx_ag_rollback_action_id
    ON action_gateway (rollback_action_id)
    WHERE rollback_action_id IS NOT NULL;

-- [IDX-10] Global SOC timeline.
-- Query: "all actions across all clients in the last 30 days".
-- Used by compliance dashboards and cross-client SOC audits.
CREATE INDEX IF NOT EXISTS idx_ag_created_at
    ON action_gateway (created_at DESC);

-- NOTE: idempotency_key is already indexed by the UNIQUE constraint.
-- No separate index needed.

-- ── Row-level security: action_gateway ───────────────────────────────────────
-- service_role: bypasses RLS — full access for the AI ingest service.
-- authenticated: SELECT only for their own client's actions.
-- All writes (INSERT/UPDATE) must go through the service_role API path
-- (ActionGateway service methods). No direct writes from the authenticated role.
-- anon: deny all.

ALTER TABLE action_gateway ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ag_deny_anon ON action_gateway;
CREATE POLICY ag_deny_anon
    ON action_gateway
    AS RESTRICTIVE
    FOR ALL
    TO anon
    USING (false);

DROP POLICY IF EXISTS ag_agent_select ON action_gateway;
CREATE POLICY ag_agent_select
    ON action_gateway
    AS PERMISSIVE
    FOR SELECT
    TO authenticated
    USING (
        client = COALESCE(
            auth.jwt() -> 'user_metadata' ->> 'client',
            auth.jwt() -> 'app_metadata' ->> 'client'
        )
    );

-- ── Column comments: action_gateway ──────────────────────────────────────────

COMMENT ON TABLE action_gateway IS
    'Sprint 2.1.5 Action Gateway persistence. Every action proposed by the AI '
    'system. Authoritative operational state — not just metadata. '
    'Supersedes action_requests (S2_001) and action_transition_log (S2_002).';

COMMENT ON COLUMN action_gateway.action_payload IS
    'Sanitized input to the target system. MUST NOT contain unmasked PII. '
    'Caller is responsible for masking before INSERT. '
    'Equivalent to action_params in the pre-Sprint-2.1.5 Python model.';

COMMENT ON COLUMN action_gateway.action_namespace IS
    'Target system identifier. Routes to the correct integration adapter. '
    'e.g. freshdesk, payment_gateway, kyc_platform, internal. '
    'Required by executor — cannot dispatch without knowing the target system.';

COMMENT ON COLUMN action_gateway.expires_at IS
    'Unified deadline column. AWAITING_APPROVAL: approval SLA deadline. '
    'APPROVED: executor pickup deadline. NULL for SAFE (auto-approved). '
    'Monitored by the SLA watchdog (Sprint 2.2). '
    'Equivalent to approval_deadline in the pre-Sprint-2.1.5 Python model.';

COMMENT ON COLUMN action_gateway.approver IS
    'Agent who made the approval decision, or ''auto_approval'' for SAFE actions. '
    'Equivalent to approved_by in the pre-Sprint-2.1.5 Python model.';

COMMENT ON COLUMN action_gateway.approved_at IS
    'Timestamp of the approval decision. '
    'Equivalent to approval_decision_at in the pre-Sprint-2.1.5 Python model.';

COMMENT ON COLUMN action_gateway.rejected_at IS
    'Explicit rejection timestamp. Redundant with transitions table but enables '
    'single-row compliance queries without JOIN. Mutually exclusive with approved_at.';

COMMENT ON COLUMN action_gateway.execution_failed_at IS
    'When execution entered FAILED or TIMED_OUT state. Redundant with transitions '
    'table but enables time-to-failure metrics directly from this table.';

COMMENT ON COLUMN action_gateway.executor_id IS
    'Worker or process ID that acquired the EXECUTING lock. Used by the '
    'stuck-execution watchdog (Sprint 2.2) to detect zombie executions.';

COMMENT ON COLUMN action_gateway.execution_attempt IS
    'Starts at 0. Incremented before each attempt in begin_execution(). '
    'When execution_attempt reaches max_attempts, FAILED becomes terminal '
    '(no further retry is scheduled by record_failure()).';

COMMENT ON COLUMN action_gateway.max_attempts IS
    'Default 3 for SAFE/REVERSIBLE; gateway enforces 1 for IRREVERSIBLE. '
    'DB allows 1–10; the IRREVERSIBLE=1 constraint lives in ActionGateway.propose().';

COMMENT ON COLUMN action_gateway.rollback_action_type IS
    'Compensation action type for REVERSIBLE actions. Stored at proposal time '
    'so propose_rollback() can create the compensating action without re-deriving '
    'the compensation logic from scratch.';

COMMENT ON COLUMN action_gateway.rollback_action_id IS
    'Self-referential FK to the compensating action_gateway row. '
    'Set by propose_rollback() after the compensation action is inserted. '
    'ON DELETE RESTRICT: compensation cannot be deleted while primary references it.';

COMMENT ON COLUMN action_gateway.rollback_completed_at IS
    'When the rollback compensation completed successfully. '
    'Redundant with transitions table; enables single-row rollback-done queries.';

COMMENT ON COLUMN action_gateway.idempotency_key IS
    'SHA-256 hex of (case_id + action_type + action_namespace + '
    'canonical_json(action_payload)). UNIQUE constraint is the DB-level backstop '
    'against concurrent duplicate proposals that race past the Python duplicate check.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- TABLE: action_gateway_transitions
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- Immutable, append-only audit trail of every state transition on every action.
-- One row per transition event. Never updated or deleted.
--
-- DESIGN: FK on action_id (deviation from Sprint 1 no-FK audit philosophy)
-- ──────────────────────────────────────────────────────────────────────────
-- Sprint 1 audit tables (case_audit_log, case_transitions) used NO FK on
-- case_id to allow audit records to outlive their parent case.
--
-- action_gateway_transitions USES a FK. Rationale:
--   1. action_gateway rows are NEVER deleted in this platform (they reach
--      terminal states and remain as compliance artefacts). ON DELETE RESTRICT
--      is effectively inert — it will never be triggered in normal operations.
--   2. The FK provides a DB-level guarantee that every transition record
--      references a real action. This prevents a class of orphaned-record bugs
--      during bulk imports or data corrections.
--   3. If a future migration introduces archival/deletion of old actions,
--      the FK must be dropped first in that migration (one ALTER TABLE step).
--
-- APPEND-ONLY ENFORCEMENT
-- ──────────────────────────────────────────────────────────────────────────
-- PostgreSQL RULES intercept and discard UPDATE and DELETE statements.
-- Provides tamper-evident audit at the DB level, not just the application level.
--
-- FUTURE MIGRATION NOTE: If a data-correction migration must UPDATE rows here,
-- it MUST: DROP RULE → UPDATE → CREATE RULE within one atomic DO $$ block.
-- See S1_006 Step 8 for the reference implementation of this pattern.

CREATE TABLE IF NOT EXISTS action_gateway_transitions (

    -- ── Identity ──────────────────────────────────────────────────────────────
    transition_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK to action_gateway. ON DELETE RESTRICT because action rows are never
    -- deleted. See design note above re deviation from Sprint 1 philosophy.
    action_id       UUID        NOT NULL
                        REFERENCES action_gateway (action_id) ON DELETE RESTRICT,

    -- Denormalized from action_gateway for efficient per-case and per-client
    -- queries without joining. Same pattern as case_audit_log and case_transitions.
    case_id         UUID        NOT NULL,
    ticket_id       TEXT        NOT NULL,
    client          TEXT        NOT NULL,

    -- ── Transition ────────────────────────────────────────────────────────────
    from_state      TEXT        NOT NULL,
    to_state        TEXT        NOT NULL,

    -- Who triggered this transition:
    --   'system'                — automated gateway / watchdog
    --   'auto_approval'         — gateway auto-approved a SAFE action
    --   'human:<agent_id>'      — human agent approved / rejected
    --   'executor:<worker_id>'  — execution worker reporting result
    --   'watchdog:sla'          — SLA expiry watchdog (Sprint 2.2)
    --   'watchdog:stuck_execution' — stuck-execution watchdog (Sprint 2.2)
    actor           TEXT        NOT NULL DEFAULT 'system',

    -- Machine-readable reason string. e.g. 'auto_approved_safe',
    -- 'human_approved', 'execution_failed:NETWORK_TIMEOUT', 'retry_scheduled'.
    reason          TEXT,

    -- Structured context for this transition. Sanitized — no PII.
    -- Examples:
    --   PROPOSED→AWAITING_APPROVAL: {"risk_level": "REVERSIBLE", "expires_at": "..."}
    --   EXECUTING→FAILED: {"failure_code": "TIMEOUT", "execution_attempt": 2}
    --   EXECUTED→ROLLING_BACK: {"rollback_action_id": "..."}
    detail          JSONB       NOT NULL DEFAULT '{}'::jsonb,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Constraints ───────────────────────────────────────────────────────────
    CONSTRAINT agt_from_state_check CHECK (
        from_state IN (
            'PROPOSED', 'AWAITING_APPROVAL', 'APPROVED',
            'REJECTED', 'EXPIRED',
            'EXECUTING', 'EXECUTED', 'FAILED', 'TIMED_OUT',
            'ROLLING_BACK', 'ROLLED_BACK', 'ROLLBACK_FAILED'
        )
    ),

    CONSTRAINT agt_to_state_check CHECK (
        to_state IN (
            'PROPOSED', 'AWAITING_APPROVAL', 'APPROVED',
            'REJECTED', 'EXPIRED',
            'EXECUTING', 'EXECUTED', 'FAILED', 'TIMED_OUT',
            'ROLLING_BACK', 'ROLLED_BACK', 'ROLLBACK_FAILED'
        )
    )
);

-- ── Append-only enforcement ───────────────────────────────────────────────────
-- RULES silently swallow UPDATE and DELETE statements at the DB level.
-- Application code must INSERT only. Any UPDATE/DELETE is discarded silently.

CREATE RULE no_update_action_gateway_transitions AS
    ON UPDATE TO action_gateway_transitions DO INSTEAD NOTHING;

CREATE RULE no_delete_action_gateway_transitions AS
    ON DELETE TO action_gateway_transitions DO INSTEAD NOTHING;

-- ── Indexes: action_gateway_transitions ──────────────────────────────────────

-- [IDX-T1] Per-action timeline — primary access pattern.
-- "Show the complete state history of action X in chronological order."
-- Used by the SOC investigation UI and executor state queries.
-- ASC order: chronological for timeline rendering.
CREATE INDEX IF NOT EXISTS idx_agt_action_id_time
    ON action_gateway_transitions (action_id, created_at ASC);

-- [IDX-T2] Per-case action transition history.
-- "Show all transitions across all actions for case X, most recent first."
-- Used by the case workflow dashboard.
CREATE INDEX IF NOT EXISTS idx_agt_case_id_time
    ON action_gateway_transitions (case_id, created_at DESC);

-- [IDX-T3] Per-client compliance reporting.
-- "All transitions for client X in the last 30 days."
-- Used by SOC time-window audit reports.
CREATE INDEX IF NOT EXISTS idx_agt_client_time
    ON action_gateway_transitions (client, created_at DESC);

-- [IDX-T4] State arrival queries.
-- "All actions that entered FAILED state this week."
-- Used by failure rate dashboards and SLA breach detection.
CREATE INDEX IF NOT EXISTS idx_agt_to_state_time
    ON action_gateway_transitions (to_state, created_at DESC);

-- [IDX-T5] Global audit timeline.
-- "All transitions across all clients in a time window."
-- Used by the cross-client SOC audit interface.
CREATE INDEX IF NOT EXISTS idx_agt_created_at
    ON action_gateway_transitions (created_at DESC);

-- ── Row-level security: action_gateway_transitions ───────────────────────────
-- Same policy as action_gateway: service_role full access; authenticated agents
-- SELECT only for their client; anon denied.

ALTER TABLE action_gateway_transitions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agt_deny_anon ON action_gateway_transitions;
CREATE POLICY agt_deny_anon
    ON action_gateway_transitions
    AS RESTRICTIVE
    FOR ALL
    TO anon
    USING (false);

DROP POLICY IF EXISTS agt_agent_select ON action_gateway_transitions;
CREATE POLICY agt_agent_select
    ON action_gateway_transitions
    AS PERMISSIVE
    FOR SELECT
    TO authenticated
    USING (
        client = COALESCE(
            auth.jwt() -> 'user_metadata' ->> 'client',
            auth.jwt() -> 'app_metadata' ->> 'client'
        )
    );

-- ── Column comments: action_gateway_transitions ───────────────────────────────

COMMENT ON TABLE action_gateway_transitions IS
    'Sprint 2.1.5: Immutable audit trail of every action_gateway state transition. '
    'Append-only (RULE-enforced). FK on action_id (deviation from Sprint 1 no-FK '
    'philosophy — justified because action_gateway rows are never deleted).';

COMMENT ON COLUMN action_gateway_transitions.action_id IS
    'FK to action_gateway.action_id. ON DELETE RESTRICT — action rows are never '
    'deleted, so this constraint is effectively inert in normal operations. '
    'Provides DB-level guarantee that every transition references a real action.';

COMMENT ON COLUMN action_gateway_transitions.actor IS
    'Who triggered this transition. Format: '
    '''system'' | ''auto_approval'' | ''human:<agent_id>'' | '
    '''executor:<worker_id>'' | ''watchdog:sla'' | ''watchdog:stuck_execution''.';

COMMENT ON COLUMN action_gateway_transitions.detail IS
    'Structured context for this transition. Must not contain unmasked PII. '
    'Sanitized before INSERT by the ActionGateway service.';


-- ═══════════════════════════════════════════════════════════════════════════════
-- VERIFICATION QUERIES
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- Run after applying this migration. All queries should return results
-- consistent with a freshly-created empty schema.
--
-- V1: Confirm both tables exist
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public'
--   AND table_name IN ('action_gateway', 'action_gateway_transitions')
-- ORDER BY table_name;
-- Expected: 2 rows
--
-- V2: Confirm action_gateway column count
-- SELECT COUNT(*) FROM information_schema.columns
-- WHERE table_schema = 'public' AND table_name = 'action_gateway';
-- Expected: 34
--
-- V3: Confirm idempotency_key uniqueness constraint
-- SELECT constraint_name, constraint_type
-- FROM information_schema.table_constraints
-- WHERE table_name = 'action_gateway'
--   AND constraint_type = 'UNIQUE';
-- Expected: ag_idempotency_unique
--
-- V4: Confirm self-referential FK on rollback_action_id
-- SELECT kcu.column_name, ccu.table_name AS foreign_table
-- FROM information_schema.key_column_usage kcu
-- JOIN information_schema.referential_constraints rc
--   ON kcu.constraint_name = rc.constraint_name
-- JOIN information_schema.constraint_column_usage ccu
--   ON rc.unique_constraint_name = ccu.constraint_name
-- WHERE kcu.table_name = 'action_gateway'
--   AND kcu.column_name = 'rollback_action_id';
-- Expected: foreign_table = 'action_gateway'
--
-- V5: Confirm append-only RULES on transitions table
-- SELECT rulename FROM pg_rules
-- WHERE tablename = 'action_gateway_transitions'
-- ORDER BY rulename;
-- Expected: no_delete_action_gateway_transitions, no_update_action_gateway_transitions
--
-- V6: Confirm CHECK constraints
-- SELECT constraint_name FROM information_schema.table_constraints
-- WHERE table_name = 'action_gateway'
--   AND constraint_type = 'CHECK'
-- ORDER BY constraint_name;
-- Expected: ag_approval_decision_mutex, ag_attempt_lte_max, ag_attempt_nonneg,
--           ag_irreversible_no_rollback, ag_max_attempts_range,
--           ag_risk_level_check, ag_rollback_flag_integrity,
--           ag_rollback_timestamp_integrity, ag_state_check
--
-- V7: Confirm index count on action_gateway (10 indexes + 1 from UNIQUE = 11)
-- SELECT COUNT(*) FROM pg_indexes
-- WHERE tablename = 'action_gateway';
-- Expected: 11
--
-- V8: Confirm RLS enabled on both tables
-- SELECT relname, relrowsecurity FROM pg_class
-- WHERE relname IN ('action_gateway', 'action_gateway_transitions');
-- Expected: relrowsecurity = true for both
