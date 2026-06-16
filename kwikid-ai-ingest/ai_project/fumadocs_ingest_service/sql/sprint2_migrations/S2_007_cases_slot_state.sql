-- S2_007_cases_slot_state.sql
-- Sprint 2.15: Add slot_state JSONB column to cases table.
--
-- slot_state stores the active slot-filling context for a case.
-- Schema: {slot_name: {status, value, attempt_count}}
-- Example:
--   {
--     "session_id":   {"status": "FILLED",  "value": "KID-AB12CD34", "attempt_count": 0},
--     "phone_number": {"status": "PENDING",  "value": null,           "attempt_count": 0}
--   }
--
-- TTL: retained indefinitely (compliance audit trail). Redis TTL-30min policy
--      applies to the hot cache (Level 2 concern, not yet implemented).
--
-- Rollback: ALTER TABLE cases DROP COLUMN IF EXISTS slot_state;

BEGIN;

ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS slot_state JSONB;

-- GIN index for querying slot state contents (e.g., find all cases where session_id is filled)
CREATE INDEX IF NOT EXISTS idx_cases_slot_state
    ON cases USING GIN (slot_state)
    WHERE slot_state IS NOT NULL;

COMMENT ON COLUMN cases.slot_state IS
    'Active slot-filling context: {slot_name: {status, value, attempt_count}}. '
    'NULL until first message received. Persisted for audit; hot cache in Redis (Level 2).';

COMMIT;
