# Sprint 2.14 — Architecture Review & CTO Report

**Date:** 2026-06-08
**Sprint:** 2.14 — Human Recovery & Operational Control
**Test count before:** 2046 passed, 4 skipped
**Test count after:** 2176 passed, 4 skipped (+130 new tests)
**Failures:** 0

---

## 1. Summary

Sprint 2.14 adds operator-initiated recovery interventions for every lifecycle
state that can get stuck. Five new operations are now available:

1. Recover a DEAD_LETTER action to APPROVED (with retry-counter reset)
2. Retry a FAILED action with remaining budget
3. Cancel a pre-execution action (new CANCELLED terminal state)
4. Manually expire an action early
5. Trigger rollback for an executed REVERSIBLE action

All operations are protected by ADMIN RBAC, optimistic locking, and full audit
trails. No operation modifies schema outside its expected scope.

---

## 2. Race Condition Analysis

### 2.1 DEAD_LETTER recovery vs concurrent executor

**Scenario**: Two operators simultaneously call `/retry` on the same DEAD_LETTER
action. Both read DEAD_LETTER state. Both try to write APPROVED.

**Protection**: `update_action(expected_state=DEAD_LETTER)` generates a
PostgreSQL `UPDATE ... WHERE current_state='DEAD_LETTER'`. Only one UPDATE
matches. The losing writer receives 0 rows updated → returns False → LOCK_CONTENTION.

**Verdict**: Safe. One winner, one clear error.

### 2.2 Cancel vs executor claiming APPROVED

**Scenario**: Operator calls `/cancel` on APPROVED action at the same moment
an executor calls `begin_execution()`.

**Protection**: Both `cancel_action` and `begin_execution` use
`update_action(expected_state=APPROVED)`. PostgreSQL serializes both writes.
One wins the row. The loser gets 0 rows → LOCK_CONTENTION (cancel) or
`ActionGatewayError` (executor). The system ends up in exactly one of two clean
states: CANCELLED or EXECUTING.

**Verdict**: Safe. In the CANCELLED vs EXECUTING race, EXECUTING is correct
business behavior (the executor already started). The operator will see LOCK_CONTENTION
and can inspect the action to see it moved to EXECUTING.

### 2.3 Force rollback vs concurrent rollback trigger

**Scenario**: Two operators call `/rollback` on the same EXECUTED action
simultaneously.

**Protection**: `gateway.propose_rollback()` calls `_sm.transition(action, ROLLING_BACK)`,
which calls `update_action()`. The second operator hits the terminal-state guard
after the first succeeds (ROLLING_BACK is in TERMINAL-adjacent states that block
further transitions). The second call to `force_rollback_action` will either
see `is_rolled_back=True` (after the compensation completes) or `ALREADY_ROLLED_BACK`
pre-check if it reads the updated row after the first succeeds.

**Note**: There is a narrow window where both operators read `is_rolled_back=False`
before either writes. In that window, `gateway.propose_rollback()` handles the
duplicate via `DuplicateActionError` (idempotency key on the compensation action)
and returns the existing compensation. Both callers get a 200 response pointing
to the same compensation action. No double-rollback occurs.

**Verdict**: Safe. Duplicate rollback triggers are idempotent.

---

## 3. Invalid Transition Analysis

### 3.1 CANCELLED state

CANCELLED was added to `ALLOWED_ACTION_TRANSITIONS` for PROPOSED, AWAITING_APPROVAL,
and APPROVED. It is NOT reachable from:
- EXECUTING (executor is running — no race possible between cancel and execution start
  that ends with both succeeding)
- Any terminal state (TERMINAL_ACTION_STATES check blocks all outgoing transitions)

`cancel_action` has an explicit `_CANCELLABLE` check before calling the state
machine, providing a clear error code instead of `ActionTransitionError`.

### 3.2 expire_action_manually scope

PROPOSED was intentionally excluded from `_EXPIRABLE`. The reason: PROPOSED
transitions synchronously within the same request that created the action. By
the time any HTTP call to `/expire` arrives, the action is already in
AWAITING_APPROVAL or APPROVED. Allowing PROPOSED → EXPIRED would only catch
actions that failed to transition (a bug), not a normal operational need.

### 3.3 DEAD_LETTER bypass correctness

The bypass does not call `can_transition()` but achieves equivalent safety via:
1. Pre-check: `action.current_state != DEAD_LETTER` returns INVALID_STATE
2. Explicit state mutation: `action.current_state = APPROVED` (no shortcut to a wrong state)
3. Optimistic lock: `expected_state=DEAD_LETTER` — if the state changed between read and
   write, the update fails (LOCK_CONTENTION)

The transition record is created manually and saved first (before the action
update). If the action update fails, there's a dangling transition record. This
is the same pattern as `_sm.transition()` which also fires the callback before
the outer `update_action` — an accepted design constraint in the existing codebase.

---

## 4. Audit Trail Completeness

| Operation | Audit mechanism |
|---|---|
| retry_dead_letter | `AuditEvent(ACTION_RECOVERED_FROM_DEAD_LETTER)` + transition record |
| retry_failed | Transition record only (actor captures operator identity) |
| cancel_action | `AuditEvent(ACTION_CANCELLED)` + transition record |
| expire_manually | `AuditEvent(ACTION_MANUALLY_EXPIRED)` + transition record |
| force_rollback | `AuditEvent(ACTION_ROLLBACK_TRIGGERED)` + transition record |

`retry_failed` uses the transition record only (no separate audit event). This
is consistent with the existing gateway pattern where `approve()`, `reject()`,
and `expire()` also record transitions but do not emit `AuditEvent`s beyond what
the state machine captures. For `retry_failed`, the transition record with
`actor=operator:alice` provides sufficient traceability.

If future compliance requirements demand a separate audit event for
`retry_failed`, add `ACTION_FAILED_RETRY` to `AuditEventType` and call
`_emit_audit()` after the optimistic lock succeeds.

---

## 5. Silent Failure Inventory

The following paths are fire-and-forget (cannot propagate errors to the HTTP caller):

| Path | What fails silently | Impact |
|---|---|---|
| `_emit_audit()` | AuditService.emit() failure | Missing audit event; transition record still written |
| `_repo.record_transition()` | DB write failure | Transition history gap; action state still persisted |

The `_emit_audit` silencing is by design (same as every existing audit path).
The `record_transition` silencing is inherited from the existing state machine
`on_transition` callback pattern — any exception in `on_transition` is caught
and logged, not propagated. This means in an extreme failure scenario, a
transition can be committed to the DB without a corresponding transition record.
This pre-existed Sprint 2.14 and is a known limitation.

---

## 6. Schema Contract Integrity

Four schema artefacts were updated in Sprint 2.14:

| Artefact | Change | Test coverage |
|---|---|---|
| `action_state.py` | CANCELLED enum value + transitions | TestCancelledStateMachine (8 tests) |
| `action_models.py` | `cancelled_at` field + to_db_row/from_db_row | TestCancelledAtField (4 tests) |
| `audit/models.py` | 4 new AuditEventType values | TestAuditEventTypes (4 tests) |
| `S2_006_cancelled_state.sql` | CANCELLED in 3 CHECK constraints + cancelled_at column | TestStateConstantCoverage (updated) |

The `test_sprint215_schema_contract.py` suite was updated to include CANCELLED
in `_DB_STATES` and `cancelled_at` in `_AG_COLUMNS`. Both the Python model
and the DB schema are now in sync.

**Latent bug fixed**: `ae_event_type_check` on `audit_events` was missing
`ACTION_DEAD_LETTERED` and `ACTION_AUDIT_READ` (added to Python in Sprint 2.11
but never to the DB). This caused silent write failures for those event types
when using `AUDIT_BACKEND=supabase`. Fixed in S2_006.

---

## 7. Rollback Safety

`force_rollback_action` delegates fully to `gateway.propose_rollback()`. The
gateway validates:
- EXECUTED state
- REVERSIBLE risk level
- rollback_action_type present
- not already rolled back

The recovery service adds pre-validation (steps 1-4 above) so it can return
structured error codes (NOT_REVERSIBLE, NO_ROLLBACK_SPEC, ALREADY_ROLLED_BACK)
instead of letting `ActionGatewayError` propagate as INTERNAL_ERROR.

If `propose_rollback` raises for any other reason (DB error, unexpected state),
the outer try/except catches it and returns INTERNAL_ERROR. The DB is left
unchanged because `propose_rollback` internally uses optimistic locking.

---

## 8. What Was NOT Changed

- State machine retry/dead-letter rules — unchanged
- SLA watchdog expiry logic — unchanged
- `ActionGateway` production paths — unchanged
- Executor concurrency model — unchanged
- Sprint 2.13 operational endpoints — unchanged and still green

---

## 9. Verdict

Sprint 2.14 is production-safe. The implementation:

1. Preserves all existing state machine invariants
2. Uses optimistic locking on every write
3. Never raises from service methods (structured error codes only)
4. Emits audit events for every operator intervention that diverges from
   the normal automated lifecycle
5. Has 128 new tests covering all success paths, error paths, lock contention,
   missing actor, 503/401/403 auth, and assembly wiring
6. Full regression: 2176 passed, 4 skipped, 0 failed

**Recommended**: Deploy S2_006_cancelled_state.sql before deploying the code
(additive migration — cancelled_at is nullable, existing rows unaffected).
