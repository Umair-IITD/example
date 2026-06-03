-- S1_004_security_compliance_audit.sql
-- Sprint 1.1 — Security Compliance Audit Table
--
-- Separate from case_audit_log. Security events (DEEPFAKE, FOREIGN_IP,
-- AADHAAR_MISMATCH, etc.) route here, not to case_audit_log.
-- Access is SOC-only: RLS blocks all anon/authenticated access;
-- only the service_role key (bypasses RLS in Supabase) may read or write.
--
-- Apply via Supabase SQL editor. Idempotent (IF NOT EXISTS throughout).

-- ── Table ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS security_compliance_audit (
    audit_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id             UUID        REFERENCES cases(case_id) ON DELETE SET NULL,
    ticket_id           TEXT,
    client              TEXT,
    security_event_type TEXT        NOT NULL,
    event_details       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    severity            TEXT        NOT NULL DEFAULT 'HIGH'
                            CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')),
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    reported_to_soc     BOOLEAN     NOT NULL DEFAULT FALSE,
    soc_reported_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Row-level security ────────────────────────────────────────────────────────
-- Enable RLS so anon/authenticated roles cannot access this table.
-- The Supabase service_role key bypasses RLS and is used exclusively
-- by the AI ingest service for writes and by SOC tooling for reads.

ALTER TABLE security_compliance_audit ENABLE ROW LEVEL SECURITY;

-- Deny ALL for public (anon) role
DROP POLICY IF EXISTS deny_anon_security_audit ON security_compliance_audit;
CREATE POLICY deny_anon_security_audit
    ON security_compliance_audit
    AS RESTRICTIVE
    FOR ALL
    TO anon
    USING (false);

-- Deny ALL for authenticated role (agents use authenticated JWT, not service role)
DROP POLICY IF EXISTS deny_authenticated_security_audit ON security_compliance_audit;
CREATE POLICY deny_authenticated_security_audit
    ON security_compliance_audit
    AS RESTRICTIVE
    FOR ALL
    TO authenticated
    USING (false);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- SOC investigation: find all events for a given case
CREATE INDEX IF NOT EXISTS sca_case_id_idx
    ON security_compliance_audit (case_id)
    WHERE case_id IS NOT NULL;

-- SOC investigation: unresolved security events (not yet reported to SOC)
CREATE INDEX IF NOT EXISTS sca_unreported_idx
    ON security_compliance_audit (detected_at)
    WHERE reported_to_soc = FALSE;

-- SOC investigation: filter by severity + event type
CREATE INDEX IF NOT EXISTS sca_severity_type_idx
    ON security_compliance_audit (severity, security_event_type, detected_at DESC);

-- SOC investigation: filter by client for compliance reporting
CREATE INDEX IF NOT EXISTS sca_client_detected_idx
    ON security_compliance_audit (client, detected_at DESC)
    WHERE client IS NOT NULL;

-- ── Comments ──────────────────────────────────────────────────────────────────

COMMENT ON TABLE security_compliance_audit IS
    'SOC-only security event log. RLS blocks all non-service-role access. '
    'See 07_GOVERNANCE_POLICY_AND_HANDOFF.md §6 for routing rules.';

COMMENT ON COLUMN security_compliance_audit.security_event_type IS
    'Security trigger type, e.g. DEEPFAKE_SUSPECTED, FOREIGN_IP, AADHAAR_MISMATCH, '
    'DATA_EXFIL_PATTERN, ACCOUNT_TAKEOVER_PATTERN.';

COMMENT ON COLUMN security_compliance_audit.reported_to_soc IS
    'Set to TRUE once the SOC notification pipeline has acknowledged this event.';
