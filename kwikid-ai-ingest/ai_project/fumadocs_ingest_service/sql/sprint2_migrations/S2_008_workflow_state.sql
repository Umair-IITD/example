-- S2_008_workflow_state.sql
-- Sprint 2.16: Workflow orchestration state columns for the cases table.
--
-- Adds:
--   workflow_id          TEXT         — which playbook is/was executing
--   workflow_state       TEXT         — WorkflowState value (PENDING/RUNNING/PAUSED/COMPLETED/ESCALATED/FAILED)
--   workflow_step_index  INTEGER      — current step index (informational; 0-based)
--   workflow_context     JSONB        — full WorkflowExecutionResult serialized
--
-- All columns are nullable — existing rows are unaffected.
-- Apply this migration before deploying Sprint 2.16 code.
--
-- Idempotent: IF NOT EXISTS guards prevent errors on repeated application.
--
BEGIN;

-- ── Columns ────────────────────────────────────────────────────────────────────

ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS workflow_id TEXT;

ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS workflow_state TEXT
    CONSTRAINT cases_workflow_state_check
        CHECK (workflow_state IN (
            'PENDING', 'RUNNING', 'PAUSED',
            'COMPLETED', 'ESCALATED', 'FAILED'
        ));

ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS workflow_step_index INTEGER;

ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS workflow_context JSONB;

-- ── Indexes ────────────────────────────────────────────────────────────────────

-- Partial index for active workflow monitoring
CREATE INDEX IF NOT EXISTS idx_cases_workflow_state
    ON cases (workflow_state)
    WHERE workflow_state IN ('RUNNING', 'PAUSED');

-- GIN index for JSONB workflow_context (for operational queries)
CREATE INDEX IF NOT EXISTS idx_cases_workflow_context
    ON cases USING GIN (workflow_context)
    WHERE workflow_context IS NOT NULL;

-- Lookup by workflow_id (operational summary queries)
CREATE INDEX IF NOT EXISTS idx_cases_workflow_id
    ON cases (workflow_id)
    WHERE workflow_id IS NOT NULL;

-- ── Column comments ────────────────────────────────────────────────────────────

COMMENT ON COLUMN cases.workflow_id IS
    'Playbook identifier (e.g., vkyc_session_failure_v1). Set when workflow starts.';

COMMENT ON COLUMN cases.workflow_state IS
    'WorkflowState: PENDING|RUNNING|PAUSED|COMPLETED|ESCALATED|FAILED. '
    'NULL means no workflow has been started for this case.';

COMMENT ON COLUMN cases.workflow_step_index IS
    'Informational: number of steps completed so far (0-based count of step_results).';

COMMENT ON COLUMN cases.workflow_context IS
    'Full WorkflowExecutionResult serialized as JSONB. '
    'Contains run_id, step_results, pending_action_id, resolution_note, etc.';

COMMIT;
