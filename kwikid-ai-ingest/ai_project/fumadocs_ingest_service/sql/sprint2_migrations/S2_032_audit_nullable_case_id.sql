-- S2_032_audit_nullable_case_id.sql
-- Sprint 2.32: Fix audit write failure — "invalid input syntax for type uuid: ''"
--
-- Root cause (proven in Sprint 2.31 ULTRATHINK analysis):
--   1. AuditEntry.case_id defaults to "" (empty string)
--   2. Both handler _audit_event() helpers create AuditEntry without case_id
--   3. case_audit_log.case_id is UUID NOT NULL — rejects both "" and NULL
--   4. Some audit events (WEBHOOK_DUPLICATE, CLIENT_RESOLUTION_FAILED) legitimately
--      occur BEFORE a Case is created, so they can never have a UUID
--
-- Architectural contract (Option B):
--   case_id is OPTIONAL — pre-case events write NULL, post-case events write the UUID.
--
-- Changes:
--   Step 1 — Drop NOT NULL on case_id (allows NULL for pre-case events)
--   Step 2 — Drop stale action_type check constraint
--             S1_003 only listed Sprint 1 event types; Sprint 2 added 20+ Freshdesk
--             event types that were never added to the constraint. The Python
--             AuditEventType enum is the single source of truth for valid values —
--             the DB constraint was already out of sync and is not needed.
--
-- Rollback:
--   ALTER TABLE case_audit_log ALTER COLUMN case_id SET NOT NULL;
--   (Re-adding the check constraint is not recommended — keep Python enum as guard.)

-- Step 1: Make case_id nullable (pre-case events write NULL)
ALTER TABLE case_audit_log
    ALTER COLUMN case_id DROP NOT NULL;

-- Step 2: Drop the Sprint-1-only action_type constraint
--         Python AuditEventType enum already enforces valid values before DB write.
ALTER TABLE case_audit_log
    DROP CONSTRAINT IF EXISTS case_audit_action_type_check;
