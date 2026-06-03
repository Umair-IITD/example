-- S1_005_operational_telemetry.sql
-- Sprint 1.1 — Operational Telemetry Schema Additions
--
-- 1. Composite index (client, current_state) on cases — enables efficient
--    per-client queue dashboards without full table scans.
-- 2. sla_breach_at column on cases — stores the computed SLA deadline for
--    escalated cases; used by the Level 2 SLA watchdog (Sprint 2).
--
-- Apply via Supabase SQL editor. Idempotent (IF NOT EXISTS throughout).

-- ── sla_breach_at column ──────────────────────────────────────────────────────

ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS sla_breach_at TIMESTAMPTZ;

COMMENT ON COLUMN cases.sla_breach_at IS
    'SLA deadline for this case. NULL until set by Level 2 SLA watchdog (Sprint 2). '
    'When non-NULL and current_state is ESCALATED/AWAITING_INPUT, SLA breach alerts fire.';

-- ── Composite index: per-client state queue ───────────────────────────────────
-- Supports: SELECT * FROM cases WHERE client = $1 AND current_state = $2
-- Used by Level 1 dashboards and Level 2 queue watchers.

CREATE INDEX IF NOT EXISTS cases_client_state_idx
    ON cases (client, current_state);

-- ── Index: SLA breach monitoring ──────────────────────────────────────────────
-- Supports: SELECT * FROM cases WHERE sla_breach_at <= now() AND current_state NOT IN (...)
-- Used by the Sprint 2 SLA watchdog to find breaching cases efficiently.

CREATE INDEX IF NOT EXISTS cases_sla_breach_idx
    ON cases (sla_breach_at)
    WHERE sla_breach_at IS NOT NULL;
