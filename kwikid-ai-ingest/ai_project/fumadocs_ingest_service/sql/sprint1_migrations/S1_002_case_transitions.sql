-- S1_002_case_transitions.sql
-- Sprint 1: Create case_transitions table.
--
-- Immutable log of every state transition. Never updated or deleted.
-- Provides full audit trail of case lifecycle for compliance reporting.
--
-- Apply after S1_001_cases.sql.
-- Rollback: DROP TABLE IF EXISTS case_transitions;

CREATE TABLE IF NOT EXISTS case_transitions (
    transition_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       UUID        NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    from_state    TEXT        NOT NULL,
    to_state      TEXT        NOT NULL,
    reason        TEXT,                      -- machine-readable reason string
    actor         TEXT        NOT NULL DEFAULT 'system',  -- 'system' | 'workflow_engine' | 'human:<agent_id>'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Case timeline queries: "show me all transitions for case X in order"
CREATE INDEX IF NOT EXISTS idx_case_transitions_case_id_time
    ON case_transitions (case_id, created_at DESC);

-- Operational: find all cases that reached a specific state
CREATE INDEX IF NOT EXISTS idx_case_transitions_to_state
    ON case_transitions (to_state, created_at DESC);

-- Audit: time-range queries across all transitions
CREATE INDEX IF NOT EXISTS idx_case_transitions_created_at
    ON case_transitions (created_at DESC);

-- ── Append-only enforcement ───────────────────────────────────────────────────

CREATE RULE no_update_case_transitions AS
    ON UPDATE TO case_transitions DO INSTEAD NOTHING;

CREATE RULE no_delete_case_transitions AS
    ON DELETE TO case_transitions DO INSTEAD NOTHING;

-- ── Comments ──────────────────────────────────────────────────────────────────

COMMENT ON TABLE  case_transitions IS 'Immutable audit trail of every case state transition. Append-only.';
COMMENT ON COLUMN case_transitions.case_id IS 'References cases.case_id (UUID). Cascades on case deletion.';
COMMENT ON COLUMN case_transitions.actor   IS 'system | workflow_engine | human:<agent_id>';
COMMENT ON COLUMN case_transitions.reason  IS 'Machine-readable reason string for the transition.';
