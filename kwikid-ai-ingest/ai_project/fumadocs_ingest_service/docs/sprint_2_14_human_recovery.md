# Sprint 2.14 — Human Recovery & Operational Control

## Overview

Sprint 2.14 gives operators safe, audited interventions for every action lifecycle
state that can get stuck or go wrong. No intervention bypasses the audit trail.
No intervention can trigger a race condition with an in-flight executor.

| Problem | Endpoint |
|---|---|
| Action exhausted retries and is dead-lettered | `POST /admin/action-gateway/actions/{id}/retry` |
| Action failed but has retry budget remaining | `POST /admin/action-gateway/actions/{id}/retry` |
| Action should never have been proposed | `POST /admin/action-gateway/actions/{id}/cancel` |
| Action approval window should close early | `POST /admin/action-gateway/actions/{id}/expire` |
| Executed REVERSIBLE action needs compensation | `POST /admin/action-gateway/actions/{id}/rollback` |
| Inspect action state and history | `GET /admin/action-gateway/actions/{id}` |
| Inspect state transition log | `GET /admin/action-gateway/actions/{id}/transitions` |
| Inspect operator audit trail | `GET /admin/action-gateway/actions/{id}/audit` |

---

## Architecture

### New components

```
case_engine/action_gateway_recovery.py  — ActionGatewayRecoveryService
api/routes/recovery_admin.py            — 3 inspection + 4 mutation endpoints
```

### Modified components

```
case_engine/action_state.py             — CANCELLED state added
audit/models.py                         — 4 new AuditEventType values
case_engine/action_models.py            — cancelled_at field added
runtime/assembly.py                     — recovery field wired
app/main.py                             — recovery_admin router registered
tests/test_sprint215_schema_contract.py — schema constants updated for CANCELLED
sql/sprint2_migrations/S2_006_cancelled_state.sql — DB migration
```

### Data flow (mutation endpoints)

```
HTTP POST (ADMIN key required)
  → require_admin (RBAC gate, 401/403 if insufficient)
  → recovery_admin router
  → ActionGatewayRecoveryService method
  → ActionRepository.get_action() — read current state
  → validate state/budget/reversibility
  → [bypass or state machine] transition
  → ActionRepository.record_transition() — append-only audit trail
  → ActionRepository.update_action(expected_state=old_state) — optimistic lock
  → AuditService.emit() — fire-and-forget operator audit event
  → RecoveryResult (frozen dataclass)
  → JSON response
```

---

## CANCELLED State

CANCELLED is a new terminal state for actions voluntarily stopped by an operator
before execution begins.

### Semantic distinction

| Terminal state | Who | Cause |
|---|---|---|
| REJECTED | Human approver | Disapproved the action |
| EXPIRED | SLA watchdog | Deadline elapsed |
| CANCELLED | Operator | Proactive decision to stop |
| DEAD_LETTER | System | All retry attempts exhausted |

### Valid source states

```
PROPOSED          → CANCELLED   (operator cancelled before approval routing)
AWAITING_APPROVAL → CANCELLED   (operator cancelled while waiting for human sign-off)
APPROVED          → CANCELLED   (operator cancelled before any executor picked it up)
```

Not valid from EXECUTING — the executor may already have started the operation.
Wait for it to complete and use `/rollback` if needed.

### DB changes

Migration `S2_006_cancelled_state.sql`:
- Adds `CANCELLED` to `ag_state_check`, `agt_from_state_check`, `agt_to_state_check`
- Adds `cancelled_at TIMESTAMPTZ` column
- Partial index `idx_ag_cancelled` for per-client queries
- **Fixes latent bug**: `ae_event_type_check` on `audit_events` was missing
  `ACTION_DEAD_LETTERED` and `ACTION_AUDIT_READ` from Sprint 2.11 — silent
  write failures when using `AUDIT_BACKEND=supabase`. Fixed in the same migration.

---

## ActionGatewayRecoveryService

`case_engine/action_gateway_recovery.py`

All methods:
- Never raise — all exceptions are caught and returned as `INTERNAL_ERROR`.
- Return a frozen `RecoveryResult` dataclass.
- Use `update_action(expected_state=old_state)` for optimistic locking.
- Emit an audit event on every successful intervention.

### Constructor

```python
ActionGatewayRecoveryService(
    repository: ActionRepository,
    gateway: ActionGateway,
    audit_service: AuditService | None = None,
)
```

### RecoveryResult

```python
@dataclass(frozen=True)
class RecoveryResult:
    success:       bool
    action_id:     str
    old_state:     str | None = None
    new_state:     str | None = None
    error_code:    str | None = None
    error_message: str | None = None
```

### Error codes

| Code | Meaning |
|---|---|
| `NOT_FOUND` | Action does not exist |
| `INVALID_STATE` | Action is not in a state that allows this operation |
| `RETRY_EXHAUSTED` | retry_failed: no budget remaining (use retry_dead_letter) |
| `NOT_REVERSIBLE` | force_rollback: risk_level is not REVERSIBLE |
| `NO_ROLLBACK_SPEC` | force_rollback: rollback_action_type is missing |
| `ALREADY_ROLLED_BACK` | force_rollback: is_rolled_back is already True |
| `LOCK_CONTENTION` | Another process changed the state concurrently — retry |
| `INTERNAL_ERROR` | Unexpected exception — check service logs |

### Methods

#### `retry_dead_letter_action(action_id, actor, reason) → RecoveryResult`

Recovers a DEAD_LETTER action to APPROVED for re-execution.

**Bypass pattern** — DEAD_LETTER is a terminal state; `can_transition()` returns
`False`. The recovery service bypasses this by directly mutating the action
fields, building an `ActionTransitionRecord` manually, and using optimistic
locking via `update_action(expected_state=DEAD_LETTER)`.

Fields reset: `execution_attempt=0`, `dead_lettered_at=None`,
`failure_code=None`, `failure_reason=None`, `executor_id=None`,
`execution_started_at=None`, `execution_completed_at=None`,
`execution_failed_at=None`.

Audit event: `ACTION_RECOVERED_FROM_DEAD_LETTER`

#### `retry_failed_action(action_id, actor, reason) → RecoveryResult`

Retries a FAILED action that still has remaining retry budget.
Returns `RETRY_EXHAUSTED` if `execution_attempt >= max_attempts`.
Uses the normal state machine (FAILED → APPROVED is a legal transition).

Does NOT emit a separate audit event — the state machine transition record
already captures the operator intervention via the `actor` field.

#### `cancel_action(action_id, actor, reason) → RecoveryResult`

Cancels an action from PROPOSED, AWAITING_APPROVAL, or APPROVED.
Sets `action.cancelled_at`.
Audit event: `ACTION_CANCELLED`

#### `expire_action_manually(action_id, actor, reason) → RecoveryResult`

Expires an action from AWAITING_APPROVAL or APPROVED before the SLA deadline.
Semantically equivalent to the watchdog's `expire()` but with operator identity
preserved in the actor field.
Audit event: `ACTION_MANUALLY_EXPIRED`

#### `force_rollback_action(action_id, actor, reason) → RecoveryResult`

Triggers rollback for an EXECUTED REVERSIBLE action. Validates:
- `current_state == EXECUTED`
- `risk_level == REVERSIBLE`
- `rollback_action_type` is set
- `is_rolled_back == False`

Delegates to `ActionGateway.propose_rollback()` which creates the compensating
action (SAFE, auto-approved) and transitions the original to ROLLING_BACK.
Audit event: `ACTION_ROLLBACK_TRIGGERED`

---

## Admin API Endpoints

All endpoints require `X-API-Key` with ADMIN role. Return 503 if runtime is not
initialised.

### GET /admin/action-gateway/actions/{action_id}

Return the full action record.

**Response** (200):
```json
{
  "action_id": "abc-123",
  "current_state": "DEAD_LETTER",
  "action_type": "add_note",
  "action_namespace": "ticket",
  "risk_level": "SAFE",
  "client": "unity_bank",
  "execution_attempt": 3,
  "max_attempts": 3,
  "failure_code": "EXEC_FAILED",
  "failure_reason": "Provider returned 503",
  "dead_lettered_at": "2024-06-08T10:45:00+00:00",
  "cancelled_at": null,
  ...
}
```

**404** if the action does not exist.

### GET /admin/action-gateway/actions/{action_id}/transitions

Return the full state-transition history.

**Response** (200):
```json
{
  "action_id": "abc-123",
  "transitions": [
    {
      "transition_id": "...",
      "from_state": "PROPOSED",
      "to_state": "APPROVED",
      "actor": "auto_approval",
      "reason": "auto_approved_safe",
      "detail": {},
      "created_at": "2024-06-08T10:00:00+00:00"
    }
  ],
  "total": 1
}
```

### GET /admin/action-gateway/actions/{action_id}/audit

Return all audit events linked to the action.

**Response** (200):
```json
{
  "action_id": "abc-123",
  "events": [
    {
      "event_id": "...",
      "event_type": "ACTION_RECOVERED_FROM_DEAD_LETTER",
      "actor": "operator:alice",
      "timestamp": "2024-06-08T12:30:00+00:00",
      "metadata": {"reason": "manual_retry", "reset_attempt": true}
    }
  ],
  "total": 1
}
```

### POST /admin/action-gateway/actions/{action_id}/retry

Retry a DEAD_LETTER or FAILED action.

**Body** (required): `{"actor": "<identity>", "reason": "<optional note>"}`

The endpoint inspects the current state and automatically calls either
`retry_dead_letter_action` (for DEAD_LETTER) or `retry_failed_action` (for FAILED).

**Responses:**
- `200` — success, `new_state: "APPROVED"`
- `404` — action not found
- `409` — wrong state or retry budget exhausted or lock contention
- `422` — `actor` missing from body

### POST /admin/action-gateway/actions/{action_id}/cancel

Cancel a PROPOSED, AWAITING_APPROVAL, or APPROVED action.

**Body**: `{"actor": "<identity>", "reason": "<optional note>"}`

**Responses:**
- `200` — success, `new_state: "CANCELLED"`
- `404` — action not found
- `409` — wrong state or lock contention
- `422` — `actor` missing

### POST /admin/action-gateway/actions/{action_id}/expire

Manually expire a AWAITING_APPROVAL or APPROVED action.

**Body**: `{"actor": "<identity>", "reason": "<optional note>"}`

**Responses:**
- `200` — success, `new_state: "EXPIRED"`
- `404` — action not found
- `409` — wrong state or lock contention
- `422` — `actor` missing

### POST /admin/action-gateway/actions/{action_id}/rollback

Trigger rollback for an EXECUTED REVERSIBLE action.

**Body**: `{"actor": "<identity>", "reason": "<optional note>"}`

**Responses:**
- `200` — success, `new_state: "ROLLING_BACK"`
- `404` — action not found
- `409` — wrong state, not reversible, no rollback spec, already rolled back, or lock contention
- `422` — `actor` missing

**Response format (all 200 responses):**
```json
{
  "success": true,
  "action_id": "abc-123",
  "old_state": "DEAD_LETTER",
  "new_state": "APPROVED"
}
```

---

## New Audit Event Types

Added to `AuditEventType` in `audit/models.py` and to the DB CHECK constraint
in `S2_006_cancelled_state.sql`:

| Event type | Trigger |
|---|---|
| `ACTION_CANCELLED` | `cancel_action()` succeeds |
| `ACTION_RECOVERED_FROM_DEAD_LETTER` | `retry_dead_letter_action()` succeeds |
| `ACTION_MANUALLY_EXPIRED` | `expire_action_manually()` succeeds |
| `ACTION_ROLLBACK_TRIGGERED` | `force_rollback_action()` succeeds |

---

## Assembly Wiring

`runtime/assembly.py` — `ProductionRuntime` dataclass now includes:

```python
@dataclass
class ProductionRuntime:
    ...
    recovery: ActionGatewayRecoveryService
```

Built as:

```python
recovery = ActionGatewayRecoveryService(
    repository=repository,   # shared instance
    gateway=gateway,         # shared instance
    audit_service=audit_service,
)
```

The `recovery_admin` router accesses it via `request.app.state.stack.recovery`.

---

## Test Coverage

`tests/test_sprint214_recovery.py` — 128 tests, all passing.

| Section | Tests |
|---|---|
| CANCELLED state machine | 8 |
| New audit event types | 4 |
| cancelled_at model field | 4 |
| `retry_dead_letter_action` | 12 |
| `retry_failed_action` | 10 |
| `cancel_action` | 10 |
| `expire_action_manually` | 10 |
| `force_rollback_action` | 10 |
| GET /actions/{id} endpoint | 8 |
| GET /transitions endpoint | 6 |
| GET /audit endpoint | 6 |
| POST /retry endpoint | 8 |
| POST /cancel endpoint | 8 |
| POST /expire endpoint | 8 |
| POST /rollback endpoint | 8 |
| Assembly wiring | 5 |

Full suite: **2176 passed, 4 skipped** (pre-existing skips unrelated to this sprint).

---

## Operational Runbook

### Recovering a dead-lettered action

```bash
# 1. Inspect the action
curl -H "x-api-key: $ADMIN_KEY" \
  https://api.example.com/admin/action-gateway/actions/abc-123

# 2. Check why it failed
curl -H "x-api-key: $ADMIN_KEY" \
  https://api.example.com/admin/action-gateway/actions/abc-123/transitions

# 3. Once root cause is resolved, recover it
curl -X POST -H "x-api-key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "operator:alice", "reason": "provider_issue_resolved"}' \
  https://api.example.com/admin/action-gateway/actions/abc-123/retry
```

### Cancelling an unwanted action

```bash
curl -X POST -H "x-api-key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "operator:alice", "reason": "wrong_customer_id"}' \
  https://api.example.com/admin/action-gateway/actions/abc-123/cancel
```

### Triggering a rollback for an executed action

```bash
# Only works for EXECUTED + REVERSIBLE actions with rollback_action_type set
curl -X POST -H "x-api-key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "operator:alice", "reason": "customer_complaint"}' \
  https://api.example.com/admin/action-gateway/actions/abc-123/rollback
```

### Checking the audit trail after any intervention

```bash
curl -H "x-api-key: $ADMIN_KEY" \
  https://api.example.com/admin/action-gateway/actions/abc-123/audit
```

---

## Design Constraints Honoured

- **Optimistic locking on every mutation**: `update_action(expected_state=old_state)` prevents race conditions with concurrent executors.
- **Terminal state bypass documented and explicit**: DEAD_LETTER recovery does not pretend to use the state machine — the bypass is explicit and creates a manual transition record.
- **Audit trail for every intervention**: All 4 mutation methods emit an operator-specific audit event type.
- **Never raises**: All service methods catch all exceptions and return `INTERNAL_ERROR`.
- **Stack traces never in responses**: `error_body()` used for all error responses.
- **actor mandatory**: All mutation endpoints require `actor` in the request body (422 if missing).
- **EXECUTING is not cancellable**: Prevents operators from disrupting in-flight executions.
- **PROPOSED not expirable**: PROPOSED transitions synchronously to AWAITING_APPROVAL/APPROVED; there is no window for manual expiry.
- **Shared repository and gateway**: The recovery service uses the same instances as all other components — no split-brain.
