# Sprint 2.15 — Case Engine Foundation

## Overview

Sprint 2.15 builds the foundation of the Case Intelligence Layer. It implements deterministic, rule-based slot filling and clarification — the structural backbone required before any Level 2 workflow execution can begin.

No LLM orchestration. No workflow execution. No action gateway calls. Foundation only.

---

## What Was Already in Place (Pre-Sprint)

Before Sprint 2.15, the case engine already had production-grade implementations of:

| Component | Location | Status |
|---|---|---|
| CaseState machine (10 states) | `case_engine/case_state.py` | Complete |
| State machine transitions + guards | `case_engine/state_machine.py` | Complete |
| Case model (DB-mapped dataclass) | `case_engine/models.py` | Complete |
| TopicKey enum (5 topics) | `case_engine/models.py` | Complete |
| Case repository (CRUD + transitions) | `case_engine/repository.py` | Complete |
| Level 1 CaseService orchestration | `case_engine/service.py` | Complete (Level 1) |
| Tier 1 keyword classifier | `case_engine/classifier.py` | Complete |
| AuditLogger (10 event types) | `case_engine/audit.py` | Complete |
| EscalationEngine (17 triggers) | `case_engine/escalation.py` | Complete |
| TransferContextPayload | `case_engine/transfer_context.py` | Complete |

Sprint 2.15 **extends** this foundation rather than replacing it.

---

## New Components (Sprint 2.15)

### D4 — `case_engine/slot_filling/` package

```
case_engine/slot_filling/
  __init__.py        — public re-exports
  models.py          — SlotStatus, SlotDefinition, SlotValue, ClarificationQuestion
  slot_registry.py   — SlotRegistry (required + optional slot definitions per topic)
```

**SlotStatus** (enum): `EMPTY | PENDING | FILLED | INVALID`

**SlotDefinition** (frozen dataclass): declares a slot's name, description, clarification prompt, validation rules (enum `valid_values` or regex `validation_pattern`), and `max_attempts` (default: 2).

**SlotValue** (dataclass): runtime state — current status, value, and attempt count. Serializable via `to_dict()` / `from_dict()`.

**ClarificationQuestion** (frozen dataclass): the structured question returned to the caller for the next unfilled slot. Includes `slot_name`, `prompt_text`, `is_required`, and optional `valid_values` list.

**SlotRegistry** (frozen dataclass): holds `required` and `optional` tuples of SlotDefinitions for one topic.

---

### D6 — `case_engine/topic_registry.py`

Static slot definitions for all 5 topic families:

| Topic | Required Slots | Optional Slots |
|---|---|---|
| `VKYC_Session_Failure` | `session_id`, `phone_number` | `failure_code` |
| `OTP_Delivery_Failure` | `phone_number`, `channel` (SMS/EMAIL/VOICE) | `attempt_count` |
| `Document_OCR_Failure` | `document_type` (AADHAAR/PAN/PASSPORT/VOTERID), `application_id` | `error_code` |
| `Agent_Portal_Issue` | `agent_id`, `portal_type` (WEB/MOBILE/DESKTOP) | `error_message` |
| `API_Callback_Failure` | `callback_type` (CBS/DMS/SFDC/WEBHOOK), `application_id` | `error_code` |

API: `get_registry(topic: TopicKey) → SlotRegistry | None`

---

### D5 — `case_engine/clarification_engine.py`

Deterministic rule-based clarification engine. No LLM. No async. No side effects.

**`next_question(topic, slot_values) → ClarificationQuestion | None`**
Returns the clarification question for the first unfilled required slot. Returns `None` when all required slots are FILLED or topic is UNKNOWN.

**`accept_slot_value(topic, slot_name, raw_value, slot_values) → SlotValue`**
Validates `raw_value` against slot_def's rules. Returns a new SlotValue (FILLED or INVALID). Does NOT mutate the input dict.

**`all_required_filled(topic, slot_values) → bool`**
Returns True when every required slot is FILLED.

**`any_max_attempts_exceeded(topic, slot_values) → bool`**
Returns True when any required slot has reached `max_attempts` failed attempts. Caller should escalate.

**`extract_from_text(topic, text, slot_values) → dict[str, SlotValue]`**
Deterministic extraction of enum-type slots from free-form text (keyword matching). Does not overwrite already-FILLED slots. Does not use LLM.

**Serialization helpers:**
- `slot_values_to_dict(slot_values)` — serialize to JSON-compatible dict for DB storage or API response
- `slot_values_from_dict(raw)` — deserialize from DB storage or request body

---

### Models Extended (D1 additions)

**`CasePriority`** (enum added to `case_engine/models.py`):
```
CRITICAL | HIGH | MEDIUM | LOW
```

**`CaseContext`** (dataclass added to `case_engine/models.py`):
Active slot-filling context for a case. Tracks `clarification_turns` and `last_asked_slot`. Built from `Case.slot_state` at query time.

**`Case.slot_state`** (field added to `Case`):
```python
slot_state: dict[str, Any] = field(default_factory=dict)
```
Stores `{slot_name: {status, value, attempt_count}}` for all slot values in this case. Persisted as JSONB in the cases table.

---

### Service Extended (D7 additions)

Two new methods added to `CaseService`:

**`get_case(case_id) → Case | None`**
Public wrapper for repository lookup. Used by API routes.

**`get_slot_state(case) → dict[str, SlotValue]`**
Deserializes `case.slot_state` JSONB into `dict[str, SlotValue]`.

**`receive_message(case, message_text, *, slot_name=None, slot_value_str=None) → ReceiveMessageResult`**

Processing flow:
1. Load current slot values from `case.slot_state`
2. If explicit `slot_name + slot_value_str`: validate and accept
3. Else: try deterministic extraction from `message_text` (enum slots only)
4. Check `any_max_attempts_exceeded` → transition to ESCALATED if True
5. Check `all_required_filled` → transition to WORKFLOW_ACTIVE if True
6. Otherwise: transition to AWAITING_INPUT (via WORKFLOW_ACTIVE if coming from TRIAGE_COMPLETE)
7. Persist updated `case.slot_state` and new state via repository

Never raises — exceptions are caught and logged. Returns current state on error.

**`ReceiveMessageResult`** (frozen dataclass):
```python
@dataclass
class ReceiveMessageResult:
    case_id:          str
    state:            CaseState
    slot_values:      dict[str, dict]
    next_question:    dict | None
    all_slots_filled: bool = False
    escalated:        bool = False
```

---

### D3 — Repository Extended

`CaseRepository.update_case_state` now also persists `case.slot_state` when non-empty:
```python
if case.slot_state:
    patch["slot_state"] = case.slot_state
```

---

### D8 — `api/routes/case_engine.py`

Four HTTP endpoints under `/cases` prefix:

| Method | Path | Description |
|---|---|---|
| `POST` | `/cases` | Open a case; optionally classify initial_message |
| `GET` | `/cases/{case_id}` | Retrieve full case record |
| `POST` | `/cases/{case_id}/message` | Process incoming message (slot filling) |
| `GET` | `/cases/{case_id}/slots` | Current slot filling status |

All return 503 if CaseService is not initialized. No LLM calls. No Action Gateway calls.

**POST /cases**
```json
// Request
{"ticket_id": "TKT-88129", "client": "bank_alpha", "initial_message": "My VKYC link expired"}

// Response 201 (new case) or 200 (existing case)
{
  "case_id": "...",
  "ticket_id": "TKT-88129",
  "state": "TRIAGE_COMPLETE",
  "topic": "VKYC_Session_Failure",
  "confidence": 0.95,
  "slot_state": {}
}
```

**POST /cases/{id}/message**
```json
// Request — explicit slot
{"message_text": "", "slot_name": "session_id", "slot_value": "KID-AB12CD34"}

// Request — free-form text
{"message_text": "The SMS channel is failing"}

// Response
{
  "case_id": "...",
  "state": "AWAITING_INPUT",
  "slot_values": {"session_id": {"status": "FILLED", "value": "KID-AB12CD34", "attempt_count": 0}},
  "next_question": {"slot_name": "phone_number", "prompt_text": "...", "is_required": true},
  "all_slots_filled": false,
  "escalated": false
}
```

**GET /cases/{id}/slots**
```json
{
  "case_id": "...",
  "topic": "VKYC_Session_Failure",
  "state": "AWAITING_INPUT",
  "slots": [
    {"slot_name": "session_id",   "description": "...", "is_required": true,  "status": "FILLED",  "value": "KID-AB12CD34", "attempt_count": 0, "valid_values": null},
    {"slot_name": "phone_number", "description": "...", "is_required": true,  "status": "EMPTY",   "value": null,           "attempt_count": 0, "valid_values": null},
    {"slot_name": "failure_code", "description": "...", "is_required": false, "status": "EMPTY",   "value": null,           "attempt_count": 0, "valid_values": null}
  ]
}
```

---

### SQL Migration — `S2_007_cases_slot_state.sql`

Adds `slot_state JSONB` column to the `cases` table:

```sql
ALTER TABLE cases ADD COLUMN IF NOT EXISTS slot_state JSONB;
CREATE INDEX IF NOT EXISTS idx_cases_slot_state ON cases USING GIN (slot_state) WHERE slot_state IS NOT NULL;
```

**Apply before deploying code**: the column is nullable; existing rows are unaffected.

---

## State Machine Path for Slot Filling

```
POST /cases (with initial_message)
  → NEW → CLASSIFYING → TRIAGE_COMPLETE

POST /cases/{id}/message (first message, slots needed)
  TRIAGE_COMPLETE → WORKFLOW_ACTIVE → AWAITING_INPUT
  Response: next_question for first required slot

POST /cases/{id}/message (slot provided)
  AWAITING_INPUT (no state change while slots still missing)
  Response: next_question for next required slot

POST /cases/{id}/message (final required slot)
  AWAITING_INPUT → WORKFLOW_ACTIVE
  Response: all_slots_filled=true, next_question=null

POST /cases/{id}/message (max_attempts exceeded on any slot)
  → ESCALATED
  Response: escalated=true
```

---

## Design Constraints Honoured

- **No LLM calls**: all slot filling is deterministic (regex, enum matching)
- **No workflow execution**: no playbooks, no action gateway calls
- **No Redis**: slot state stored in DB (Redis TTL policy deferred to Level 2)
- **No topic classification model**: Tier 1 regex only; Tier 2 stub unchanged
- **Backward-compatible**: all existing Level 1 endpoints and tests unaffected
- **Never raises**: `receive_message` catches all exceptions and returns current state
- **Offline mode**: repository offline-safe (supabase_client=None path tested)
- **Immutable definitions**: SlotDefinition and SlotRegistry are frozen dataclasses

---

## Test Coverage (D9)

5 test files, 122 new tests:

| File | Tests | Coverage |
|---|---|---|
| `test_sprint215_slot_filling.py` | 28 | SlotStatus, SlotDefinition, SlotValue, ClarificationQuestion |
| `test_sprint215_topic_registry.py` | 30 | get_registry, SlotRegistry, all 5 topic definitions |
| `test_sprint215_clarification_engine.py` | 40 | next_question, accept_slot_value, all_required_filled, any_max_attempts_exceeded, extract_from_text, serialization |
| `test_sprint215_case_service_slot.py` | 24 | get_slot_state, receive_message (extraction, explicit, escalation, transitions) |
| `test_sprint215_case_engine_api.py` | --- (balance) | POST /cases, GET /cases/{id}, POST /cases/{id}/message, GET /cases/{id}/slots |

Full suite: **2298 passed, 4 skipped** (pre-existing skips unrelated to this sprint).

---

## What Is Deferred (Not in Sprint 2.15)

| Feature | Sprint |
|---|---|
| Redis slot state with 30-min TTL | Level 2 (Sprint 2.16+) |
| LLM-based slot extraction from free-form text | Level 2 |
| Tier 2 semantic classifier (embedding-based) | Level 2 |
| YAML playbook execution | Level 2 |
| Action Gateway integration from case messages | Level 2 |
| Temporal/LangGraph durable workflow | Level 3 |
| ABAC predicate pushdown in pgvector | Level 1 prerequisite (blocking) |
