# Sprint 2.15 — Architecture Review & CTO Report

**Date:** 2026-06-08
**Sprint:** 2.15 — Case Engine Foundation
**Test count before:** 2176 passed, 4 skipped
**Test count after:** 2298 passed, 4 skipped (+122 new tests)
**Failures:** 0

---

## 1. Summary

Sprint 2.15 builds the structural backbone of the Case Intelligence Layer.
Five component groups were delivered:

1. **Slot Filling data model** (`case_engine/slot_filling/`) — SlotStatus, SlotDefinition,
   SlotValue, ClarificationQuestion, SlotRegistry. Immutable definitions; mutable runtime values.
2. **Topic Registry** (`case_engine/topic_registry.py`) — Static slot definitions for all
   5 topic families. `get_registry(topic)` is the single lookup interface.
3. **Clarification Engine** (`case_engine/clarification_engine.py`) — Deterministic,
   stateless engine. No LLM. No async. No side effects. Six public methods.
4. **Models + Service extensions** (`case_engine/models.py`, `case_engine/service.py`) —
   `CasePriority`, `CaseContext`, `Case.slot_state`, `CaseService.receive_message`,
   `CaseService.get_slot_state`, `CaseService.get_case`.
5. **Case Engine API** (`api/routes/case_engine.py`) — Four HTTP endpoints under `/cases`.

No LLM calls. No workflow execution. No Action Gateway calls. Foundation only.

---

## 2. Concurrent Write Analysis

Sprint 2.15 does not introduce background threads, SLA watchdogs, or executor
pools. All state mutations flow through a single synchronous HTTP request.
Concurrent-write risk is therefore low, but two scenarios are worth documenting:

### 2.1 Simultaneous messages for the same case

**Scenario**: Two calls to `POST /cases/{id}/message` arrive in parallel for the
same case_id (e.g., a retry from the frontend while the first request is still
in-flight).

**Protection**: Both calls deserialize `case.slot_state` from the same DB row
(via `get_case`). The second read reflects whatever the first write committed.
Because each call loads a fresh `Case` object from the DB, the winner's
`slot_state` is the one that reaches `update_case_state` last. There is no
optimistic locking on `slot_state` itself — this is an accepted limitation at
the foundation level. Redis-backed slot state with atomic TTL operations is
deferred to Sprint 2.16+.

**Verdict**: Low risk at the current traffic level. For a given support ticket,
only one agent is in conversation at a time. A mid-flight retry would either
write identical slot data or a corrected slot — both outcomes are safe. Document
and revisit when Redis is introduced.

### 2.2 open_case idempotency

**Scenario**: Freshdesk webhook is replayed — two calls to `POST /cases` with
the same `ticket_id` and `client`.

**Protection**: `CaseService.open_case` calls `_repo.get_case_by_ticket` first.
If a row exists, the existing Case is returned immediately without a second
`INSERT`. The second request returns HTTP 200 (existing case) instead of 201.

**Verdict**: Safe. Idempotent by design.

---

## 3. State Machine Validity

### 3.1 TRIAGE_COMPLETE → AWAITING_INPUT is not a valid edge

The existing state machine (`case_engine/state_machine.py`) defines:

```
TRIAGE_COMPLETE → {WORKFLOW_ACTIVE, ESCALATED, CLOSED}
WORKFLOW_ACTIVE → {AWAITING_INPUT, ACTION_PENDING, ESCALATED, CLOSED}
```

Direct `TRIAGE_COMPLETE → AWAITING_INPUT` is blocked. `receive_message` was
initially written with a single transition call, which surfaced this error
during the test run. The fix is a two-step sequence:

```python
if case.current_state == CaseState.TRIAGE_COMPLETE:
    self._sm.safe_transition(case, CaseState.WORKFLOW_ACTIVE, reason="slot_filling_started")
if case.current_state not in (CaseState.AWAITING_INPUT, CaseState.ESCALATED, CaseState.CLOSED):
    self._sm.safe_transition(case, CaseState.AWAITING_INPUT, reason="awaiting_slot_input")
```

This honours the architectural intent: slot filling is a sub-state of
WORKFLOW_ACTIVE, not a branch of TRIAGE.

### 3.2 Re-entrancy for AWAITING_INPUT

When a case is already AWAITING_INPUT and a new message arrives, the second
`if` block above is a no-op (case is already in the exclusion set). No spurious
transition is attempted. Confirmed by `test_already_in_awaiting_input_stays`.

### 3.3 WORKFLOW_ACTIVE when all slots are filled

When `all_required_filled` returns True and the case is not already
WORKFLOW_ACTIVE, `receive_message` transitions to WORKFLOW_ACTIVE. If the case
is already WORKFLOW_ACTIVE (e.g., a subsequent message that completes the last
slot), the guard `if case.current_state != CaseState.WORKFLOW_ACTIVE` prevents
a self-transition error.

---

## 4. Audit Trail Completeness

| Event | Audit mechanism |
|---|---|
| Slot accepted (explicit) | `audit.log_slot_filled(case, slot_name, value_hash, turn_count)` |
| Slot validated from text | No audit event — extraction is read-only inference, not an assertion |
| Max attempts exceeded | `_sm.safe_transition(case, ESCALATED)` → `on_transition` → transition record |
| All slots filled | `_sm.safe_transition(case, WORKFLOW_ACTIVE)` → transition record |
| Topic not yet classified | No-op return; caller (webhook handler) owns the classification audit |

`log_slot_filled` hashes the value before logging to avoid storing PII in the
audit table. The hash is only for deduplication and traceability — no plaintext
slot values enter the audit log.

There is no `log_slot_invalid` event. An INVALID slot is a user input error, not
a system event. Traceability for repeated failures is captured via the
`attempt_count` field in `case.slot_state` (persisted to DB) and the escalation
audit entry that fires when `max_attempts` is exceeded.

---

## 5. Silent Failure Inventory

The following paths are explicitly non-raising:

| Path | Failure mode | Impact |
|---|---|---|
| `receive_message` outer try/except | Any exception inside message processing | Returns current case state; logs exception at ERROR level; slot state may be partially written |
| `CaseService.get_case` | `_repo.get_case` DB error | Returns None; API route returns 404 (correct behaviour for offline mode) |
| `_repo.update_case_state` | DB write failure | Transition committed in memory; state not persisted; next webhook reload will restore state from DB |
| `_audit.log_slot_filled` | Audit service failure | Missing audit record; slot is still marked FILLED in case.slot_state |

The `receive_message` catch-all is by design: a slot-filling error must never
block the Level 1 RAG response pipeline. The trade-off is that a bug inside
`receive_message` is invisible to the HTTP caller — it sees the pre-error case
state with `next_question=None`. Structured error typing (`ReceiveMessageError`)
was considered but deferred to Level 2 when the failure modes are better
understood.

---

## 6. Schema Contract Integrity

One schema artefact was added in Sprint 2.15:

| Artefact | Change | Test coverage |
|---|---|---|
| `S2_007_cases_slot_state.sql` | `slot_state JSONB` column + GIN index on `cases` | `TestGetCase.test_response_contains_slot_state` (round-trip via API) |
| `case_engine/models.py` | `Case.slot_state` field, `to_db_row`/`from_db_row` serialization | `TestGetSlotState.*` (2 tests), `TestReceiveMessageStateTransitions.test_slot_state_updated_on_case` |

**Migration is additive**: `slot_state JSONB` is nullable. All existing rows
have `NULL` (treated as `{}` by `from_db_row`). No backfill required.

**No breaking changes** to any existing column, constraint, or index. The GIN
index covers only non-NULL rows (`WHERE slot_state IS NOT NULL`), so it adds no
overhead for rows that have not yet entered slot filling.

The Python `Case.slot_state` field defaults to `{}`. `to_db_row` writes `None`
when the dict is empty (saves JSONB storage on unclassified cases).
`from_db_row` coerces `None` → `{}` on read. These two transforms are inverses.

---

## 7. Slot Validation Invariants

### 7.1 Immutability of definitions

`SlotDefinition` and `SlotRegistry` are frozen dataclasses. `valid_values` is a
`frozenset`. `required` and `optional` tuples in `SlotRegistry` are tuples (not
lists). A caller cannot accidentally mutate topic definitions at runtime.

### 7.2 accept_slot_value never mutates the input dict

`accept_slot_value` creates a new `SlotValue` and returns it. The caller is
responsible for writing it back into `slot_values[slot_name]`. This prevents
accidental aliasing when callers inspect the dict before and after acceptance.

### 7.3 extract_from_text does not overwrite FILLED slots

If a slot is already `FILLED`, `extract_from_text` skips it regardless of what
appears in the text. This prevents a later free-form message from silently
overwriting a value that was explicitly accepted in a prior turn.

### 7.4 Escalation threshold

`any_max_attempts_exceeded` returns True when `sv.attempt_count >= slot_def.max_attempts`
(default: 2). The check fires after `accept_slot_value` increments `attempt_count`
on a failed validation. So: first invalid → count=1 (no escalation), second
invalid → count=2 (escalation triggered). This matches the architecture spec
"slot validation failure after 2 attempts → ESCALATED."

---

## 8. What Was NOT Changed

- All existing Level 1 endpoints (`/webhook`, `/chat`, `/train`, `/suggestions`) — unchanged
- All Action Gateway endpoints (Sprints 2.10–2.14) — unchanged
- `case_engine/case_state.py` state definitions — unchanged
- `case_engine/state_machine.py` transition table — unchanged
- `case_engine/classifier.py` — unchanged (Tier 1 keyword classifier only)
- `case_engine/escalation.py` — unchanged (17 trigger definitions)
- `case_engine/audit.py` — unchanged (two methods added for slot events only: `log_slot_filled`)
- `case_engine/repository.py` — one-line addition to `update_case_state` only (`slot_state` patch)
- Sprint 2.11–2.14 test suites — all still green

---

## 9. Gap Analysis Verdict

Before writing any code, a complete review of `Big_Phase_2_Documentations/`
was performed and compared against the existing codebase. Key finding:

**D1–D3 and D7 already existed under their production names** (`models.py`,
`state_machine.py`, `repository.py`, `service.py`). The correct action was to
extend these files rather than create parallel `case_models.py`, `case_state_machine.py`
etc., which would have introduced duplication and import ambiguity.

Truly new components (D4, D5, D6, D8) were created in new files/packages with
no pre-existing equivalents.

---

## 10. Verdict

Sprint 2.15 is production-safe for the foundation level.

1. Zero LLM calls, zero workflow execution, zero Action Gateway calls in this layer
2. All slot validation is deterministic — no probabilistic failures
3. `receive_message` never raises — worst case is a logged exception and an empty `next_question`
4. All state machine transitions honour the existing guard table — no bypasses
5. The slot_state JSONB column is additive and nullable — safe to deploy schema first
6. 122 new tests cover all success paths, invalid inputs, escalation, state transitions, and API contract
7. Full regression: 2298 passed, 4 skipped, 0 failed

**Deployment order:**

1. Apply `sql/sprint2_migrations/S2_007_cases_slot_state.sql` to the production DB
2. Deploy the updated Python code
3. No restart of existing services required — `app.state.case_service = None` initial
   state ensures a safe cold-start until the lifespan hook initialises the service

**What is deferred and why:**

Redis slot state (Sprint 2.16+) — DB-backed JSONB is sufficient for the current
single-agent-per-ticket assumption. Redis TTL and atomic CAS are needed only when
concurrent slot updates become possible (Level 2 durable workflows).

LLM-based slot extraction (Sprint 2.16+) — deterministic enum and regex matching
covers the structured slot types in all 5 topic families. Free-form text (e.g.,
natural-language `session_id`) requires LLM extraction, deferred until the
Level 2 LLM orchestration layer is in place.
