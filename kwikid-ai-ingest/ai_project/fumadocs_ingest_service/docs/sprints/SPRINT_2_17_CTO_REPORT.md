# SPRINT 2.17 CTO REPORT
## Workflow Reliability + Investigation Tool Framework + Reasoning Foundation

**Date:** 2026-06-08  
**Author:** Engineering (Claude Code)  
**Status:** COMPLETE — 2706 tests passing, 0 failing  
**Branch:** major-architecture-change

---

## Executive Summary

Sprint 2.17 resolves four workflow correctness defects from Sprint 2.16, lays the foundational tool-execution layer (5 mock investigation tools, provider-agnostic framework), and delivers the first deterministic reasoning engine that decides what the agent does next without any LLM involvement. The sprint also evolves all 5 YAML playbooks with investigation metadata and exposes admin visibility endpoints for operational inspection.

The system remains on its "no LLM in the workflow layer" constraint. Every decision produced by `ReasoningEngine` is reproducible from the same inputs. No new database schema changes were required.

---

## Section 1: Race Condition Analysis

### Current Race Surface

The primary race risk is concurrent `resume_workflow` calls for the same `case_id`. The Supabase action gateway uses optimistic locking (`expected_version` / `new_version` on the `actions` table) which serialises gateway updates. However, `CaseService.resume_workflow` reads the case, runs the workflow engine, then writes back — this is a read-modify-write cycle without a transaction lock on the cases table.

**Known exposure:** Two simultaneous `POST /actions/{id}/complete` webhooks for the same action could each read the case in `ACTION_PENDING`, both run `WorkflowEngine.resume_after_action`, and both attempt to write back. The second write would win, producing one extra audit event and a silently-dropped first execution result.

**Current mitigation:** The action gateway's `mark_action_completed` uses `expected_version` and raises `StaleActionError` on version mismatch, preventing both gateway writes from succeeding simultaneously. One of the two webhook deliveries will receive a non-2xx and be retried — at which point the action is already `COMPLETED` and `ActionGateway.get_action` returns the completed state, causing the retry handler to no-op.

**Residual gap:** If the gateway write succeeds for both (unlikely given OCC) or if the resume path is triggered from two different entry points (webhook + operator UI), the second `resume_workflow` will raise `WorkflowConsistencyError` (added in A4 of this sprint) and produce a 409 rather than silently executing twice. This is a regression-prevention improvement, not a full solution.

**Remediation for Sprint 2.19:** Add a row-level advisory lock (`SELECT ... FOR UPDATE`) on the case row during the resume critical section, or serialise via a Redis-backed case-level mutex.

---

## Section 2: Workflow Consistency Review

### A1: State Machine Gap (Fixed)

The `ACTION_PENDING → RESOLVED` transition was always illegal per the state machine definition — this was correct. The bug was in `resume_workflow` attempting this transition directly. Fixed by inserting the intermediate `WORKFLOW_ACTIVE` hop:

```
ACTION_PENDING → WORKFLOW_ACTIVE (reason: action_completed)
              → RESOLVED         (reason: workflow_resolved)
```

This is semantically correct: once an action is completed the case re-enters active processing (even if for one microsecond) before resolving.

### A2: Audit Completeness

Resume and terminal events were previously silent. The five new `AuditEventType` values (`WORKFLOW_RESUMED`, `WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `TOOL_EXECUTED`, `TOOL_FAILED`) close all gaps. The audit trail for a complete happy-path workflow now produces 6+ events:

```
WORKFLOW_STARTED → STEP_COMPLETED (×N) → WORKFLOW_RESUMED → STEP_COMPLETED (×M) → WORKFLOW_COMPLETED
```

### A3: Double-Start Guard

`WorkflowAlreadyStartedError` prevents `start_workflow` from overwriting an active workflow. The `receive_message` idempotency treatment (treating `WorkflowAlreadyStartedError` as `wf_started=True`) means duplicate webhook deliveries or retried messages do not produce duplicate workflow instances.

### A4: Consistency Checker

`WorkflowConsistencyChecker.validate_resume()` enforces three invariants before any resume:
1. The workflow is in `PAUSED` state (not running, not terminal)
2. The workflow is not in a terminal state (`COMPLETED`, `FAILED`, `ESCALATED`)
3. The case is in `ACTION_PENDING` (we only resume after an action completes)

`check_state_alignment()` produces non-fatal warnings for misaligned states, enabling future health-check endpoints to surface drift.

---

## Section 3: Investigation Tool Framework Review

### Design Decisions

**Provider-agnostic by construction.** `BaseTool` is an ABC with a `definition` property and a `run(inputs) -> dict` method. Adding a real KwikID API adapter means subclassing `BaseTool` and registering it in `ToolRegistry.build_default()`. No other code changes required.

**Exception containment.** `ToolExecutor.execute()` wraps every `tool.run()` call in `try/except Exception`, converting any unhandled exception into a `ToolResult.fail("TOOL_EXECUTION_ERROR", ...)`. This matches the "never raises" contract enforced throughout the engine layer.

**Input validation before execution.** Required inputs are checked before calling `tool.run()`. This produces `MISSING_REQUIRED_INPUTS` error codes that the reasoning engine can interpret to request slot-filling instead of blaming tool failures.

**Immutable `ToolDefinition`.** Using `@dataclass(frozen=True)` prevents post-registration mutation. The `output_schema` is a plain `dict[str, str]` (field → type hint string) rather than a full JSON Schema, intentionally lightweight for this sprint.

### Gaps

- `output_schema` is a string map, not a formal JSON Schema — sufficient for admin display but will need to be a proper schema when we add LLM function-calling in Sprint 2.20
- No async support: `tool.run()` is synchronous. All mock tools are fast (pure Python), so this is fine. Real API adapters will need `async def run()` or thread pool offloading

---

## Section 4: Reasoning Framework Review

### Why Deterministic Rules First

The reasoning engine's job is to decide: *what should the agent do next?* In Sprint 2.17, that decision is made by a 5-rule priority chain:

```
1. Workflow terminal   → WORKFLOW_COMPLETE
2. Workflow paused     → WAIT_FOR_ACTION
3. Slots missing       → ASK_FOR_SLOT
4. Investigation tools pending → RUN_TOOL
5. All work done       → PROPOSE_ACTION
```

This produces correct, testable, auditable decisions without LLM latency or cost. The same context always produces the same decision — critical for tracing bugs in production.

### Rule Coverage

The `_TOPIC_TOOL_MAP` covers all 5 current topics. Unknown topics fall through to `PROPOSE_ACTION` at priority 5 — safe default. `analyze()` wraps `choose_next_step()` in `try/except` and returns an `ESCALATE` decision on unhandled error, preserving the "never raises" contract.

### `ReasoningContext.latest_tool_result()` Semantics

The method scans `tool_results` in reverse order and returns the last **successful** result for a tool. Failed results are skipped. This means a tool that succeeded once (even if later retried and failed) appears "run" to `all_tools_run()`. This is the intended behaviour: once we have good data from a tool, we don't discard it.

### LLM Integration Path (future)

`ReasoningDecision` carries `reasoning_steps: list[ReasoningStep]` — each step has `step_name`, `observation`, `conclusion`, `confidence`. When we add an LLM reasoning layer in Sprint 2.20, it will produce a chain of `ReasoningStep` objects that slot directly into this structure, making the transition seamless and the audit trail consistent across deterministic and LLM-based decisions.

---

## Section 5: Scalability Analysis

### Current Scale Profile

The system currently handles one case at a time per request. There is no horizontal parallelism within a single case (the workflow executes synchronously in the request thread). This is acceptable while case volume is low.

### Tool Layer Scalability

`ToolExecutor` is stateless. Each call is independent. At high volume, the bottleneck will be the real KwikID API calls (network I/O). The fix is async execution (Sprint 2.19) using `asyncio.gather()` to run independent tools in parallel.

### PlaybookRegistry + ToolRegistry at Startup

Both registries are loaded once at startup and stored in `app.state`. Playbook loading (YAML parse + validation) takes ~5ms for 5 playbooks. Tool registration is pure Python object instantiation. Neither is a scalability concern.

### ReasoningEngine at Scale

`ReasoningEngine` is a pure Python computation over an in-memory `ReasoningContext`. It completes in < 1ms for all current rule chains. There is no state, no I/O, no locking. It will scale linearly with the number of concurrent requests.

---

## Section 6: Security Review

### No New Attack Surface

Sprint 2.17 adds two new admin endpoints (`/admin/tools/`, `/admin/workflows/playbooks`) and mock tools. All admin endpoints are guarded by `require_admin` (API key + role check). Mock tools take plain string inputs and produce hardcoded outputs — no SQL, no file system access, no subprocess execution.

### Auth Dependency Hardening

The `_get_authenticator()` function previously let `AttributeError` propagate when `app.state.authenticator` was not set. This caused the TestClient to raise instead of returning an HTTP error, and in a misconfigured production deploy could produce an unformatted 500. Fixed: wrapped in `try/except AttributeError` to raise `HTTPException(503)`.

### Phone Number Masking

`GetUserDetailsTool.run()` masks the phone number in its output (`+91-9876***210`). This is implemented in the mock and must be re-implemented in the real adapter (Sprint 2.19).

### No Secrets in Tool Outputs

All mock tool outputs are synthetic. Real adapters must ensure:
- No session tokens in `GetSessionDetailsTool` output
- No full PAN/Aadhaar numbers in KYC fields
- `risk_tier` returned as enum string, not as the raw ML score

---

## Section 7: Silent Failure Inventory

| Component | Failure Mode | Current Handling |
|-----------|-------------|-----------------|
| `ToolExecutor.execute()` | Tool raises exception | Caught → `ToolResult.fail("TOOL_EXECUTION_ERROR")` — **explicit** |
| `ToolExecutor.execute()` | Tool not in registry | Returns `ToolResult.fail("TOOL_NOT_FOUND")` — **explicit** |
| `ToolExecutor.execute()` | Missing required input | Returns `ToolResult.fail("MISSING_REQUIRED_INPUTS")` — **explicit** |
| `ReasoningEngine.analyze()` | `choose_next_step` raises | Catches, returns `ESCALATE` decision — **explicit** |
| `PlaybookRegistry.build()` | Missing YAML fields | Raises `PlaybookValidationError` — **explicit** |
| `WorkflowConsistencyChecker` | Double-start attempt | Raises `WorkflowAlreadyStartedError` — **explicit** |
| `CaseService.resume_workflow` | Called on non-PAUSED workflow | Raises `WorkflowConsistencyError` → 409 — **explicit** |
| `_get_authenticator()` | `app.state.authenticator` absent | Now raises `HTTPException(503)` — **explicit** (fixed this sprint) |

**Remaining silent failures:**
- `WorkflowEngine._run_steps()` catches all exceptions per step and marks the step as failed, but the exception message is not surfaced to the audit log's `detail` field — it is only in `result.escalation_reason`. Low severity.
- `PlaybookRegistry._parse()` silently coerces malformed `investigation_steps` entries (non-dict items are filtered out). Should emit a warning log.

---

## Section 8: Future Integration Plan — Real KwikID APIs

When replacing mock tools with real KwikID API adapters (Sprint 2.19):

### Step 1: Create adapter subclasses

```python
class KwikIDSessionAdapter(BaseTool):
    def __init__(self, http_client: KwikIDHTTPClient): ...
    @property
    def definition(self) -> ToolDefinition:
        return GetSessionDetailsTool().definition  # same contract
    async def run(self, inputs: dict) -> dict:
        resp = await self.http_client.get_session(inputs["session_id"])
        return _map_response(resp)
```

### Step 2: Update `ToolRegistry.build_default()`

```python
@classmethod
def build_default(cls, http_client=None) -> ToolRegistry:
    if http_client:
        # real adapters
        registry.register(KwikIDSessionAdapter(http_client))
        ...
    else:
        # mock tools (test / local dev)
        registry.register(GetSessionDetailsTool())
        ...
```

### Step 3: Pass `http_client` from `runtime/assembly.py`

The `AppFactory.create()` in `runtime/assembly.py` builds `app.state`. Add `KwikIDHTTPClient` construction there and pass it to `ToolRegistry.build_default()`.

### Step 4: Handle async

Convert `ToolExecutor.execute()` to `async def execute()`, and await `tool.run()` if the tool is async-capable (use `asyncio.iscoroutinefunction`).

No test changes required — `ToolRegistry.build_default()` without `http_client` continues to use mock tools.

---

## Section 9: Future Integration Plan — LLM Reasoning

When adding LLM-based reasoning (Sprint 2.20+):

### What stays

- `ReasoningContext` — unchanged, already serialisable via `to_dict()`
- `ReasoningDecision` with `reasoning_steps` — the LLM chain-of-thought maps directly to `ReasoningStep` objects
- `NextStepType` enum — the LLM must produce one of these typed decisions, not free text
- `ReasoningEngine.choose_next_step()` — deterministic rules become a fallback / override layer

### LLM integration architecture

```python
class LLMReasoningEngine(ReasoningEngine):
    def __init__(self, llm_client, fallback: ReasoningEngine): ...

    def choose_next_step(self, ctx: ReasoningContext) -> ReasoningDecision:
        # 1. Run deterministic rules first (short-circuit on high-confidence cases)
        deterministic = self.fallback.choose_next_step(ctx)
        if deterministic.confidence >= 0.95:
            return deterministic

        # 2. Build prompt from ctx.to_dict()
        # 3. Call LLM, parse response into NextStepType + rationale
        # 4. Return ReasoningDecision with reasoning_steps populated from LLM CoT
```

This keeps the LLM as an enhancement, not a dependency. If the LLM is unavailable, `analyze()` falls back to the deterministic engine. The audit trail looks identical whether the decision came from rules or the LLM.

### Constraints to enforce

- LLM output must be validated against `NextStepType` enum — reject free text
- `slot_name` (when `next_step == ASK_FOR_SLOT`) must be a known slot for the case's topic
- `tool_name` (when `next_step == RUN_TOOL`) must be a name in `ToolRegistry`
- Confidence from LLM should be calibrated against deterministic baseline before trusting

---

## Section 10: Remaining Gaps Before Autonomous Support-Agent Operation

The following capabilities are required before the system can operate as a fully autonomous enterprise support agent (like Sierra AI / Zendesk AI) without human intervention:

| Gap | Priority | Target Sprint |
|-----|----------|-------------|
| Real KwikID API adapters for 5 investigation tools | P0 — blocks autonomous investigation | 2.19 |
| Slot-filling auto-population from tool results | P0 — agent can't advance without this | 2.18 |
| End-to-end conversation loop (message → reasoning → tool → action proposal) | P0 | 2.19 |
| Tool execution audit trail persisted to DB | P1 — compliance requirement | 2.18 |
| Async tool execution with timeout | P1 — real APIs need I/O handling | 2.19 |
| LLM reasoning layer (optional enhancement) | P2 — improves quality, not required for autonomy | 2.20 |
| Action proposal → Freshdesk ticket reply integration | P1 — needed for closed loop | 2.19 |
| Confidence thresholds: when to escalate vs auto-resolve | P1 — safety boundary | 2.18 |
| Rate limiting on tool calls (KwikID API quota) | P1 — prod safety | 2.19 |
| Human-in-the-loop override at any step | P0 — required for regulated operations | 2.18 |
| End-to-end latency target (< 3s p95) | P1 — customer-facing SLA | 2.20 |

### Current system capability

The system today can:
- Classify a support ticket topic deterministically
- Execute a YAML playbook workflow with slot filling and action gating
- Decide what to do next (ask for slot / run tool / propose action) via deterministic rules
- Present tool definitions and playbook metadata to operators via admin API

The system **cannot yet**:
- Actually call KwikID APIs to get real investigation data
- Automatically fill slots from tool results
- Close a support ticket autonomously without operator confirmation
- Generate natural-language replies to Freshdesk tickets

These are Sprint 2.18–2.19 deliverables.

---

## Regression Results

```
Sprint 2.17 tests:    187 new tests
Full suite:           2706 passed, 0 failed, 4 skipped
Previous baseline:    2519 passing (Sprint 2.16)
Net new tests:        +187
```

All Sprint 2.16 tests continue to pass. The 4 skipped tests are infrastructure-related (Supabase connectivity) and have been skipped since Sprint 2.10.
