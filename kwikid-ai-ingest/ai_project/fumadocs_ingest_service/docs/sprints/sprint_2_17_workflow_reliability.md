# Sprint 2.17 — Workflow Reliability + Investigation Tool Framework + Reasoning Foundation

**Date:** 2026-06-08  
**Branch:** major-architecture-change  
**Test count:** 2706 passing, 0 failing (full regression)  
**Sprint 2.17-specific tests:** 187 new tests across 6 test files

---

## Overview

Sprint 2.17 has three pillars:

1. **Workflow Reliability (Part A)** — fix known Sprint 2.16 defects: state machine gap, missing audit events, double-start vulnerability, consistency validation
2. **Investigation Tool Framework (Parts B + C)** — provider-agnostic tool layer with 5 mock KwikID investigation tools
3. **Reasoning Foundation (Parts D + E + F)** — deterministic reasoning engine, evolved playbooks, admin visibility endpoints

---

## Part A: Workflow Reliability Fixes

### A1: ACTION_PENDING → RESOLVED State Machine Gap

**Root cause.** The state machine correctly blocks the direct transition `ACTION_PENDING → RESOLVED` (it is not listed as a valid edge). However, `resume_workflow` attempted this transition after `WorkflowEngine` returned `COMPLETED` while the case was in `ACTION_PENDING`.

**Fix.** When `wf_result.workflow_state == WorkflowState.COMPLETED` and `case.current_state == CaseState.ACTION_PENDING`, `resume_workflow` now takes a two-hop path:

```
ACTION_PENDING → WORKFLOW_ACTIVE (reason: "action_completed")
              → RESOLVED         (reason: "workflow_resolved")
```

The same two-hop logic was applied for the `ESCALATED` path.

### A2: Audit Events for Resume/Complete/Fail

Added five new `AuditEventType` values to `case_engine/models.py`:

| Event | Trigger |
|-------|---------|
| `WORKFLOW_RESUMED` | `WorkflowEngine.resume_after_action()` starts |
| `WORKFLOW_COMPLETED` | Workflow reaches `COMPLETED` state |
| `WORKFLOW_FAILED` | Workflow reaches `FAILED` state |
| `TOOL_EXECUTED` | Tool execution succeeds |
| `TOOL_FAILED` | Tool execution fails |

Four new methods added to `AuditLogger`: `log_workflow_resumed`, `log_workflow_completed`, `log_workflow_failed`, `log_tool_executed`.

### A3: Double-Start Guard

`CaseService.start_workflow` now raises `WorkflowAlreadyStartedError` if `case.workflow_state` is already in `{RUNNING, PAUSED, COMPLETED, ESCALATED, FAILED}`. The `receive_message` flow catches this exception and treats it as idempotent (sets `wf_started=True` without logging an error).

### A4: Workflow Consistency Validation

`case_engine/workflows/consistency.py` introduces `WorkflowConsistencyChecker`:

- `validate_start(case)` — rejects if workflow is already active/completed
- `validate_resume(case)` — rejects if workflow is not `PAUSED`, if it has terminated, or if case is not in `ACTION_PENDING`
- `check_state_alignment(case)` — returns a list of warning strings for misaligned case/workflow state (diagnostic, non-raising)
- `validate_no_duplicate_active_workflow(cases)` — batch diagnostic for multiple cases

`CaseService` now holds a `WorkflowConsistencyChecker` instance and calls `validate_resume(case)` before any resume attempt.

---

## Part B: Investigation Tool Framework

### Architecture

```
case_engine/tools/
├── __init__.py          # re-exports BaseTool, ToolDefinition, ToolInput, ToolResult,
│                        #   ToolRegistry, ToolExecutor
├── tool_models.py       # immutable dataclasses: ToolDefinition, ToolInput, ToolResult
├── tool_executor.py     # BaseTool ABC + ToolExecutor (never raises)
├── tool_registry.py     # ToolRegistry with build_default() factory
└── mock_tools.py        # 5 concrete BaseTool implementations
```

### ToolDefinition (frozen dataclass)

```python
ToolDefinition(
    tool_name:       str,
    description:     str,
    required_inputs: tuple[str, ...],
    output_schema:   dict[str, str],   # field_name → type_hint
    version:         str = "1.0",
    tags:            tuple[str, ...] = (),
)
```

Immutable post-construction. `to_dict()` converts tuples to lists for JSON serialisation.

### ToolExecutor execution contract

1. Look up tool in registry → `TOOL_NOT_FOUND` on miss
2. Validate all `required_inputs` present → `MISSING_REQUIRED_INPUTS` on gap
3. Record `time.monotonic()` start
4. Call `tool.run(inputs)` inside `try/except Exception` → `TOOL_EXECUTION_ERROR` on unhandled raise
5. Return `ToolResult` with `duration_ms` always set

`ToolExecutor.execute()` **never raises**. Every failure is a typed `ToolResult.fail(...)`.

---

## Part C: Mock Investigation Tools

Five `BaseTool` implementations in `case_engine/tools/mock_tools.py` provide deterministic fake data for architectural validation. They will be replaced by real KwikID API adapters in Sprint 2.19.

| Tool | Required Input | Key Outputs |
|------|---------------|------------|
| `GetSessionDetailsTool` | `session_id` | status, attempt_count, can_reset, failure_code |
| `GetUserDetailsTool` | `phone_number` | kyc_status, account_active, risk_tier (masks phone) |
| `GetFailureReasonTool` | `operation_id` | failure_category, is_transient, recommended_action |
| `GetCaseHistoryTool` | `phone_number` | case_count, recent_cases, escalation_rate |
| `GetOnboardingStatusTool` | `application_id` | onboarding_stage, completion_pct, can_auto_advance |

`ToolRegistry.build_default()` registers all 5 and is the single source of truth for the default tool set.

---

## Part D: Reasoning Framework Foundation

### Architecture

```
case_engine/reasoning/
├── __init__.py             # re-exports key classes
├── reasoning_models.py     # NextStepType, ReasoningStep, ReasoningContext, ReasoningDecision
├── reasoning_context.py    # build_context(), build_context_from_case()
└── reasoning_engine.py     # ReasoningEngine with deterministic 5-rule chain
```

### ReasoningEngine: 5-rule priority chain

```
Priority 1 (highest): workflow_state == COMPLETED  → WORKFLOW_COMPLETE (confidence 1.0)
Priority 2:           workflow_state == PAUSED      → WAIT_FOR_ACTION
Priority 3:           any slot_values missing       → ASK_FOR_SLOT (first missing slot)
Priority 4:           any topic tool not yet run    → RUN_TOOL (next unrun tool)
Priority 5 (lowest):  all tools run                 → PROPOSE_ACTION
```

No LLM is involved. All decisions are deterministic and reproducible from the same `ReasoningContext`.

### Topic-to-Tool Map

```python
_TOPIC_TOOL_MAP = {
    "VKYC_Session_Failure":  ["GetSessionDetailsTool", "GetUserDetailsTool"],
    "OTP_Delivery_Failure":  ["GetUserDetailsTool", "GetFailureReasonTool"],
    "Document_OCR_Failure":  ["GetUserDetailsTool", "GetOnboardingStatusTool"],
    "Agent_Portal_Issue":    ["GetUserDetailsTool", "GetFailureReasonTool"],
    "API_Callback_Failure":  ["GetFailureReasonTool"],
}
```

For unknown topics, `recommend_tool()` returns `None` and `choose_next_step()` falls through to `PROPOSE_ACTION`. `analyze()` never raises.

---

## Part E: Playbook Evolution

All 5 YAML playbooks in `case_engine/workflows/playbooks/` now carry three optional metadata sections:

```yaml
investigation_steps:
  - tool: GetSessionDetailsTool
    purpose: "Retrieve session status and failure code"
    required_input: session_id

tool_candidates:
  - GetSessionDetailsTool
  - GetUserDetailsTool

resolution_paths:
  automatic: resolve_case
  escalation_timeout: escalate_case
```

`WorkflowDefinition` gains three new fields (all optional with empty defaults):

```python
investigation_steps: tuple[dict[str, Any], ...] = ()
tool_candidates:     tuple[str, ...] = ()
resolution_paths:    dict[str, str] = {}
```

`PlaybookRegistry._parse()` extracts these fields optionally — old playbooks without them still load without error (backward compatible).

---

## Part F: Admin Visibility Endpoints

### Tool Admin (`api/routes/tool_admin.py`)

| Endpoint | Method | Auth | Response |
|----------|--------|------|---------|
| `/admin/tools/` | GET | require_admin | `{total, tools: [ToolDefinition.to_dict()...]}` sorted by name |
| `/admin/tools/{tool_name}` | GET | require_admin | `ToolDefinition.to_dict()` or 404 |

Returns 503 if `app.state.tool_registry` is not set.

### Workflow Admin additions (`api/routes/workflow_admin.py`)

| Endpoint | Method | Auth | Response |
|----------|--------|------|---------|
| `/admin/workflows/playbooks` | GET | require_admin | `{total, playbooks: [...]}` with investigation metadata |
| `/admin/workflows/playbooks/{workflow_id}` | GET | require_admin | Full `WorkflowDefinition.to_dict()` or 404 |

Returns 503 if `app.state.playbook_registry` is not set.

### Auth fix (`security/dependencies.py`)

`_get_authenticator()` now wraps `request.app.state.authenticator` access in `try/except AttributeError` and raises `HTTPException(503)` instead of letting the `AttributeError` propagate — ensuring any unconfigured test app returns a proper HTTP error code rather than crashing the TestClient.

---

## Test Coverage

| File | Tests | Covers |
|------|-------|-------|
| `test_sprint217_workflow_fixes.py` | ~65 | A1–A4: state machine fix, audit events, double-start, consistency checker |
| `test_sprint217_tool_framework.py` | ~40 | B: ToolDefinition, ToolInput, ToolResult, ToolRegistry, ToolExecutor |
| `test_sprint217_mock_tools.py` | ~40 | C: all 5 mock tools, default registry, executor integration |
| `test_sprint217_reasoning.py` | ~35 | D: ReasoningStep, ReasoningContext, ReasoningEngine (all 6 decision paths) |
| `test_sprint217_playbook_evolution.py` | ~25 | E: WorkflowDefinition new fields, YAML upgrade, backward compat |
| `test_sprint217_admin_api.py` | ~27 | F: tool list/get, playbook list/get, auth guard, 503 handling |
| **Total** | **~232** | All Sprint 2.17 components |

Full regression: **2706 passed, 0 failed** (up from 2519 before this sprint).

---

## What Is Not Yet Built

- Real KwikID API adapters for the 5 investigation tools (Sprint 2.19)
- LLM-based reasoning to replace/augment `ReasoningEngine` (Sprint 2.20+)
- Tool execution audit trail persisted to DB (Sprint 2.18)
- Slot-filling auto-population from tool results (Sprint 2.18)
- End-to-end conversation loop: reasoning → slot fill → tool call → action proposal (Sprint 2.19)
