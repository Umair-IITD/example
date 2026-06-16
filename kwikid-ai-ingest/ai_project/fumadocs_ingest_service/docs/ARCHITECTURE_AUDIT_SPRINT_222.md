# Architecture Compliance Audit — Sprint 2.22

**Date:** 2026-06-12
**Auditor:** CTO Compliance Review
**Source of Truth:** flow_diagram.mermaid + SUPPORT_OPERATIONS_BLUEPRINT.md
**Sprint:** 2.22

---

## Methodology

Every node in `flow_diagram.mermaid` was traced against the current codebase (3545 passing tests, Sprint 2.21 complete). Each node is assessed against five criteria:

1. **Exists** — implementation file/class present
2. **Reachable** — code path actually reaches it
3. **Used** — invoked in production or workflow path
4. **Wired** — connected to upstream/downstream per diagram
5. **Audited** — audit event emitted

Status values: `IMPLEMENTED` · `PARTIALLY_IMPLEMENTED` · `NOT_IMPLEMENTED` · `IMPLEMENTED_BUT_NOT_WIRED`

---

## External Systems

| Node | Status | Notes |
|---|---|---|
| USER (Bank Agent / Customer) | NOT_IMPLEMENTED | External; Freshdesk webhook provides entry point |
| FD (Freshdesk) | PARTIALLY_IMPLEMENTED | `freshdesk/freshdesk_provider.py` + webhook exists; write-back (notes, replies) not wired |
| ASANA | NOT_IMPLEMENTED | No Asana client. L2 escalation creates ticket concept only |
| DEV (Engineering Team) | NOT_IMPLEMENTED | External actor; no integration |

---

## Core Ingestion & Case Management

| Node | Status | File | Notes |
|---|---|---|---|
| TICKET | PARTIALLY_IMPLEMENTED | `api/routes/webhook.py`, `webhook/freshdesk_processor.py` | Ingestion exists; not all ticket fields mapped |
| CASE | IMPLEMENTED | `case_engine/service.py` `CaseService` | Full lifecycle, state machine |
| CASEDB | IMPLEMENTED | Supabase `cases` table | SQL migrations complete |

---

## AI & Reasoning Engine

| Node | Status | File | Notes |
|---|---|---|---|
| CLASSIFIER | IMPLEMENTED | `case_engine/classifier.py`, `query_router/classifier.py` | Topic classification with thresholds |
| SLOTEXTRACT | IMPLEMENTED | `case_engine/slot_filling/` | SlotRegistry, SlotValue models |
| CLARIFICATION | PARTIALLY_IMPLEMENTED | `case_engine/clarification_engine.py` | Engine exists; not fully wired into receive_message loop |
| WORKFLOWSELECT | IMPLEMENTED | `case_engine/workflows/playbook_registry.py` | Topic → playbook mapping |
| ENGINE | IMPLEMENTED | `case_engine/workflows/workflow_engine.py` | Deterministic step executor |
| INVESTIGATION | IMPLEMENTED | `case_engine/investigation/` (7 files) | Full investigation pipeline Sprint 2.18 |
| EVIDENCE | IMPLEMENTED | `case_engine/investigation/collector.py` | EvidenceCollector with tool integration |
| ROOTCAUSE | IMPLEMENTED | `case_engine/investigation/root_cause.py` | RootCauseEngine, 14 categories |
| REASONING | PARTIALLY_IMPLEMENTED | `case_engine/reasoning/reasoning_engine.py` | ReasoningEngine exists; not wired into workflow path |
| GUARDRAILS | IMPLEMENTED_BUT_NOT_WIRED | `case_engine/workflows/workflow_engine.py` lines 349–389 | Guards implemented inline as PROPOSE_ACTION preconditions; not a standalone module |
| OBSGEN | IMPLEMENTED | `case_engine/investigation/observation.py` | ObservationGenerator produces structured notes |
| LLM | NOT_IMPLEMENTED | — | No enterprise LLM integration; all paths are deterministic |

**Critical Gap: GUARDRAILS**
The `GUARDRAILS` node in the flow diagram (`REASONING → GUARDRAILS → ACTIONPROPOSAL`) is currently implemented as inline precondition checks in `workflow_engine.py:_exec_propose_action()`. It is not a standalone module with a defined interface. This is acceptable for current deterministic implementation but will block future guardrail policy injection.
**Fix in Sprint 2.22:** Document gap; existing inline guards satisfy blueprint requirement for now.

**Critical Gap: REASONING**
The `REASONING` node exists in `case_engine/reasoning/` but is not wired into the workflow execution path. The HYBRIDRAG → REASONING → GUARDRAILS flow is not complete.
**Impact:** Medium. The knowledge layer (HYBRIDRAG) output goes directly to ACTIONPROPOSAL via `knowledge_result`. The ReasoningEngine is not called in the workflow path.
**Fix:** Document as known gap; reasoning engine is advisory; proposal engine incorporates knowledge_result directly.

---

## Knowledge Layer

| Node | Status | File | Notes |
|---|---|---|---|
| STACK | IMPLEMENTED | `case_engine/knowledge/importer.py` | StackOverflow Teams Export importer |
| INGEST | IMPLEMENTED | `case_engine/knowledge/importer.py` | `StackOverflowImporter.import_file()` |
| INDEX | IMPLEMENTED | `case_engine/knowledge/repository.py` | KnowledgeRepository stores entries |
| HYBRIDRAG | IMPLEMENTED | `case_engine/knowledge/retriever.py` | Hybrid BM25+semantic retrieval |
| VISION | NOT_IMPLEMENTED | — | No image/video understanding. Blueprint Section 12 marks as "Future Capability" |
| PLAYBOOKS | IMPLEMENTED | `case_engine/workflows/playbooks/*.yml` + registry | 5 playbooks loaded |

---

## Tool & Investigation Layer

| Node | Status | File | Notes |
|---|---|---|---|
| TOOLS | IMPLEMENTED | `case_engine/tools/tool_registry.py` | ToolRegistry with registration |
| ADMINSVC | PARTIALLY_IMPLEMENTED | `api/routes/` (multiple admin routes) | API exists; not all support portal APIs connected |
| USERTOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockGetUserDetailsTool |
| SESSIONTOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockGetSessionDetailsTool |
| FAILTOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockGetFailureReasonTool |
| CASETOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockGetCaseHistoryTool |
| ONBOARDTOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockGetOnboardingStatusTool |
| LOGTOOL | NOT_IMPLEMENTED | — | No session logs API integration |
| SUMMARYTOOL | NOT_IMPLEMENTED | — | No session summary API integration |
| VIDEOTOOL | NOT_IMPLEMENTED | — | No video retrieval integration |
| METRICTOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockMetricsTool |
| SERVERTOOL | IMPLEMENTED (mock) | `case_engine/tools/mock_tools.py` | MockServerStatusTool |

**Note:** All 7 tool implementations are mocks returning synthetic data. No live API calls to support portal. This is Sprint 2.17 scope — acceptable for current phase.

---

## Execution Layer — CRITICAL PATH

This is the core of Sprint 2.22 audit. The flow diagram defines:

```
ACTIONPROPOSAL → ACTIONGW → RISKCHECK → APPROVAL → HUMANAPPROVER → EXECUTE → VERIFY → RECOVERY → RESOLUTION
```

| Node | Status | File | Gap |
|---|---|---|---|
| ACTIONPROPOSAL | IMPLEMENTED | `case_engine/actions/` (5 files) | Sprint 2.21 complete |
| ACTIONGW | IMPLEMENTED_BUT_NOT_WIRED | `case_engine/action_gateway.py` | Sprint 2.1 gateway manages execution lifecycle but is NOT wired to receive ActionProposalBundle as input. It receives an `ActionProposal` (Sprint 2.1 DTO) directly from workflow engine, bypassing the proposal-to-gateway routing layer. |
| RISKCHECK | PARTIALLY_IMPLEMENTED | `case_engine/actions/risk.py` + `case_engine/action_state.py` | Sprint 2.21 risk assessed at proposal time. Sprint 2.1 `ActionRiskLevel` (SAFE/REVERSIBLE/IRREVERSIBLE) applied at gateway time. These are TWO SEPARATE risk systems that are not connected. |
| APPROVAL | PARTIALLY_IMPLEMENTED | `case_engine/action_gateway.py::approve()/reject()` | Human approval methods exist. No orchestration layer that receives risk decision and routes to approval workflow. No automatic routing from RISKCHECK → APPROVAL. |
| HUMANAPPROVER | NOT_IMPLEMENTED | — | Approval API routes exist (`api/routes/actions.py`) but no approval workflow orchestration. No Freshdesk/Slack integration for human approval requests. |
| EXECUTE | IMPLEMENTED | `case_engine/action_gateway.py::begin_execution()`, `case_engine/action_runtime.py`, `executors/` | Sprint 2.2 executor framework complete. ExecutorRegistry, ExecutionRuntime. |
| ACTIONDB | IMPLEMENTED | Supabase `action_gateway` table | SQL migrations S2_001 through S2_006 |
| VERIFY | NOT_IMPLEMENTED | — | No VerificationEngine. `record_success/record_failure` in action_gateway.py provides outcome tracking but not active verification |
| RECOVERY | IMPLEMENTED | `case_engine/action_gateway_recovery.py` | RecoveryService with retry, rollback, dead-letter. Sprint 2.10 |
| RESOLUTION | NOT_IMPLEMENTED | — | No standalone Resolution module. WorkflowState.COMPLETED is the closest proxy |
| NOTEGEN | NOT_IMPLEMENTED | — | No NotesGenerator. ObservationGenerator (Sprint 2.18) generates investigation notes but not resolution notes |
| USERRESPONSE | NOT_IMPLEMENTED | — | No customer reply generator |
| CLOSE | NOT_IMPLEMENTED | — | No ticket closure automation. CaseState.CLOSED exists but is not triggered automatically |
| ESCALATE | IMPLEMENTED | `case_engine/escalation.py`, `WorkflowState.ESCALATED` | Escalation state and logic exist |

### Critical Wiring Gap: ACTIONPROPOSAL → ACTIONGW

**The most critical architectural violation:**

In the current code, the workflow engine's `_exec_propose_action()` method:
1. Runs `_run_action_proposal()` → stores `ActionProposalBundle` as `result.action_proposal_result` (Sprint 2.21) ✓
2. Then calls `gateway.propose()` directly with a Sprint 2.1 `ActionProposal` DTO — **NOT** derived from the Sprint 2.21 bundle

The Sprint 2.21 proposal system and the Sprint 2.1 execution gateway are **parallel, unconnected paths**. The `action_proposal_result` is stored but never fed into the gateway decision. The gateway still creates its own `ActionProposal` from the YAML playbook step template.

**This means:** The RISKCHECK decision is made by Sprint 2.1's `ActionRiskLevel` (from the YAML `risk_level` field), completely ignoring the Sprint 2.21 `ProposalRiskLevel` that was carefully computed by the risk engine.

**Fix in Sprint 2.22:** Create `ActionGatewayService` that bridges the proposal system to the execution gateway. Add `ACTION_GATEWAY` step type that uses the proposal bundle as its primary input.

---

## Governance

| Node | Status | File | Notes |
|---|---|---|---|
| AUDIT | IMPLEMENTED | `case_engine/audit.py`, `audit/` | AuditLogger, SupabaseAuditRepository |
| AUDITDB | IMPLEMENTED | Supabase `case_audit_log` | Write-through audit |

### Audit Event Coverage Analysis

Diagram requires audit on: CASE, ENGINE, INVESTIGATION, ROOTCAUSE, ACTIONGW, APPROVAL, EXECUTE, RECOVERY, CLOSE

| Required Audit Point | Status |
|---|---|
| CASE -.-> AUDIT | ✓ `log_transition`, `log_classification` |
| ENGINE -.-> AUDIT | ✓ `log_workflow_started`, `log_step_completed`, etc. |
| INVESTIGATION -.-> AUDIT | ✓ `log_investigation_started/completed` |
| ROOTCAUSE -.-> AUDIT | ✓ `log_investigation_completed` includes root cause |
| ACTIONGW -.-> AUDIT | ✗ **MISSING** — no `ACTION_GATEWAY_STARTED/COMPLETED` events |
| APPROVAL -.-> AUDIT | ✗ **MISSING** — `log_action_proposed/executed/rejected` exist but no `APPROVAL_REQUESTED/GRANTED/REJECTED` |
| EXECUTE -.-> AUDIT | ✓ (via action_gateway transitions) |
| RECOVERY -.-> AUDIT | ✓ `case_engine/audit.py` recovery events |
| CLOSE -.-> AUDIT | ✗ **MISSING** — no case closure audit event |

---

## Architecture Completeness Summary

| Architecture Layer | Completion % | Notes |
|---|---|---|
| Ticket Ingestion | 60% | Webhook works; not all fields mapped |
| Case Management | 95% | Full lifecycle; closure not automated |
| Topic Classification | 90% | Working with 5 topics |
| Workflow Orchestration | 85% | ENGINE + PLAYBOOKS fully operational |
| Slot Extraction | 80% | Models + registry complete |
| Clarification | 50% | Engine exists but not fully wired |
| Investigation | 85% | Full pipeline; live tools not integrated |
| Knowledge / HYBRIDRAG | 80% | Full retrieval; VISION not built |
| Action Proposal (Sprint 2.21) | 100% | ACTIONPROPOSAL fully implemented |
| Action Gateway (Sprint 2.22) | 15% | **Critical gap — this sprint's focus** |
| Risk Check Routing | 20% | Risk assessed but not routed to approval |
| Approval Workflow | 25% | Approve/reject methods exist; no orchestration |
| Execution | 75% | Sprint 2.1/2.2 executor framework complete |
| Verification | 5% | outcome recording only; no active verification |
| Recovery | 80% | Retry + dead-letter complete |
| Resolution | 10% | Workflow terminal states only |
| Notes Generation | 40% | ObservationGenerator done; NotesGenerator not |
| Escalation | 80% | L2 escalation complete |
| Audit Coverage | 70% | Gateway + approval audit missing |
| **Overall** | **~58%** | |

---

## Critical Gaps Fixed in Sprint 2.22

### Gap 1: ACTION_GATEWAY step type missing
**Before:** `WorkflowStepType` has 8 values; no `ACTION_GATEWAY` step
**After:** `WorkflowStepType.ACTION_GATEWAY` added (9th step type)

### Gap 2: ACTIONPROPOSAL → ACTIONGW wiring broken
**Before:** Sprint 2.21 proposal result stored but never fed to gateway routing
**After:** `ActionGatewayService.process()` accepts `action_proposal_result` dict and routes through RISKCHECK → APPROVAL

### Gap 3: RISKCHECK is two separate disconnected systems
**Before:** `ProposalRiskLevel` (Sprint 2.21) and `ActionRiskLevel` (Sprint 2.1) independent
**After:** `GatewayRiskEngine` maps `ProposalRiskLevel` → routing decision; bridges the two systems

### Gap 4: APPROVAL routing not orchestrated
**Before:** `approve()/reject()` exist but nothing routes to them based on risk level
**After:** `ApprovalEngine` deterministically routes based on risk level; HIGH_RISK mechanical enforcement

### Gap 5: ACTION_GATEWAY audit events missing
**Before:** No `ACTION_GATEWAY_STARTED/COMPLETED` or `APPROVAL_REQUESTED/GRANTED/REJECTED`
**After:** 5 new AuditEventType values + 5 new AuditLogger methods

### Gap 6: gateway_result not persisted in workflow context
**Before:** Proposal result stored; no gateway result
**After:** `WorkflowExecutionResult.gateway_result` added with to_dict/from_dict

---

## Remaining Gaps After Sprint 2.22

| Gap | Node | Priority |
|---|---|---|
| REASONING not wired into workflow | REASONING | Medium |
| VERIFY node not implemented | VERIFY | High |
| HUMANAPPROVER integration (Freshdesk/Slack) | HUMANAPPROVER | High |
| RESOLUTION module not implemented | RESOLUTION | Medium |
| NOTEGEN (resolution notes) | NOTEGEN | Medium |
| USERRESPONSE generator | USERRESPONSE | Medium |
| CLOSE (automated ticket closure) | CLOSE | Medium |
| LOGTOOL, SUMMARYTOOL, VIDEOTOOL (live APIs) | Multiple tools | Low (mock OK for now) |
| ASANA integration | ASANA | Low |
| LLM integration | LLM | Future |
| VISION module | VISION | Future |
| Clarification engine fully wired | CLARIFICATION | Medium |
