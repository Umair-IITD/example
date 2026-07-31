# Sprint 2.1.5 — Action Gateway Schema

## Overview

Sprint 2.1.5 introduces the persistent database layer for the Action Gateway. The Sprint 2.1 implementation was fully in-memory; this sprint defines the authoritative schema that makes action state durable across process restarts, enabling the executor, SLA watchdog, and approval API to be built in Sprint 2.2.

Two tables are defined in `sql/sprint2_migrations/S2_001_action_gateway.sql`:

| Table | Role |
|---|---|
| `action_gateway` | Mutable operational state — every action from proposal to terminal state |
| `action_gateway_transitions` | Immutable audit trail — every state change, append-only |

## Supersession Notice

This sprint supersedes two previously designed (but never applied) migrations:

- `S2_001_action_requests.sql` → **do not apply**
- `S2_002_action_transition_log.sql` → **do not apply**

**Operator action required before deployment:**
```
mv sql/sprint2_migrations/S2_001_action_requests.sql \
   sql/sprint2_migrations/_SUPERSEDED_S2_001_action_requests.sql

mv sql/sprint2_migrations/S2_002_action_transition_log.sql \
   sql/sprint2_migrations/_SUPERSEDED_S2_002_action_transition_log.sql
```

## Architecture Decisions

### AD-1: Table name — `action_gateway` not `action_requests`

`action_gateway` matches the service class name (`ActionGateway`) and is more descriptive of the table's role. "Requests" implies it's a queue of pending items; the table is the complete operational state of every action across its entire lifecycle.

### AD-2: Self-referencing FK on `rollback_action_id`

`rollback_action_id` is a `REFERENCES action_gateway(action_id) ON DELETE RESTRICT`. This is the correct choice for an operational table:

- Action rows are **never deleted** in this platform — they reach terminal states and remain as compliance artefacts. `ON DELETE RESTRICT` is therefore effectively inert.
- The FK guarantees that `rollback_action_id` always resolves to a real action. Without it, a stale UUID would silently corrupt the rollback chain.
- `propose_rollback()` inserts the compensation action first, then UPDATEs the original with `rollback_action_id`. The FK is satisfied at every point.

Rejected: unbound UUID. The unbound approach is correct for pure audit tables (where the record must outlive a deletable parent). `action_gateway` is not a pure audit table — it's the live operational state.

### AD-3: Transition table FK — deviation from Sprint 1 philosophy

Sprint 1 audit tables (`case_audit_log`, `case_transitions`) used **no FK** on `case_id` so that audit records could outlive their parent case. `action_gateway_transitions` uses a **FK with ON DELETE RESTRICT**.

Justification for the deviation:

1. `action_gateway` rows are never deleted → `RESTRICT` is inert
2. The FK provides DB-level assurance that every transition references a real action
3. The Sprint 1 no-FK rationale (DPDP archival, forensic reconstruction after deletion) only applies when rows can actually be deleted

**Future migration note:** If archival/deletion of `action_gateway` rows is ever introduced, the FK on `action_gateway_transitions.action_id` must be dropped first in that migration.

### AD-4: `expires_at` — unified deadline column

`expires_at` replaces the Sprint 2.1 `approval_deadline` and serves two roles depending on the current state:

| State | Semantics |
|---|---|
| `AWAITING_APPROVAL` | Approval SLA deadline (4h REVERSIBLE, 24h IRREVERSIBLE) |
| `APPROVED` | Executor pickup deadline — executor must begin before this |

A single column for both makes the SLA watchdog simpler: one partial index scan on `current_state IN ('AWAITING_APPROVAL', 'APPROVED') AND expires_at < NOW()` finds all overdue actions across both phases.

### AD-5: Explicit event timestamp columns

`rejected_at`, `execution_failed_at`, `rollback_completed_at` are redundant with the transitions table but are explicitly stored in `action_gateway` for:

- Single-row dashboard queries (no JOIN to transitions)
- Direct aggregation for time-to-rejection and time-to-failure metrics
- `"all rejections this week"` reports as a simple partial index scan on one table

### AD-6: 12 added columns beyond user spec

The user's 22-column spec was augmented with 12 fields required for business logic correctness:

| Column | Reason |
|---|---|
| `action_namespace` | Executor routing — cannot dispatch without target system identifier |
| `executor_id` | Stuck-execution watchdog (Sprint 2.2) |
| `execution_attempt` | Retry logic — `can_retry` property requires this persisted across restarts |
| `max_attempts` | Retry ceiling — without this, IRREVERSIBLE becomes retryable |
| `rollback_action_type` | `propose_rollback()` cannot create compensation without this |
| `rollback_params` | Compensation parameters for rollback |
| `proposed_by` | Audit: who initiated the proposal |
| `proposed_at` | SLA measurement (distinct from `created_at`) |
| `approval_notes` | SOC audit trail — agent justification |
| `execution_result` | Sanitized success response from target system |
| `failure_code` | Machine-readable failure code for automated routing |
| `is_rolled_back` | Explicit boolean faster than deriving from `rollback_completed_at IS NOT NULL` |

### AD-7: `ag_irreversible_no_rollback` CHECK constraint

```sql
CONSTRAINT ag_irreversible_no_rollback CHECK (
    risk_level != 'IRREVERSIBLE' OR rollback_action_type IS NULL
)
```

IRREVERSIBLE actions must never carry a `rollback_action_type`. There is no safe compensation path for irreversible operations — if this column were set, `propose_rollback()` would silently create a compensating action for an operation that cannot be safely reversed. The DB constraint prevents this at the data level, not just the service level.

## Schema Reference

### action_gateway (34 columns)

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `action_id` | UUID | NOT NULL | gen_random_uuid() | PK |
| `case_id` | UUID | NOT NULL | — | FK→cases(case_id) RESTRICT |
| `ticket_id` | TEXT | NOT NULL | — | Freshdesk ticket number |
| `client` | TEXT | NOT NULL | — | Tenant slug |
| `action_type` | TEXT | NOT NULL | — | e.g. POST_RESOLUTION_NOTE |
| `action_namespace` | TEXT | NOT NULL | — | e.g. freshdesk, payment_gateway |
| `risk_level` | TEXT | NOT NULL | — | SAFE/REVERSIBLE/IRREVERSIBLE |
| `current_state` | TEXT | NOT NULL | PROPOSED | 12-state machine |
| `action_payload` | JSONB | NOT NULL | '{}' | Sanitized — no PII |
| `rollback_action_type` | TEXT | NULL | — | REVERSIBLE only |
| `rollback_params` | JSONB | NULL | — | REVERSIBLE only |
| `proposed_by` | TEXT | NOT NULL | system | Audit trail |
| `proposed_at` | TIMESTAMPTZ | NOT NULL | NOW() | SLA start time |
| `approval_required` | BOOLEAN | NOT NULL | TRUE | FALSE for SAFE |
| `expires_at` | TIMESTAMPTZ | NULL | — | Unified SLA deadline |
| `approver` | TEXT | NULL | — | Agent or 'auto_approval' |
| `approved_at` | TIMESTAMPTZ | NULL | — | Approval decision time |
| `rejected_at` | TIMESTAMPTZ | NULL | — | Rejection time |
| `approval_notes` | TEXT | NULL | — | Agent justification |
| `executor_id` | TEXT | NULL | — | Worker ID |
| `execution_started_at` | TIMESTAMPTZ | NULL | — | |
| `execution_completed_at` | TIMESTAMPTZ | NULL | — | Success timestamp |
| `execution_failed_at` | TIMESTAMPTZ | NULL | — | Failure timestamp |
| `execution_attempt` | SMALLINT | NOT NULL | 0 | Increments before each attempt |
| `max_attempts` | SMALLINT | NOT NULL | 3 | 1 for IRREVERSIBLE |
| `execution_result` | JSONB | NULL | — | Sanitized success response |
| `failure_code` | TEXT | NULL | — | Machine-readable code |
| `failure_reason` | TEXT | NULL | — | Human-readable description |
| `rollback_action_id` | UUID | NULL | — | Self-ref FK RESTRICT |
| `rollback_completed_at` | TIMESTAMPTZ | NULL | — | Rollback completion time |
| `is_rolled_back` | BOOLEAN | NOT NULL | FALSE | Explicit rollback flag |
| `idempotency_key` | TEXT | NOT NULL | — | UNIQUE SHA-256 hex |
| `created_at` | TIMESTAMPTZ | NOT NULL | NOW() | |
| `updated_at` | TIMESTAMPTZ | NOT NULL | NOW() | Auto-updated by trigger |

### CHECK Constraints — action_gateway

| Constraint | Rule |
|---|---|
| `ag_risk_level_check` | risk_level IN ('SAFE', 'REVERSIBLE', 'IRREVERSIBLE') |
| `ag_state_check` | current_state IN (12 state values) |
| `ag_max_attempts_range` | max_attempts BETWEEN 1 AND 10 |
| `ag_attempt_lte_max` | execution_attempt <= max_attempts |
| `ag_attempt_nonneg` | execution_attempt >= 0 |
| `ag_idempotency_unique` | UNIQUE (idempotency_key) |
| `ag_approval_decision_mutex` | NOT (approved_at IS NOT NULL AND rejected_at IS NOT NULL) |
| `ag_rollback_flag_integrity` | is_rolled_back = TRUE implies rollback_action_id IS NOT NULL |
| `ag_rollback_timestamp_integrity` | rollback_completed_at IS NOT NULL implies rollback_action_id IS NOT NULL |
| `ag_irreversible_no_rollback` | IRREVERSIBLE implies rollback_action_type IS NULL |

### action_gateway_transitions (11 columns)

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `transition_id` | UUID | NOT NULL | PK |
| `action_id` | UUID | NOT NULL | FK→action_gateway RESTRICT |
| `case_id` | UUID | NOT NULL | Denormalized |
| `ticket_id` | TEXT | NOT NULL | Denormalized |
| `client` | TEXT | NOT NULL | Denormalized |
| `from_state` | TEXT | NOT NULL | CHECK (12 states) |
| `to_state` | TEXT | NOT NULL | CHECK (12 states) |
| `actor` | TEXT | NOT NULL | system/auto_approval/human/executor/watchdog |
| `reason` | TEXT | NULL | Machine-readable reason string |
| `detail` | JSONB | NOT NULL | Structured context (no PII) |
| `created_at` | TIMESTAMPTZ | NOT NULL | |

Append-only: PostgreSQL RULES discard UPDATE and DELETE at the DB level.

## Index Strategy

### action_gateway (10 indexes + 1 implicit from UNIQUE)

| Index | Columns + WHERE | Query Pattern |
|---|---|---|
| `idx_ag_case_id_time` | (case_id, created_at DESC) | Case action history |
| `idx_ag_ticket_client` | (ticket_id, client) | Freshdesk webhook lookup |
| `idx_ag_pending_approval` | (client, expires_at ASC) WHERE AWAITING_APPROVAL | SLA watchdog — most urgent first |
| `idx_ag_approved_queue` | (client, risk_level, created_at ASC) WHERE APPROVED | Executor FIFO pickup queue |
| `idx_ag_executing` | (execution_started_at ASC) WHERE EXECUTING | Stuck-execution watchdog |
| `idx_ag_expires_at_sweep` | (expires_at ASC) WHERE NOT NULL AND state IN (AWAITING_APPROVAL, APPROVED) | Global SLA sweep |
| `idx_ag_rollback_eligible` | (case_id, execution_completed_at DESC) WHERE EXECUTED + REVERSIBLE + no rollback | Rollback candidate lookup |
| `idx_ag_client_state_time` | (client, current_state, created_at DESC) | Compliance reporting |
| `idx_ag_rollback_action_id` | (rollback_action_id) WHERE NOT NULL | Original→rollback chain lookup |
| `idx_ag_created_at` | (created_at DESC) | SOC global timeline |
| *(implicit)* | UNIQUE (idempotency_key) | Duplicate proposal detection |

**Partial index rationale**: Production queues are dominated by terminal-state rows over time. A full-table index on `(client, current_state)` would scan mostly `REJECTED`, `EXPIRED`, `EXECUTED` rows for any active-state query. Each partial index above scans only the operationally relevant subset.

### action_gateway_transitions (5 indexes)

| Index | Columns | Query Pattern |
|---|---|---|
| `idx_agt_action_id_time` | (action_id, created_at ASC) | Per-action timeline (chronological) |
| `idx_agt_case_id_time` | (case_id, created_at DESC) | Per-case audit history |
| `idx_agt_client_time` | (client, created_at DESC) | Per-client SOC reporting |
| `idx_agt_to_state_time` | (to_state, created_at DESC) | State arrival queries (failure rates, etc.) |
| `idx_agt_created_at` | (created_at DESC) | Cross-client SOC audit |

## Idempotency Design

```
idempotency_key = SHA-256(case_id + action_type + action_namespace + canonical_json(action_payload))
```

**Canonical JSON** uses `json.dumps(sort_keys=True, separators=(',', ':'))` to ensure key ordering does not affect the hash.

**Race condition protection**: Two concurrent proposals for the same logical action can both pass the Python-level `get_action_by_idempotency_key()` check before either INSERT completes. The `UNIQUE (idempotency_key)` constraint catches this at the DB level — only one INSERT succeeds; the second raises `UniqueViolation`. The `ActionRepository.insert_action()` method must catch `UniqueViolation` and convert it to `DuplicateActionError` (Sprint 2.1.5 follow-up).

**Scope**: The idempotency key is case-scoped. If the same action type is needed for two different cases, the different `case_id` produces different keys — two proposals are correctly allowed. If a case is truly reopened (not just re-processed), a new Case record with a new `case_id` would be created, also generating a new key.

## Python Model Alignment Requirements

The current `ActionRequest` Python model uses different field names than this schema. The following changes are required in `case_engine/action_models.py` before connecting the gateway to this table:

### Field Renames (to_db_row and from_db_row)

| Python field | DB column | Change |
|---|---|---|
| `action_params` | `action_payload` | Rename |
| `approved_by` | `approver` | Rename |
| `approval_decision_at` | `approved_at` | Rename |
| `approval_deadline` | `expires_at` | Rename |

### New Fields Required (no current Python equivalent)

| DB column | Set by | Python field to add |
|---|---|---|
| `rejected_at` | `ActionGateway.reject()` | `rejected_at: datetime | None = None` |
| `execution_failed_at` | `record_failure()`, `record_timeout()` | `execution_failed_at: datetime | None = None` |
| `rollback_completed_at` | `record_rollback_success()` | `rollback_completed_at: datetime | None = None` |

### Transition Record Rename

`ActionTransitionRecord` currently produces `log_id` in `to_db_row()`. The DB column is `transition_id`. Rename the field.

**Tracking**: The schema contract tests (`test_sprint215_schema_contract.py`) contain 8 `xfail` tests that serve as the acceptance criteria for these model changes. Remove the `@pytest.mark.xfail` decorators after completing the updates.

## Row-Level Security

Both tables use identical RLS policies:

| Role | Access |
|---|---|
| `service_role` | Full bypass — all reads and writes via ActionGateway service |
| `authenticated` | SELECT only for rows matching their JWT `client` claim |
| `anon` | Deny all (RESTRICTIVE policy) |

JWT claim path: `auth.jwt() -> 'user_metadata' ->> 'client'` with fallback to `app_metadata`. Adjust if your Supabase auth provider uses a different claim path.

## Migration Ordering

```
S1_001_cases.sql                     ← must exist (provides cases.case_id UUID PK)
S1_006_uuid_rectification.sql        ← must be applied to existing deployments
S2_001_action_gateway.sql            ← THIS FILE (Sprint 2.1.5)
```

Do NOT apply the superseded files:
- `S2_001_action_requests.sql`
- `S2_002_action_transition_log.sql`

## Manual Verification Queries

Run these in the Supabase SQL editor after applying the migration.

### V1 — Both tables exist
```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('action_gateway', 'action_gateway_transitions')
ORDER BY table_name;
-- Expected: 2 rows
```

### V2 — action_gateway column count
```sql
SELECT COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'action_gateway';
-- Expected: 34
```

### V3 — Idempotency UNIQUE constraint exists
```sql
SELECT constraint_name, constraint_type
FROM information_schema.table_constraints
WHERE table_name = 'action_gateway' AND constraint_type = 'UNIQUE';
-- Expected: ag_idempotency_unique
```

### V4 — Self-referential FK on rollback_action_id
```sql
SELECT kcu.column_name, ccu.table_name AS references_table
FROM information_schema.key_column_usage kcu
JOIN information_schema.referential_constraints rc
  ON kcu.constraint_name = rc.constraint_name
JOIN information_schema.constraint_column_usage ccu
  ON rc.unique_constraint_name = ccu.constraint_name
WHERE kcu.table_name = 'action_gateway'
  AND kcu.column_name = 'rollback_action_id';
-- Expected: references_table = 'action_gateway'
```

### V5 — Append-only RULES on transitions table
```sql
SELECT rulename, tablename
FROM pg_rules
WHERE tablename = 'action_gateway_transitions'
ORDER BY rulename;
-- Expected: no_delete_action_gateway_transitions, no_update_action_gateway_transitions
```

### V6 — All CHECK constraints present
```sql
SELECT constraint_name
FROM information_schema.table_constraints
WHERE table_name = 'action_gateway' AND constraint_type = 'CHECK'
ORDER BY constraint_name;
-- Expected 9 rows:
--   ag_approval_decision_mutex
--   ag_attempt_lte_max
--   ag_attempt_nonneg
--   ag_irreversible_no_rollback
--   ag_max_attempts_range
--   ag_risk_level_check
--   ag_rollback_flag_integrity
--   ag_rollback_timestamp_integrity
--   ag_state_check
```

### V7 — Index count on action_gateway (11 = 10 explicit + 1 from UNIQUE)
```sql
SELECT COUNT(*) AS index_count
FROM pg_indexes
WHERE tablename = 'action_gateway';
-- Expected: 11
```

### V8 — RLS enabled on both tables
```sql
SELECT relname, relrowsecurity
FROM pg_class
WHERE relname IN ('action_gateway', 'action_gateway_transitions');
-- Expected: relrowsecurity = true for both
```

### V9 — Smoke test: SAFE action round-trip (replace UUIDs with real values)
```sql
-- Insert a test SAFE action
INSERT INTO action_gateway (
    case_id, ticket_id, client,
    action_type, action_namespace, risk_level,
    current_state, action_payload, proposed_by,
    approval_required, approver, approved_at,
    expires_at, execution_attempt, max_attempts,
    idempotency_key
) VALUES (
    '00000000-0000-0000-0000-000000000001',  -- replace with real case_id
    'TKT-SMOKETEST', 'unity_bank',
    'POST_RESOLUTION_NOTE', 'freshdesk', 'SAFE',
    'APPROVED', '{"note": "test"}'::jsonb, 'system',
    FALSE, 'auto_approval', NOW(),
    NULL, 0, 3,
    'smoketest-' || gen_random_uuid()::text
)
RETURNING action_id, current_state, idempotency_key;

-- Then insert a transition record
INSERT INTO action_gateway_transitions (
    action_id, case_id, ticket_id, client,
    from_state, to_state, actor, reason, detail
)
SELECT action_id, case_id, ticket_id, client,
       'PROPOSED', 'APPROVED', 'auto_approval', 'auto_approved_safe',
       '{"risk_level": "SAFE"}'::jsonb
FROM action_gateway WHERE ticket_id = 'TKT-SMOKETEST';

-- Verify append-only: this UPDATE should be silently ignored
UPDATE action_gateway_transitions SET actor = 'tampered'
WHERE action_id IN (SELECT action_id FROM action_gateway WHERE ticket_id = 'TKT-SMOKETEST');

SELECT actor FROM action_gateway_transitions
WHERE action_id IN (SELECT action_id FROM action_gateway WHERE ticket_id = 'TKT-SMOKETEST');
-- Expected: actor = 'auto_approval' (UPDATE was silently discarded by RULE)

-- Clean up
DELETE FROM action_gateway WHERE ticket_id = 'TKT-SMOKETEST';
-- Expected: 0 rows deleted (action_gateway_transitions FK RESTRICT prevents deletion)
-- To clean up: delete transitions first, then action (or just leave the test row)
```

## Sprint 2.2 Readiness

The persistent schema enables the following Sprint 2.2 components:

| Component | Reads | Writes |
|---|---|---|
| Executor worker | `idx_ag_approved_queue` — FIFO pickup | `begin_execution()`, `record_success()`, `record_failure()` |
| SLA watchdog | `idx_ag_expires_at_sweep` — expired actions | `expire()` |
| Stuck-execution watchdog | `idx_ag_executing` + `executor_id` | `record_timeout()` |
| Approval API (HTTP) | `idx_ag_pending_approval` — pending list | `approve()`, `reject()` |
| Compliance dashboard | `idx_ag_client_state_time` | (read-only) |
| SOC audit | `idx_agt_client_time`, `idx_agt_action_id_time` | (read-only) |

**Blocking item before Sprint 2.2**: Update `ActionRequest.to_db_row()` and `from_db_row()` to use the new column names. The 8 xfail tests in `test_sprint215_schema_contract.py` are the acceptance criteria.

**Idempotency UniqueViolation handling**: `ActionRepository.insert_action()` must catch the `UniqueViolation` from the `ag_idempotency_unique` constraint and raise `DuplicateActionError`. This is required before the gateway can handle concurrent proposals safely at the DB level.
