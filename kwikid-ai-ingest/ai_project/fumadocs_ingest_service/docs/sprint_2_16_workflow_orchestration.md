# Sprint 2.16 — Workflow Orchestration Foundation

## Overview

Sprint 2.16 introduces the deterministic workflow execution layer for the KwikID Enterprise Support Agent. It bridges the gap between slot filling (Sprint 2.15) and automated action execution (Action Gateway), enabling end-to-end ticket resolution through governed, auditable playbook execution.

**Architecture philosophy:** This is NOT an LLM orchestrator. Every transition is defined by YAML playbooks. Every step type is an enum. Every navigation pointer is validated at startup. The system behaves identically every time a given case state and slot state are presented.

---

## Components

### 1. `case_engine/workflows/models.py` — Domain Model

**WorkflowStepType** (6 values):
- `COLLECT_INFORMATION` — placeholder for slots not yet filled (should not appear at execution time)
- `CHECK_CONDITION` — evaluate conditions against slot context; branch on pass/fail
- `PROPOSE_ACTION` — propose action via Action Gateway; pause if approval needed
- `REQUEST_APPROVAL` — explicit pause for human review before continuing
- `RESOLVE_CASE` — terminal step: mark workflow COMPLETED
- `ESCALATE_CASE` — terminal step: mark workflow ESCALATED

**WorkflowState** (6 values): `PENDING | RUNNING | PAUSED | COMPLETED | ESCALATED | FAILED`

Terminal states: `{COMPLETED, ESCALATED, FAILED}` — held in `TERMINAL_WORKFLOW_STATES` frozenset.

**WorkflowCondition** (frozen dataclass):
- Operators: `eq | neq | exists | not_exists | in | not_in`
- All string comparisons are case-insensitive
- AND logic when multiple conditions are attached to a step

**WorkflowStep** (frozen dataclass):
- Navigation: `on_success`, `on_failure`, `on_approval`, `on_rejection` — all point to `step_id` values or the sentinels `"RESOLVE"` / `"ESCALATE"`

**WorkflowExecutionResult** (mutable dataclass):
- Serializable to/from JSONB via `to_dict()` / `from_dict()`
- Persisted to `cases.workflow_context` after each step
- `step_results` list accumulates one entry per step executed

---

### 2. `case_engine/workflows/playbook_registry.py` — Registry

`PlaybookRegistry.build(playbook_dir)` loads all `*.yml` and `*.yaml` files, validates them, and indexes by `topic` and `workflow_id`.

**Validation rules enforced at startup:**
- Required top-level fields: `workflow_id`, `topic`, `version`, `name`, `steps`
- Steps must have: `step_id`, `type`, `name`
- Step type must be a known `WorkflowStepType` value
- Navigation pointers (`on_success`, `on_failure`) must be known `step_id`s or `"RESOLVE"/"ESCALATE"`
- `PROPOSE_ACTION` steps require `action_type` and `action_namespace`
- `REVERSIBLE` actions require `rollback_action_type`
- Conditions require `field` and a valid `operator`

Any validation failure raises `PlaybookValidationError` and prevents startup.

---

### 3. YAML Playbooks (`case_engine/workflows/playbooks/`)

| File | Topic | Workflow ID | Required Slots | Steps |
|------|-------|-------------|---------------|-------|
| `vkyc_session_failure.yml` | `VKYC_Session_Failure` | `vkyc_session_failure_v1` | session_id, phone_number | 7 |
| `otp_delivery_failure.yml` | `OTP_Delivery_Failure` | `otp_delivery_failure_v1` | phone_number, channel | 5+ |
| `api_callback_failure.yml` | `API_Callback_Failure` | `api_callback_failure_v1` | callback_type, application_id | 5+ |
| `document_ocr_failure.yml` | `Document_OCR_Failure` | `document_ocr_failure_v1` | document_type, application_id | 5+ |
| `agent_portal_issue.yml` | `Agent_Portal_Issue` | `agent_portal_issue_v1` | agent_id, portal_type | 5+ |

All playbooks use `CHECK_CONDITION → PROPOSE_ACTION → CHECK_CONDITION → RESOLVE_CASE` as the happy path, with `ESCALATE_CASE` steps for each failure branch.

---

### 4. `case_engine/workflows/workflow_engine.py` — Execution Engine

`WorkflowEngine` is **stateless**. Create one instance, use it across thousands of cases. All mutable state lives in `WorkflowExecutionResult`.

**`start(case, registry, slot_values, gateway, audit)`:**
1. Look up playbook by `case.topic`
2. Validate required slots are all `FILLED`
3. Evaluate `entry_conditions` (if any)
4. Execute first step

**`resume_after_action(case, registry, action, slot_values, audit)`:**
1. Deserialize `WorkflowExecutionResult` from `case.workflow_context`
2. Find which step was paused
3. Determine action outcome (succeeded / failed / rejected)
4. Inject `action_result.*` into slot context
5. Navigate and execute from the next step

**Step execution dispatch:**

| Step Type | Behavior |
|-----------|----------|
| `CHECK_CONDITION` | Evaluate all conditions (AND); branch on pass/fail |
| `PROPOSE_ACTION` | If no gateway: SKIPPED_NO_GATEWAY, navigate on_success. If gateway returns EXECUTED: continue synchronously. Otherwise: PAUSED |
| `RESOLVE_CASE` | COMPLETED, set `resolution_note`, emit audit |
| `ESCALATE_CASE` | ESCALATED, set `escalation_reason`, emit audit |
| `REQUEST_APPROVAL` | PAUSED |
| `COLLECT_INFORMATION` | ESCALATED (design-time error — should not occur post-slot-fill) |

**Navigation sentinels:**
- `"RESOLVE"` → COMPLETED immediately without another step
- `"ESCALATE"` → ESCALATED immediately without another step
- Unknown `step_id` → FAILED with log error

**Param template rendering:** `{slot_name}` placeholders in `action_params_template` values are substituted from slot context. Missing slots render as empty string. Non-string values pass through unchanged.

---

### 5. Case Model Extensions (`case_engine/models.py`)

Four new fields on `Case`:
```python
workflow_id:         str | None     = None
workflow_state:      str | None     = None
workflow_step_index: int | None     = None
workflow_context:    dict[str, Any] = field(default_factory=dict)
```

Four new `AuditEventType` values:
```
WORKFLOW_STARTED | WORKFLOW_STEP_COMPLETED | WORKFLOW_ESCALATED | WORKFLOW_RESOLVED
```

---

### 6. CaseService Extensions (`case_engine/service.py`)

**`start_workflow(case, slot_values) → WorkflowStartResult`:**
- Requires `self._registry` to be set (else returns FAILED)
- Persists workflow fields to case
- Applies state transitions:
  - COMPLETED → `safe_transition(RESOLVED)`
  - ESCALATED → `safe_transition(ESCALATED)`
  - PAUSED → `safe_transition(ACTION_PENDING)` from WORKFLOW_ACTIVE
  - FAILED → `safe_transition(FAILED)`

**`resume_workflow(case, action_request, slot_values) → WorkflowStartResult`:**
- Same pattern; additionally handles RUNNING → `safe_transition(WORKFLOW_ACTIVE)` from ACTION_PENDING

**Auto-start in `receive_message`:** When all required slots are filled and `self._registry` is set, `start_workflow` is called automatically. The `ReceiveMessageResult.workflow_started` field signals this to callers.

**`list_workflow_cases(states) → list[Case]`:** Delegates to `CaseRepository.list_cases_by_workflow_state()`.

---

### 7. SQL Migration (`sql/sprint2_migrations/S2_008_workflow_state.sql`)

```sql
ALTER TABLE cases ADD COLUMN IF NOT EXISTS workflow_id TEXT;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS workflow_state TEXT
    CONSTRAINT cases_workflow_state_check
    CHECK (workflow_state IN ('PENDING','RUNNING','PAUSED','COMPLETED','ESCALATED','FAILED'));
ALTER TABLE cases ADD COLUMN IF NOT EXISTS workflow_step_index INTEGER;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS workflow_context JSONB;

CREATE INDEX IF NOT EXISTS idx_cases_workflow_state ON cases (workflow_state)
    WHERE workflow_state IN ('RUNNING', 'PAUSED');
CREATE INDEX IF NOT EXISTS idx_cases_workflow_context ON cases USING GIN (workflow_context)
    WHERE workflow_context IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cases_workflow_id ON cases (workflow_id)
    WHERE workflow_id IS NOT NULL;
```

Partial indexes on RUNNING/PAUSED optimize the admin monitoring queries without indexing terminal states.

---

### 8. API Endpoints

#### Case Engine Routes (`/cases/{case_id}/workflow`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/cases/{case_id}/workflow` | Return current workflow state for a case |
| `POST` | `/cases/{case_id}/workflow/resume` | Resume a paused workflow after action completes |

**Resume body:**
```json
{ "action_id": "<uuid>" }
```

**Resume response:**
```json
{
  "case_id": "...", "state": "RESOLVED",
  "workflow_id": "vkyc_session_failure_v1",
  "workflow_state": "COMPLETED",
  "current_step_id": "resolve_session_reset",
  "pending_action_id": null,
  "resolved": true, "escalated": false,
  "step_results": [...],
  "resolution_note": "The VKYC session has been successfully reset.",
  "escalation_reason": null
}
```

#### Workflow Admin Routes (`/admin/workflows/`)

All routes require ADMIN role (`X-API-Key` with admin scope).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/workflows/summary` | Aggregate counts by state, topic, workflow_id |
| `GET` | `/admin/workflows/active` | RUNNING + PAUSED workflows with details |
| `GET` | `/admin/workflows/stuck` | PAUSED workflows not updated in >30 minutes |

---

## Execution Flow — VKYC Session Failure

```
Customer: "My VKYC session failed"
    ↓
Topic Classifier: VKYC_Session_Failure (confidence 0.95)
    ↓ CaseState: TRIAGE_COMPLETE
Clarification Engine: "Please provide your session ID"
    ↓ customer provides KID-AB12CD34
Clarification Engine: "Please provide your phone number"
    ↓ customer provides +91-9876543210
All slots FILLED → auto-start workflow
    ↓ CaseState: WORKFLOW_ACTIVE
WorkflowEngine.start():
  Step 1: check_session_status (CHECK_CONDITION)
    → session_id EXISTS ✓, phone_number EXISTS ✓ → PASS
  Step 2: propose_session_reset (PROPOSE_ACTION, REVERSIBLE)
    → ActionGateway.propose() → ActionRequest AWAITING_APPROVAL
    → CaseState: ACTION_PENDING, WorkflowState: PAUSED
    ↓
[Human approver approves via /actions/{id}/approve]
    ↓
POST /cases/{case_id}/workflow/resume { "action_id": "..." }
WorkflowEngine.resume_after_action():
  action.current_state == EXECUTED → action_result.success = "true"
  Step 3: verify_reset_outcome (CHECK_CONDITION)
    → action_result.success == "true" ✓ → PASS
  Step 4: resolve_session_reset (RESOLVE_CASE)
    → WorkflowState: COMPLETED → CaseState: RESOLVED
    ↓
Response: { "resolved": true, "resolution_note": "VKYC session successfully reset" }
```

---

## Future Extensibility

The playbook schema is designed to accommodate future step types without breaking changes:

- **`NOTIFY_CUSTOMER`** — send message via Freshdesk reply (not yet implemented)
- **`LOOKUP_EXTERNAL`** — read-only API call to backend for context enrichment
- **`WAIT_FOR_EVENT`** — time-bounded pause waiting for external event (requires cron-based resume)
- **Parallel steps** — multi-step fan-out (requires orchestrator state graph extension)

Adding a new step type requires: (1) adding to `WorkflowStepType`, (2) adding a handler in `WorkflowEngine._execute_step`, (3) validating in `PlaybookRegistry._parse_step`. No existing playbooks need modification.

---

## Test Coverage

| File | Tests | Focus |
|------|-------|-------|
| `test_sprint216_workflow_models.py` | 50 | Enums, conditions, dataclass immutability, serialization |
| `test_sprint216_playbook_registry.py` | 42 | Load/validate all 5 playbooks, all validation error paths |
| `test_sprint216_workflow_engine.py` | 57 | All step types, navigation, gateway integration, audit safety |
| `test_sprint216_case_integration.py` | 34 | CaseService.start_workflow, resume_workflow, auto-start |
| `test_sprint216_workflow_api.py` | 38 | All 5 new endpoints, auth, response structure |
| **Total** | **221** | |

Full regression: **2519 passed, 0 failed** (baseline was 2298).
