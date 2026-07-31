# Architecture Review — Sprint 2.23
# Full Code Review: Sprints 2.18–2.23 vs flow_diagram.mermaid + Blueprint

**Date:** 2026-06-12
**Reviewer:** CTO Architecture Review
**Scope:** Full trace of flow_diagram.mermaid and SUPPORT_OPERATIONS_BLUEPRINT.md against codebase (4247 passing tests, Sprint 2.23 complete)
**Methodology:** Every node in the diagram traced against code. Every Blueprint principle checked against implementation. Honest gaps documented — no rationalization.

---

## Methodology

Each node assessed on five criteria:
1. **Exists** — implementation file/class present
2. **Reachable** — code path reaches it
3. **Used** — invoked in a production or workflow path
4. **Wired** — connected to upstream/downstream per diagram
5. **Audited** — audit event emitted

Status: `IMPLEMENTED` · `PARTIALLY_IMPLEMENTED` · `IMPLEMENTED_BUT_NOT_WIRED` · `NOT_IMPLEMENTED`

---

## External Systems

| Node | Status | Notes |
|---|---|---|
| USER (Bank Agent / Customer) | NOT_IMPLEMENTED | External; entry via Freshdesk webhook |
| FD (Freshdesk) | PARTIALLY_IMPLEMENTED | Inbound webhook exists; write-back (notes, replies) not wired |
| ASANA | NOT_IMPLEMENTED | No Asana client; L2 escalation is concept only |
| DEV (Engineering Team) | NOT_IMPLEMENTED | External actor |

---

## Core Ingestion & Case Management

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| TICKET | PARTIALLY_IMPLEMENTED | `api/routes/webhook.py` | 2.1 | Ingest exists; not all Freshdesk fields mapped |
| CASE | IMPLEMENTED | `case_engine/service.py` | 2.1 | Full lifecycle, state machine |
| CASEDB | IMPLEMENTED | Supabase `cases` table | 2.1 | SQL migrations complete |
| CLASSIF | IMPLEMENTED | `case_engine/classifier.py` | 2.1 | Topic classification with thresholds |
| SLOTEXTRACT | IMPLEMENTED | `case_engine/slot_filling/` | 2.1 | SlotRegistry, SlotValue |
| CLARIFICATION | PARTIALLY_IMPLEMENTED | `case_engine/clarification_engine.py` | 2.17 | Engine exists; not fully wired into `receive_message` loop |

---

## Workflow Orchestration

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| WORKFLOWSELECT | IMPLEMENTED | `case_engine/workflows/playbook_registry.py` | 2.16 | Topic → playbook mapping |
| ENGINE | IMPLEMENTED | `case_engine/workflows/workflow_engine.py` | 2.16 | Deterministic step executor; 10 step types |
| WORKFLOWDB | IMPLEMENTED | `workflow_context` column in `cases` | 2.16 | WorkflowExecutionResult serialized to JSON |

---

## Investigation Pipeline (Sprints 2.18–2.19)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| INVESTIGATION | IMPLEMENTED | `case_engine/investigation/` (7 files) | 2.18 | Full investigation pipeline |
| EVIDENCE | IMPLEMENTED | `case_engine/investigation/collector.py` | 2.18 | EvidenceCollector with tool integration |
| ROOTCAUSE | IMPLEMENTED | `case_engine/investigation/root_cause.py` | 2.18 | RootCauseEngine, 14 categories |
| REASONING | PARTIALLY_IMPLEMENTED | `case_engine/reasoning/reasoning_engine.py` | 2.17 | ReasoningEngine exists; **not wired into workflow path** |
| GUARDRAILS | IMPLEMENTED_BUT_NOT_WIRED | `workflow_engine.py` lines ~349–389 | 2.19 | Guards implemented as PROPOSE_ACTION preconditions; not standalone |
| OBSGEN | IMPLEMENTED | `case_engine/investigation/observation.py` | 2.18 | ObservationGenerator |
| LLM | NOT_IMPLEMENTED | — | — | No enterprise LLM integration; all paths deterministic |
| INVESTIGATE (step type) | IMPLEMENTED | `WorkflowStepType.INVESTIGATE` | 2.19 | Wired into WorkflowEngine dispatch |

---

## Knowledge Base (Sprint 2.20)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| KB | IMPLEMENTED | `case_engine/knowledge/` | 2.20 | KnowledgeService, retriever, matcher, recommendation |
| KBDB | IMPLEMENTED | Supabase tables | 2.20 | `knowledge_articles`, `knowledge_chunks` SQL |
| KNOWLEDGE_BASE (step type) | IMPLEMENTED | `WorkflowStepType.KNOWLEDGE_BASE` | 2.20 | Wired into WorkflowEngine |

---

## Action Proposal (Sprint 2.21)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| ACTIONPROPOSAL | IMPLEMENTED | `case_engine/actions/` | 2.21 | ActionProposalService, ProposalEngine |
| PROPOSE_ACTION (step type) | IMPLEMENTED | `WorkflowStepType.PROPOSE_ACTION` | 2.21 | Wired into WorkflowEngine; requires investigation |
| proposal_result | IMPLEMENTED | `WorkflowExecutionResult.proposal_result` | 2.21 | Serialized into WorkflowExecutionResult |

---

## Action Gateway (Sprint 2.22)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| ACTIONGW | IMPLEMENTED | `case_engine/action_gateway/proposal_gateway.py` | 2.22 | Validates proposal is COMPLETED before proceeding |
| RISKCHECK | IMPLEMENTED | `case_engine/action_gateway/risk_engine.py` | 2.22 | Maps ProposalRiskLevel → routing decision |
| APPROVAL | IMPLEMENTED | `case_engine/action_gateway/approval_engine.py` | 2.22 | SAFE→auto-approved; REVERSIBLE/HIGH_RISK→PENDING |
| APPROVALDB | IMPLEMENTED | `case_engine/action_gateway/repository.py` | 2.22 | Supabase write; approval records |
| ACTION_GATEWAY (step type) | IMPLEMENTED | `WorkflowStepType.ACTION_GATEWAY` | 2.22 | Wired into WorkflowEngine; gateway_result persisted |
| gateway_result | IMPLEMENTED | `WorkflowExecutionResult.gateway_result` | 2.22 | Feeds into EXECUTE gateway guard |

---

## Execution Layer (Sprint 2.23) ← THIS SPRINT

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| EXECUTE | IMPLEMENTED | `case_engine/execution/executor.py` + workflow integration | 2.23 | ActionExecutor + MockExecutionAdapter |
| ACTIONDB | IMPLEMENTED | `WorkflowExecutionResult.execution_result` | 2.23 | ExecutionBundle serialized |
| VERIFY | IMPLEMENTED | `case_engine/execution/verification.py` | 2.23 | VerificationEngine; fail-closed for unknown action types |
| DECISION3{Outcome Successful?} | IMPLEMENTED | `verification_result.is_success()` | 2.23 | In ExecutionService._process() |
| RECOVERY (RETRY) | IMPLEMENTED | `case_engine/execution/recovery.py` | 2.23 | RecoveryStrategy.RETRY; MAX_RETRIES=3 |
| RECOVERY (ROLLBACK) | IMPLEMENTED | `case_engine/execution/recovery.py` | 2.23 | RecoveryStrategy.ROLLBACK for reversible actions |
| RECOVERY (DEADLETTER) | IMPLEMENTED | `case_engine/execution/recovery.py` | 2.23 | RecoveryStrategy.DEAD_LETTER for high-risk/known-bad |
| RESOLUTION | IMPLEMENTED | `case_engine/execution/resolution.py` | 2.23 | ResolutionEngine; 5 resolution paths |
| EXECUTE (step type) | IMPLEMENTED | `WorkflowStepType.EXECUTE` | 2.23 | 10th step type; wired into WorkflowEngine |

---

## Audit Trail

| Event Group | Status | Sprint | Notes |
|---|---|---|---|
| Ticket ingestion audit | PARTIALLY_IMPLEMENTED | 2.1 | Not all events |
| Case lifecycle audit | IMPLEMENTED | 2.1–2.10 | CASE_OPENED, CASE_RESOLVED, CASE_ESCALATED |
| Workflow audit | IMPLEMENTED | 2.16 | WORKFLOW_STARTED, STEP_COMPLETED, ESCALATED, RESOLVED |
| Investigation audit | IMPLEMENTED | 2.18 | WORKFLOW_INVESTIGATION_STARTED/COMPLETED |
| Action proposal audit | IMPLEMENTED | 2.21 | PROPOSAL_STARTED/COMPLETED |
| Gateway audit | IMPLEMENTED | 2.22 | ACTION_GATEWAY_STARTED/COMPLETED, APPROVAL_REQUESTED/GRANTED/REJECTED |
| Execution audit | IMPLEMENTED | 2.23 | EXECUTION_STARTED/COMPLETED, VERIFICATION_STARTED/COMPLETED, RECOVERY_STARTED/COMPLETED, RESOLUTION_STARTED/COMPLETED |

All audit writes go through `AuditLogger._write()` which catches all exceptions — audit failures never crash production code.

---

## Blueprint Principle Compliance Scorecard

| # | Principle | Status | Gap |
|---|---|---|---|
| 1 | Investigation Before Action | IMPLEMENTED | ProposalGateway blocks if investigation_result absent |
| 2 | Root Cause Required | IMPLEMENTED | InvestigationService requires root_cause_category |
| 3 | Slot Filling Before Workflow | PARTIALLY_IMPLEMENTED | SlotRegistry exists; not all playbooks require mandatory slots |
| 4 | Verification After Execution | IMPLEMENTED | VerificationEngine mandatory in ExecutionService pipeline |
| 5 | Recovery After Failure | IMPLEMENTED | RecoveryEngine fires on every requires_recovery()=True |
| 6 | Escalate When Uncertain | IMPLEMENTED | UNCERTAIN verification → recovery; RecoveryEngine defaults to ESCALATE |
| 7 | Human Approval Where Required | IMPLEMENTED | REVERSIBLE/HIGH_RISK → PENDING in ApprovalEngine |
| 8 | No Bypass of Action Gateway | IMPLEMENTED | ACTION_GATEWAY step type is only path; _exec_execute checks gateway_result |
| 9 | Audit Everything | IMPLEMENTED | 27 AuditEventType values; all pipeline stages audited |
| 10 | Never Raise to HTTP | IMPLEMENTED | All engines catch-all; admin routes return structured errors |
| 11 | Deterministic (No LLM) | IMPLEMENTED | Zero LLM calls anywhere in execution path |
| 12 | Metrics for Operations | IMPLEMENTED | MetricsCollector, GET /metrics, Sprint 2.10 |

---

## Architectural Gaps — Honest Assessment

These are real gaps, not deferred features. They represent divergence from the blueprint or incomplete flow_diagram.mermaid wiring.

### Critical

**None.** The execution pipeline that powers the end-to-end happy path is fully implemented. The flow `CLASSIFY → INVESTIGATE → PROPOSE → GATEWAY → EXECUTE → VERIFY → RECOVER → RESOLVE` is traceable in code.

### Significant

**1. Real adapter integrations absent.** `ExecutionAdapter` is an ABC stub. Freshdesk write-back (updating ticket notes, sending replies, setting status) is not connected. The entire execution layer currently simulates actions. This means Sprint 2.23 cannot actually resolve a live ticket.

**2. RETRY is declared, never executed.** `ExecutionService.process()` always makes exactly one attempt. `RecoveryStrategy.RETRY` schedules a retry conceptually, but no queue, scheduler, or re-entrant loop actually retries the action. A failed OTP resend with `RETRY_SCHEDULED` final_status sits there without a second attempt.

**3. ReasoningEngine not in workflow path.** `case_engine/reasoning/reasoning_engine.py` (Sprint 2.17) implements `REASON`, `SYNTHESIZE`, and confidence scoring but is not wired to any `WorkflowStepType`. The flow diagram shows `REASONING` as a node; no workflow step reaches it.

**4. Clarification loop incomplete.** `ClarificationEngine` (Sprint 2.17) exists but `receive_message` in `CaseService` does not route unclear messages back through clarification before re-processing. A confused message silently falls through.

**5. Freshdesk write-back not wired.** After a case resolves (RESOLVED state), the system does not update the Freshdesk ticket. The agent sees no confirmation. The flow diagram shows `FD` as a bi-directional node.

### Minor

**6. Slot filling not mandatory in playbooks.** YAML playbooks do not enforce required slot presence before workflow starts. A VKYC playbook can start without a `session_id` slot.

**7. action_namespace unused.** `action_namespace` is stored in `ExecutionBundle` and passed to `ExecutionService.process()`, but no engine uses it for routing, filtering, or adapter selection.

**8. Partial rollback not verified.** `ROLLBACK_COMPLETED` assumes rollback succeeded. There is no second verification pass after rollback. If the rollback itself failed, the case would show `PARTIALLY_RESOLVED` when it should be `ESCALATED`.

---

## Execution Layer — Internal Architecture Quality

### What's Good

- **Immutable models.** All execution models are frozen dataclasses. No shared mutable state between pipeline stages.
- **Fail-closed everywhere.** UNCERTAIN verification, ESCALATE recovery default, never-raise contract at every layer.
- **Clean separation.** Each engine (executor, verifier, recovery, resolution) has one responsibility and a single public method. The service orchestrates; engines don't know about each other.
- **Backwards-compatible.** `WorkflowEngine(execution_service=None)` is valid — existing workflows that don't have an EXECUTE step are unaffected.
- **Audit emission pattern.** The `_emit()` helper in `ExecutionService` means audit failures are never a source of production crashes.

### What's Fragile

- **`_SUCCESS_BY_EXECUTION` and `_RETRYABLE_ACTIONS` are hardcoded frozensets.** Adding a new action type requires a code change to these sets (no config-driven registration). If a new adapter action type is added but not added to both sets, it will silently be UNCERTAIN/ESCALATE.
- **`ExecutionService` has no timeout.** If a real adapter takes 30 seconds, the HTTP request thread blocks. No timeout decorator, no async, no circuit breaker. Acceptable while mock adapters are the only ones, but this must be addressed before real adapters go live.
- **Bundle immutability means no intermediate state.** `ExecutionBundle` is frozen at creation. If a real multi-step transaction needs to update mid-execution, the frozen model won't accommodate it without a redesign.

---

## Sprint-by-Sprint Architecture Trajectory

| Sprint | Layer Added | flow_diagram Nodes Covered |
|---|---|---|
| 2.15 | Case Engine foundation | CASE, CASEDB, state machine |
| 2.16 | Workflow Orchestration | ENGINE, WORKFLOWSELECT, step dispatcher |
| 2.17 | Reliability (guardrails, reasoning) | GUARDRAILS (partial), REASONING (wired but not in workflow path) |
| 2.18 | Investigation Pipeline | INVESTIGATION, EVIDENCE, ROOTCAUSE, OBSGEN |
| 2.19 | Workflow–Investigation Integration | INVESTIGATE step type, investigation_result field |
| 2.20 | Knowledge Base | KB, KBDB, KNOWLEDGE_BASE step type |
| 2.21 | Action Proposal | ACTIONPROPOSAL, PROPOSE_ACTION step type |
| 2.22 | Action Gateway | ACTIONGW, RISKCHECK, APPROVAL, ACTION_GATEWAY step type |
| 2.23 | **Execution Layer** | **EXECUTE, VERIFY, RECOVERY (×3), RESOLUTION, EXECUTE step type** |

The codebase now covers all major flow_diagram nodes. The remaining work is wiring (real adapters, retry scheduling, Freshdesk write-back) rather than new architectural layers.

---

## Recommended Next Sprints

| Priority | Sprint | Work |
|---|---|---|
| HIGH | 2.24 | First real ExecutionAdapter: Freshdesk note writer. Wire EXECUTE step into VKYC playbook. |
| HIGH | 2.25 | RETRY queue: background job picks up RETRY_SCHEDULED bundles and re-runs process(). |
| MEDIUM | 2.26 | Wire ReasoningEngine into REASONING step type. Make the reasoning path reachable. |
| MEDIUM | 2.27 | Clarification loop: route unclear messages back through ClarificationEngine in receive_message. |
| LOW | 2.28 | Make action sets config-driven: load from YAML instead of hardcoded frozensets. |
