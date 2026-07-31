# Sprint 1.2 Schema Consistency and Production Readiness Audit

**Date:** 2026-06-02  
**Branch:** sprint0-singleton-stabilization  
**Auditor:** Claude Code (automated)  
**Trigger:** S1_004 deployment failure — `foreign key constraint cannot be implemented` (uuid vs text)

---

## Executive Summary

A schema drift was discovered when S1_004 (`security_compliance_audit`) was applied to the Supabase instance. The migration failed because `cases.case_id` was declared as `TEXT` in S1_001, while S1_004's FK correctly declared it as `UUID`. PostgreSQL cannot build a FK from a UUID column to a TEXT primary key.

**Root cause:** S1_001 originally used `gen_random_uuid()::TEXT` to generate UUIDs but stored them as TEXT, making the column type TEXT. All three base tables (S1_001, S1_002, S1_003) were affected.

**Resolution:** All three migration files were rewritten with correct UUID types. A rectification migration (S1_006) was created for existing deployments. The Python model layer required no code changes — supabase-py returns UUID columns as strings, and `str(uuid.uuid4())` is accepted by PostgreSQL UUID columns via implicit coercion.

**Sprint 2 Readiness Verdict: CONDITIONAL GO** — go on Python code; apply SQL migrations before connecting any Sprint 2 components to the database.

---

## Schema Consistency Report

### Canonical Type Policy (established Sprint 1.2)

| Column class | Type | Rationale |
|---|---|---|
| Internal system IDs (`case_id`, `transition_id`, `audit_id`) | **UUID** | Native 128-bit type; FK comparisons are type-safe; pgvector/PostgREST handle natively |
| External identifiers (`ticket_id`, `client`, `actor`) | **TEXT** | Freshdesk IDs may be numeric strings; tenant slugs are human-readable; format not controlled by us |
| Idempotency / correlation keys | **TEXT** | SHA-256 hex string; caller-provided format; no FK target |
| Timestamps | **TIMESTAMPTZ** | Timezone-aware throughout |

### Before / After comparison

| Table | Column | Before (S1_001–S1_003 original) | After (S1_001–S1_003 rewritten) |
|---|---|---|---|
| `cases` | `case_id` | `TEXT DEFAULT gen_random_uuid()::TEXT` | `UUID DEFAULT gen_random_uuid()` |
| `case_transitions` | `transition_id` | `TEXT DEFAULT gen_random_uuid()::TEXT` | `UUID DEFAULT gen_random_uuid()` |
| `case_transitions` | `case_id` | `TEXT NOT NULL` | `UUID NOT NULL` |
| `case_audit_log` | `audit_id` | `TEXT DEFAULT gen_random_uuid()::TEXT` | `UUID DEFAULT gen_random_uuid()` |
| `case_audit_log` | `case_id` | `TEXT NOT NULL` | `UUID NOT NULL` |
| `case_audit_log` | `action_detail` | `JSONB` (nullable) | `JSONB NOT NULL DEFAULT '{}'::jsonb` |
| `security_compliance_audit` | `audit_id` | `UUID` (already correct) | no change |
| `security_compliance_audit` | `case_id` | `UUID REFERENCES cases(case_id)` (already correct) | no change |

### Python model layer — no changes needed

`Case.case_id`, `CaseTransition.transition_id`, and `AuditEntry.audit_id` are all generated via `str(uuid.uuid4())` in `models.py:_new_id()`. These produce valid UUID v4 strings. PostgreSQL's UUID column type accepts such strings via implicit coercion in both the PostgREST JSON API and the Python `supabase-py` client. UUID columns are returned as strings in JSON responses, which `from_db_row()` already handles.

---

## Foreign Key Audit Report

| FK constraint | From | To | ON DELETE | Status |
|---|---|---|---|---|
| `case_transitions_case_id_fkey` | `case_transitions.case_id UUID` | `cases.case_id UUID` | CASCADE | CORRECT (rewritten) |
| `security_compliance_audit.case_id` (unnamed) | `security_compliance_audit.case_id UUID` | `cases.case_id UUID` | SET NULL | CORRECT (was blocked; unblocked after S1_006) |
| `case_audit_log.case_id` | none — intentional | — | — | CORRECT (audit outlives cases; no FK by design) |

**No phantom or missing FKs found.** The only FK issue was the type mismatch, now resolved.

**case_audit_log.case_id design rationale (preserved):** The audit log must survive case deletion for DPDP/GDPR compliance reporting. A FK with ON DELETE CASCADE would destroy audit records. A FK with ON DELETE SET NULL would lose the case linkage for reconstruction. UUID with no FK preserves both the audit record and the (now-orphaned) case reference.

---

## Index Audit Report

### `cases` table

| Index | Columns | Partial | Purpose |
|---|---|---|---|
| `idx_cases_ticket_client` | `(ticket_id, client)` | no | Webhook retry lookup — get existing case for a ticket |
| `idx_cases_client_state` | `(client, current_state)` | `WHERE closed_at IS NULL` | Level 1 dashboard, Level 2 queue watcher |
| `idx_cases_current_state` | `(current_state)` | `WHERE closed_at IS NULL` | Escalation queue monitoring |
| `idx_cases_created_at` | `(created_at DESC)` | no | Age-based dashboard ordering |
| `idx_cases_sla_breach` | `(sla_breach_at)` | `WHERE sla_breach_at IS NOT NULL` | Sprint 2 SLA watchdog |

**Improvement vs original:** S1_005's `cases_client_state_idx` was a plain `(client, current_state)` without the `WHERE closed_at IS NULL` partial filter. The rewritten S1_001 uses a partial index, which is ~80% smaller on a production queue where most cases are closed.

### `case_transitions` table

| Index | Columns | Partial | Purpose |
|---|---|---|---|
| `idx_case_transitions_case_id_time` | `(case_id, created_at DESC)` | no | Per-case timeline queries |
| `idx_case_transitions_to_state` | `(to_state, created_at DESC)` | no | Find cases that reached a specific state |
| `idx_case_transitions_created_at` | `(created_at DESC)` | no | Audit time-range queries |

**Improvement vs original:** Added composite `(case_id, created_at DESC)` so "show all transitions for case X in order" is a single index scan, not a filter + sort.

### `case_audit_log` table

| Index | Columns | Partial | Purpose |
|---|---|---|---|
| `idx_case_audit_case_id_time` | `(case_id, event_timestamp DESC)` | no | Per-case event timeline |
| `idx_case_audit_ticket_id` | `(ticket_id, event_timestamp DESC)` | no | Freshdesk ticket lookup |
| `idx_case_audit_client_time` | `(client, event_timestamp DESC)` | no | Per-client compliance reporting |
| `idx_case_audit_action_time` | `(action_type, event_timestamp DESC)` | no | Event-type filtering |
| `idx_case_audit_timestamp` | `(event_timestamp DESC)` | no | Global time-range queries |
| `idx_case_audit_idempotency` | `(idempotency_key)` | `WHERE idempotency_key IS NOT NULL` | Dedup lookup for action execution events |

**Improvement vs original:** Added `idx_case_audit_case_id_time` composite index (missing in original S1_003). Added partial `idx_case_audit_idempotency` for dedup lookups.

---

## Changes Made

### SQL Migrations — rewritten

| File | Change |
|---|---|
| `S1_001_cases.sql` | `case_id TEXT` → `UUID`; consolidated `sla_breach_at` from S1_005; improved partial indexes |
| `S1_002_case_transitions.sql` | `transition_id TEXT` → `UUID`; `case_id TEXT` → `UUID`; composite index on `(case_id, created_at DESC)` |
| `S1_003_case_audit_log.sql` | `audit_id TEXT` → `UUID`; `case_id TEXT` → `UUID`; `action_detail` → NOT NULL DEFAULT '{}'; composite + idempotency indexes |

### SQL Migrations — created new

| File | Purpose |
|---|---|
| `S1_006_uuid_rectification.sql` | Idempotent rectification for existing deployments; 6 conditional DO $$ steps; verify query included |

### Python code — no changes

The model layer (`models.py`) already generates valid UUID strings via `str(uuid.uuid4())`. No `from_db_row()` or `to_db_row()` changes were needed. supabase-py handles the UUID ↔ string coercion transparently.

---

## Tests Added

| File | Count | Coverage |
|---|---|---|
| `tests/test_sprint12_schema_consistency.py` | 30 | UUID format validation for all system IDs; TEXT for all external IDs; `to_db_row`/`from_db_row` round-trips; canonical type policy end-to-end via CaseService |

**Test categories:**
- `TestCaseUUID` (14 tests) — Case model IDs, ticket_id TEXT, round-trips, supabase-py string format
- `TestCaseTransitionUUID` (5 tests) — CaseTransition system IDs
- `TestAuditEntryUUID` (10 tests) — AuditEntry IDs, action_detail not-None guarantee
- `TestCanonicalTypePolicy` (5 tests) — End-to-end via CaseService; UUID v4 version check; uniqueness

**Running:**
```bash
pytest tests/test_sprint12_schema_consistency.py -v
```

---

## Remaining Risks

| Risk | Severity | Owner | Mitigation |
|---|---|---|---|
| S1_001–S1_003 already applied with TEXT types in Supabase | HIGH | DevOps | Apply S1_006 before S1_004 (S1_006 is idempotent) |
| S1_004 FK was never successfully applied | HIGH | DevOps | Apply S1_004 after S1_006 completes |
| Supabase service-role key in git history | CRITICAL | DevOps | Rotate key immediately (old key in deleted `supabasesuccess.py`) |
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` not set in prod | HIGH | DevOps | Set env var to `true` before any live traffic |
| S1_005 `cases_client_state_idx` is suboptimal (no partial filter) | LOW | DevOps | S1_001 rewrite adds the correct partial index; run `REINDEX` on existing deployment |

---

## Migration Application Order

### Fresh deployment (never applied S1_001–S1_005)

```
S1_001_cases.sql
S1_002_case_transitions.sql
S1_003_case_audit_log.sql
S1_004_security_compliance_audit.sql
S1_005_operational_telemetry.sql   ← sla_breach_at ADD COLUMN IF NOT EXISTS (no-op; already in S1_001)
```

S1_005 is now effectively a no-op on fresh deployments since `sla_breach_at` is in the rewritten S1_001. It remains safe to apply (all statements use `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`).

### Existing deployment (S1_001–S1_003 applied with TEXT types; S1_004 failed; S1_005 applied)

```
S1_006_uuid_rectification.sql      ← converts TEXT → UUID idempotently
S1_004_security_compliance_audit.sql  ← now succeeds (cases.case_id is UUID)
```

---

## Sprint 2 Readiness Verdict

**CONDITIONAL GO**

| Check | Status | Notes |
|---|---|---|
| Python model layer | GO | No changes needed; UUID strings work transparently |
| SQL schema (fresh deployment) | GO | S1_001–S1_005 in correct order gives correct schema |
| SQL schema (existing deployment) | CONDITIONAL | Apply S1_006 then S1_004 before connecting Sprint 2 components |
| FK integrity | GO after migrations | case_transitions FK correct; security_compliance_audit FK will work after S1_006+S1_004 |
| Audit log design | GO | case_audit_log.case_id intentionally has no FK — preserved and documented |
| Sprint 2 LangGraph state machine | GO | CaseState enum, ALLOWED_TRANSITIONS, and TERMINAL_STATES are Sprint 2 ready |
| Action Gateway table (Sprint 2) | NOT YET | action_gateway table not yet defined; will reference cases.case_id UUID correctly |
| Temporal saga tables (Sprint 2) | NOT YET | saga_checkpoints not yet defined |

**Blocking action for Sprint 2 start:** Apply S1_006 + S1_004 to the Supabase instance and verify via the SELECT query at the bottom of S1_006 that all UUID columns show `data_type = 'uuid'`.
