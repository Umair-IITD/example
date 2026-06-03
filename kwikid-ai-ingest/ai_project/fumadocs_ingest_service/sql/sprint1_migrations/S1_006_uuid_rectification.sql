-- S1_006_uuid_rectification.sql  (Sprint 1.2 — HARDENED v2)
-- ============================================================================
-- PURPOSE
-- ============================================================================
-- Converts all internal system ID columns from TEXT to UUID for deployments
-- where S1_001–S1_003 were applied before the Sprint 1.2 type-policy fix.
--
-- APPLY ORDER FOR EXISTING DEPLOYMENTS
-- ============================================================================
--   1. S1_001 (already applied — cases table exists with TEXT ids)
--   2. S1_002 (already applied — case_transitions exists with TEXT ids)
--   3. S1_003 (already applied — case_audit_log exists with TEXT ids)
--   4. S1_005 (already applied — sla_breach_at column + S1_005 indexes)
--   5. S1_006 (THIS FILE)
--   6. S1_004 (apply FRESH after S1_006 — FK will now succeed)
--
-- NOT NEEDED FOR FRESH DEPLOYMENTS
-- ============================================================================
-- If applying S1_001–S1_005 for the first time, use the corrected versions
-- of those files (case_id UUID throughout). S1_006 is idempotent and safe to
-- run on a fresh deployment (all steps will skip with NOTICE), but it is not
-- required.
--
-- BUGS FIXED IN THIS VERSION vs. FIRST S1_006 ATTEMPT
-- ============================================================================
-- BUG 1 (REPORTED — causes migration to fail):
--   ALTER COLUMN TYPE UUID fails when the column DEFAULT expression is
--   still typed as TEXT, e.g. (gen_random_uuid())::text.
--   PostgreSQL error: "default for column cannot be cast automatically to
--   type uuid".
--   FIX: DROP DEFAULT immediately before ALTER TYPE, then SET DEFAULT
--   gen_random_uuid() (which is natively typed as UUID) afterwards.
--   Applied in Steps 2, 3, 6.
--
-- BUG 2 (DISCOVERED — causes silent data loss then migration failure):
--   S1_003 creates a PostgreSQL RULE (no_update_case_audit) that intercepts
--   any UPDATE on case_audit_log and executes DO INSTEAD NOTHING — silently
--   discarding the statement without error. The prior S1_006 Step 6 ran a
--   backfill UPDATE inside a DO block without first removing this rule.
--   Result: the UPDATE was silently eaten, NULL rows were NOT backfilled,
--   and the subsequent ALTER COLUMN SET NOT NULL failed with
--   "column contains null values". The migration was left in a broken state.
--   FIX: Drop the rule, backfill, restore the rule — all in one atomic
--   DO block. If the block fails, the transaction rolls back and the rule
--   is restored automatically (PostgreSQL DDL is transactional).
--   Applied in Step 7.
--
-- BUG 3 (HARDENED):
--   Prior version hard-coded the FK constraint name 'case_transitions_case_id_fkey'.
--   If the original S1_002 was applied with any variation, the drop would fail
--   silently (constraint not found), and the subsequent TYPE change would then
--   fail with "cannot alter column referenced by a foreign key constraint".
--   FIX: Discover FK constraints dynamically from pg_constraint, using
--   relational identity (parent table + referenced column) not constraint name.
--   Applied in Step 1.
--
-- ADDITIONAL IMPROVEMENTS
-- ============================================================================
-- - Pre-flight validation: aborts if any existing ID value cannot be cast to
--   UUID (uses EXECUTE to defer type resolution to runtime, avoiding compile-
--   time errors when columns are already UUID).
-- - Separate DO blocks for each conversion step: each block is independently
--   atomic and idempotent. A failure in one step does not corrupt prior steps.
-- - Step 4 converts case_transitions.case_id separately (it has no DEFAULT to
--   drop; combining it with transition_id was confusing and fragile).
-- - Step 8 adds missing partial index idx_cases_client_state (WHERE closed_at
--   IS NULL) for existing deployments and removes the inferior non-partial
--   duplicate cases_client_state_idx from S1_005.
--
-- IDEMPOTENCY
-- ============================================================================
-- Each DO block checks current column type (or constraint/index existence)
-- before acting. Re-running this file on a fully-converted deployment produces
-- only NOTICE messages — no errors, no changes.
--
-- ROLLBACK
-- ============================================================================
-- Not supported once data exists. Take a Supabase point-in-time backup before
-- applying. Each DO block is a transaction; failure rolls back that block only,
-- not previously-committed blocks.
--
-- PRODUCTION SAFETY
-- ============================================================================
-- This file acquires ACCESS EXCLUSIVE locks on cases, case_transitions, and
-- case_audit_log during type conversions. On a Supabase instance with live
-- traffic, schedule this migration during a low-traffic window or maintenance
-- period. The lock duration is proportional to the number of rows in each table.
--
-- Supabase SQL editor: paste and execute this entire file as one batch.
-- psql: \i S1_006_uuid_rectification.sql


-- ============================================================================
-- PRE-FLIGHT: VALIDATE ALL EXISTING ID VALUES ARE UUID-CASTABLE
-- ============================================================================
-- Aborts with EXCEPTION (rolls back) if any row has an ID value that cannot
-- be cast to UUID. Uses EXECUTE to defer the query to runtime — if the column
-- is already UUID at runtime, we skip the check (column type check first).
-- This prevents compile-time errors from applying regex operators to UUID cols.

DO $$
DECLARE
    v_bad_count BIGINT := 0;
    v_total     BIGINT := 0;
BEGIN
    -- Skip if cases.case_id is already UUID
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'cases'
          AND  column_name  = 'case_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'PREFLIGHT cases.case_id: already UUID — check skipped';
        RETURN;
    END IF;

    -- Count rows with non-UUID-format case_id (using EXECUTE to avoid
    -- compile-time type error when the column might be UUID)
    EXECUTE $q$
        SELECT COUNT(*)
        FROM   cases
        WHERE  case_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    $q$ INTO v_bad_count;

    IF v_bad_count > 0 THEN
        RAISE EXCEPTION
            E'S1_006 PREFLIGHT ABORTED\n'
            'Found % row(s) in cases where case_id cannot be cast to UUID.\n'
            'Inspect with:\n'
            '  SELECT case_id FROM cases\n'
            '  WHERE case_id !~ ''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'';\n'
            'Fix or remove invalid rows, then re-run S1_006.',
            v_bad_count;
    END IF;

    EXECUTE 'SELECT COUNT(*) FROM cases' INTO v_total;
    RAISE NOTICE 'PREFLIGHT cases.case_id: % row(s) — all valid UUID format', v_total;
END $$;

DO $$
DECLARE
    v_bad_count BIGINT := 0;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_transitions'
          AND  column_name  = 'transition_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'PREFLIGHT case_transitions IDs: already UUID — check skipped';
        RETURN;
    END IF;

    EXECUTE $q$
        SELECT COUNT(*)
        FROM   case_transitions
        WHERE  transition_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR  case_id       !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    $q$ INTO v_bad_count;

    IF v_bad_count > 0 THEN
        RAISE EXCEPTION
            'S1_006 PREFLIGHT ABORTED: % row(s) in case_transitions have ID values '
            'that cannot be cast to UUID. Inspect transition_id and case_id columns.',
            v_bad_count;
    END IF;

    RAISE NOTICE 'PREFLIGHT case_transitions IDs: all rows valid UUID format';
END $$;

DO $$
DECLARE
    v_bad_count BIGINT := 0;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_audit_log'
          AND  column_name  = 'audit_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'PREFLIGHT case_audit_log IDs: already UUID — check skipped';
        RETURN;
    END IF;

    EXECUTE $q$
        SELECT COUNT(*)
        FROM   case_audit_log
        WHERE  audit_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR  case_id  !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    $q$ INTO v_bad_count;

    IF v_bad_count > 0 THEN
        RAISE EXCEPTION
            'S1_006 PREFLIGHT ABORTED: % row(s) in case_audit_log have ID values '
            'that cannot be cast to UUID. Inspect audit_id and case_id columns.',
            v_bad_count;
    END IF;

    RAISE NOTICE 'PREFLIGHT case_audit_log IDs: all rows valid UUID format';
END $$;


-- ============================================================================
-- STEP 1: DROP ALL FK CONSTRAINTS REFERENCING cases.case_id
-- ============================================================================
-- Dynamic discovery via pg_constraint — does NOT assume constraint names.
-- Covers case_transitions and any other table added since S1_002.
-- Skips entirely if cases.case_id is already UUID (conversion was done).

DO $$
DECLARE
    r             RECORD;
    v_case_attnum INT2;
    v_found       BOOLEAN := FALSE;
BEGIN
    -- Only needed if cases.case_id is still TEXT
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'cases'
          AND  column_name  = 'case_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'Step 1: cases.case_id already UUID — FK drop skipped';
        RETURN;
    END IF;

    -- Resolve the attribute number of cases.case_id
    SELECT attnum INTO v_case_attnum
    FROM   pg_attribute
    WHERE  attrelid = 'cases'::regclass
      AND  attname  = 'case_id'
      AND  attnum   > 0;

    IF v_case_attnum IS NULL THEN
        RAISE EXCEPTION 'Step 1: Cannot find cases.case_id column in pg_attribute.';
    END IF;

    -- Drop every FK that references cases.case_id (by relational identity, not name)
    FOR r IN
        SELECT c.conname,
               c.conrelid::regclass::text AS child_table
        FROM   pg_constraint c
        WHERE  c.contype   = 'f'
          AND  c.confrelid = 'cases'::regclass
          AND  v_case_attnum = ANY(c.confkey)
        ORDER  BY c.conrelid
    LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',
                       r.child_table, r.conname);
        RAISE NOTICE 'Step 1: Dropped FK % on %', r.conname, r.child_table;
        v_found := TRUE;
    END LOOP;

    IF NOT v_found THEN
        RAISE NOTICE 'Step 1: No FK constraints found referencing cases.case_id — none to drop';
    END IF;
END $$;


-- ============================================================================
-- STEP 2: cases.case_id  TEXT → UUID
-- ============================================================================
-- CRITICAL FIX: DROP DEFAULT before ALTER TYPE.
-- The existing DEFAULT (gen_random_uuid())::text is typed as TEXT.
-- PostgreSQL cannot automatically coerce a text-typed DEFAULT to UUID.
-- Sequence: DROP DEFAULT → ALTER TYPE (USING explicit cast) → SET DEFAULT.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'cases'
          AND  column_name  = 'case_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'Step 2: cases.case_id already UUID — skipped';
        RETURN;
    END IF;

    -- Drop the TEXT-typed default before changing column type
    ALTER TABLE cases ALTER COLUMN case_id DROP DEFAULT;

    -- Convert existing values using explicit USING clause
    ALTER TABLE cases ALTER COLUMN case_id TYPE UUID USING case_id::UUID;

    -- Set the UUID-native default
    ALTER TABLE cases ALTER COLUMN case_id SET DEFAULT gen_random_uuid();

    RAISE NOTICE 'Step 2: cases.case_id converted TEXT → UUID';
END $$;


-- ============================================================================
-- STEP 3: case_transitions.transition_id  TEXT → UUID
-- ============================================================================
-- Same DROP DEFAULT → ALTER TYPE → SET DEFAULT pattern as Step 2.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_transitions'
          AND  column_name  = 'transition_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'Step 3: case_transitions.transition_id already UUID — skipped';
        RETURN;
    END IF;

    ALTER TABLE case_transitions ALTER COLUMN transition_id DROP DEFAULT;
    ALTER TABLE case_transitions ALTER COLUMN transition_id TYPE UUID USING transition_id::UUID;
    ALTER TABLE case_transitions ALTER COLUMN transition_id SET DEFAULT gen_random_uuid();

    RAISE NOTICE 'Step 3: case_transitions.transition_id converted TEXT → UUID';
END $$;


-- ============================================================================
-- STEP 4: case_transitions.case_id  TEXT → UUID
-- ============================================================================
-- Handled in a separate block from transition_id because it has a different
-- profile: no DEFAULT (it is a FK column, not a primary key). No DROP DEFAULT
-- step needed. Type conversion only.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_transitions'
          AND  column_name  = 'case_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'Step 4: case_transitions.case_id already UUID — skipped';
        RETURN;
    END IF;

    ALTER TABLE case_transitions ALTER COLUMN case_id TYPE UUID USING case_id::UUID;

    RAISE NOTICE 'Step 4: case_transitions.case_id converted TEXT → UUID';
END $$;


-- ============================================================================
-- STEP 5: RESTORE FK  case_transitions.case_id → cases.case_id
-- ============================================================================
-- Both sides are now UUID. Restore the FK with an explicit constraint name
-- for idempotency. ON DELETE CASCADE matches the original S1_002 intent.
-- The existence check uses pg_constraint (not information_schema) so it
-- correctly detects constraints regardless of auto-generated vs explicit names.

DO $$
BEGIN
    -- Check by relational identity, not by name
    IF EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  contype   = 'f'
          AND  conrelid  = 'case_transitions'::regclass
          AND  confrelid = 'cases'::regclass
    ) THEN
        RAISE NOTICE 'Step 5: FK case_transitions → cases already exists — skipped';
        RETURN;
    END IF;

    ALTER TABLE case_transitions
        ADD CONSTRAINT case_transitions_case_id_fkey
        FOREIGN KEY (case_id)
        REFERENCES cases (case_id)
        ON DELETE CASCADE;

    RAISE NOTICE 'Step 5: Restored FK case_transitions.case_id → cases.case_id (ON DELETE CASCADE)';
END $$;


-- ============================================================================
-- STEP 6: case_audit_log.audit_id  TEXT → UUID
-- ============================================================================
-- Same DROP DEFAULT → ALTER TYPE → SET DEFAULT pattern as Step 2.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_audit_log'
          AND  column_name  = 'audit_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'Step 6: case_audit_log.audit_id already UUID — skipped';
        RETURN;
    END IF;

    ALTER TABLE case_audit_log ALTER COLUMN audit_id DROP DEFAULT;
    ALTER TABLE case_audit_log ALTER COLUMN audit_id TYPE UUID USING audit_id::UUID;
    ALTER TABLE case_audit_log ALTER COLUMN audit_id SET DEFAULT gen_random_uuid();

    RAISE NOTICE 'Step 6: case_audit_log.audit_id converted TEXT → UUID';
END $$;


-- ============================================================================
-- STEP 7: case_audit_log.case_id  TEXT → UUID
-- ============================================================================
-- case_audit_log.case_id has NO DEFAULT and NO FK (intentional — audit log
-- must outlive cases for DPDP compliance). No DROP DEFAULT needed.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_audit_log'
          AND  column_name  = 'case_id'
          AND  data_type    = 'text'
    ) THEN
        RAISE NOTICE 'Step 7: case_audit_log.case_id already UUID — skipped';
        RETURN;
    END IF;

    ALTER TABLE case_audit_log ALTER COLUMN case_id TYPE UUID USING case_id::UUID;

    RAISE NOTICE 'Step 7: case_audit_log.case_id converted TEXT → UUID';
END $$;


-- ============================================================================
-- STEP 8: case_audit_log.action_detail → NOT NULL DEFAULT '{}'::jsonb
-- ============================================================================
-- CRITICAL FIX: The no_update_case_audit RULE (created in S1_003) intercepts
-- all UPDATE statements on case_audit_log and executes DO INSTEAD NOTHING —
-- silently discarding the backfill UPDATE without error or warning.
-- The prior S1_006 ran the UPDATE inside a DO block while this rule was active,
-- so NULL rows were NEVER backfilled, and the SET NOT NULL then failed with
-- "column contains null values".
--
-- FIX: All three operations (drop rule / backfill / restore rule) happen inside
-- one atomic DO block. If the block fails at any point, the entire transaction
-- rolls back — including the DROP RULE — so the rule is always in a consistent
-- state after the block completes (either all committed or all rolled back).
--
-- The SET NOT NULL (DDL) is also inside the same block for atomicity: if the
-- SET NOT NULL fails (e.g. a NULL was missed), the block rolls back, the rule
-- is restored, and no partial changes are committed.

DO $$
DECLARE
    v_null_count   BIGINT  := 0;
    v_rule_existed BOOLEAN := FALSE;
BEGIN
    -- Skip if action_detail is already NOT NULL
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = 'case_audit_log'
          AND  column_name  = 'action_detail'
          AND  is_nullable  = 'YES'
    ) THEN
        RAISE NOTICE 'Step 8: case_audit_log.action_detail already NOT NULL — skipped';
        RETURN;
    END IF;

    -- Count existing NULLs
    SELECT COUNT(*) INTO v_null_count
    FROM   case_audit_log
    WHERE  action_detail IS NULL;

    RAISE NOTICE 'Step 8: Found % NULL action_detail row(s)', v_null_count;

    -- Remove the UPDATE-blocking RULE before the backfill
    IF EXISTS (
        SELECT 1 FROM pg_rules
        WHERE  tablename = 'case_audit_log'
          AND  rulename  = 'no_update_case_audit'
    ) THEN
        DROP RULE no_update_case_audit ON case_audit_log;
        v_rule_existed := TRUE;
        RAISE NOTICE 'Step 8: Dropped no_update_case_audit rule (temporarily)';
    ELSE
        RAISE NOTICE 'Step 8: no_update_case_audit rule not found — proceeding without drop';
    END IF;

    -- Backfill NULLs (now unblocked by rule removal)
    IF v_null_count > 0 THEN
        UPDATE case_audit_log
            SET action_detail = '{}'::jsonb
            WHERE action_detail IS NULL;
        RAISE NOTICE 'Step 8: Backfilled % NULL action_detail row(s) with {}', v_null_count;
    END IF;

    -- Restore the append-only rule immediately after backfill
    -- (restored before SET NOT NULL so the rule is always present
    --  if any subsequent step fails and the block rolls back)
    IF v_rule_existed THEN
        CREATE RULE no_update_case_audit AS
            ON UPDATE TO case_audit_log DO INSTEAD NOTHING;
        RAISE NOTICE 'Step 8: Restored no_update_case_audit rule';
    END IF;

    -- Now safe to enforce NOT NULL
    ALTER TABLE case_audit_log
        ALTER COLUMN action_detail SET NOT NULL;
    ALTER TABLE case_audit_log
        ALTER COLUMN action_detail SET DEFAULT '{}'::jsonb;

    RAISE NOTICE 'Step 8: action_detail set NOT NULL DEFAULT {}';
END $$;


-- ============================================================================
-- STEP 9: INDEX CLEANUP FOR EXISTING DEPLOYMENTS
-- ============================================================================
-- S1_005 created two indexes that are inferior duplicates of what S1_001
-- (rewritten) now declares. On existing deployments only the S1_005 versions
-- exist. This step adds the canonical versions and removes the duplicates.
--
-- (client, current_state):
--   S1_005 created: cases_client_state_idx ON (client, current_state)       -- full, no WHERE
--   S1_001 declares: idx_cases_client_state ON (client, current_state)       -- partial, WHERE closed_at IS NULL
--   Result: add partial version; drop non-partial (waste on a queue that is
--   mostly closed cases — full index bloats with historical closed records).
--
-- (sla_breach_at):
--   S1_005 created: cases_sla_breach_idx ON (sla_breach_at) WHERE NOT NULL   -- functionally identical to:
--   S1_001 declares: idx_cases_sla_breach ON (sla_breach_at) WHERE NOT NULL  -- canonical name
--   Result: add canonical name; drop S1_005 duplicate.
--
-- NOTE: DROP INDEX acquires ACCESS EXCLUSIVE lock. On Supabase with live
-- traffic, replace the DROP INDEX with DROP INDEX CONCURRENTLY — but that
-- must be run OUTSIDE a transaction (cannot be in a DO block). In that case,
-- comment out the DROP lines below and run them manually after this file.

CREATE INDEX IF NOT EXISTS idx_cases_client_state
    ON cases (client, current_state)
    WHERE closed_at IS NULL;

DROP INDEX IF EXISTS cases_client_state_idx;

CREATE INDEX IF NOT EXISTS idx_cases_sla_breach
    ON cases (sla_breach_at)
    WHERE sla_breach_at IS NOT NULL;

DROP INDEX IF EXISTS cases_sla_breach_idx;


-- ============================================================================
-- POST-MIGRATION: MANUAL VERIFICATION QUERIES
-- ============================================================================
-- Run these in the Supabase SQL editor after applying this file and S1_004.
-- All results must match the expected values shown in comments.

-- ── V1: Column types (must all be 'uuid') ─────────────────────────────────────
--
-- SELECT table_name, column_name, data_type, is_nullable, column_default
-- FROM   information_schema.columns
-- WHERE  table_schema = 'public'
--   AND  table_name   IN ('cases', 'case_transitions', 'case_audit_log')
--   AND  column_name  IN ('case_id', 'transition_id', 'audit_id', 'action_detail')
-- ORDER  BY table_name, column_name;
--
-- Expected:
--   case_audit_log  | action_detail  | jsonb | NO  | '{}'::jsonb
--   case_audit_log  | audit_id       | uuid  | NO  | gen_random_uuid()
--   case_audit_log  | case_id        | uuid  | NO  | (null)
--   case_transitions| case_id        | uuid  | NO  | (null)
--   case_transitions| transition_id  | uuid  | NO  | gen_random_uuid()
--   cases           | case_id        | uuid  | NO  | gen_random_uuid()

-- ── V2: FK constraints (must include case_transitions → cases) ────────────────
--
-- SELECT tc.constraint_name,
--        tc.table_name         AS child_table,
--        kcu.column_name       AS child_column,
--        ccu.table_name        AS parent_table,
--        ccu.column_name       AS parent_column,
--        rc.delete_rule
-- FROM   information_schema.table_constraints   tc
-- JOIN   information_schema.key_column_usage    kcu
--        ON kcu.constraint_name = tc.constraint_name
-- JOIN   information_schema.constraint_column_usage ccu
--        ON ccu.constraint_name = tc.constraint_name
-- JOIN   information_schema.referential_constraints rc
--        ON rc.constraint_name = tc.constraint_name
-- WHERE  tc.constraint_type = 'FOREIGN KEY'
--   AND  tc.table_schema    = 'public'
-- ORDER  BY tc.table_name;
--
-- Expected (after S1_004 also applied):
--   case_transitions_case_id_fkey | case_transitions | case_id | cases | case_id | CASCADE
--   security_compliance_audit_case_id_fkey | security_compliance_audit | case_id | cases | case_id | SET NULL

-- ── V3: No remaining NULL action_detail rows ──────────────────────────────────
--
-- SELECT COUNT(*) AS null_action_detail_rows
-- FROM   case_audit_log
-- WHERE  action_detail IS NULL;
--
-- Expected: 0

-- ── V4: Indexes present (must include the partial idx_cases_client_state) ──────
--
-- SELECT indexname, indexdef
-- FROM   pg_indexes
-- WHERE  schemaname = 'public'
--   AND  tablename  = 'cases'
-- ORDER  BY indexname;
--
-- Expected: idx_cases_client_state present with "WHERE (closed_at IS NULL)"
-- Expected: cases_client_state_idx ABSENT
-- Expected: idx_cases_sla_breach present
-- Expected: cases_sla_breach_idx ABSENT

-- ── V5: RLS on security_compliance_audit (after S1_004 applied) ───────────────
--
-- SELECT tablename, rowsecurity, relforcerowsecurity
-- FROM   pg_tables t
-- JOIN   pg_class  c ON c.relname = t.tablename
-- WHERE  t.schemaname = 'public'
--   AND  t.tablename  = 'security_compliance_audit';
--
-- Expected: rowsecurity = true

-- ── V6: Append-only rules present on audit tables ─────────────────────────────
--
-- SELECT tablename, rulename, definition
-- FROM   pg_rules
-- WHERE  schemaname = 'public'
--   AND  tablename  IN ('case_transitions', 'case_audit_log')
-- ORDER  BY tablename, rulename;
--
-- Expected: 4 rules:
--   case_audit_log    | no_delete_case_audit       | ON DELETE TO case_audit_log DO INSTEAD NOTHING
--   case_audit_log    | no_update_case_audit       | ON UPDATE TO case_audit_log DO INSTEAD NOTHING
--   case_transitions  | no_delete_case_transitions | ON DELETE TO case_transitions DO INSTEAD NOTHING
--   case_transitions  | no_update_case_transitions | ON UPDATE TO case_transitions DO INSTEAD NOTHING
