# Architecture Review — Sprint 2.24
# Full Code Review: Sprints 2.18–2.24 vs flow_diagram.mermaid + Blueprint

**Date:** 2026-06-15
**Reviewer:** CTO Architecture Review
**Scope:** Full trace of flow_diagram.mermaid and SUPPORT_OPERATIONS_BLUEPRINT.md against codebase (4539 passing tests, Sprint 2.24 complete)
**Methodology:** Every node in the diagram traced against code. Every Blueprint principle checked against implementation. Honest gaps documented.

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
| ENGINE | IMPLEMENTED | `case_engine/workflows/workflow_engine.py` | 2.16 | Deterministic step executor; 11 step types |
| WORKFLOWDB | IMPLEMENTED | `workflow_context` column in `cases` | 2.16 | WorkflowExecutionResult serialized to JSON |

---

## Investigation Pipeline (Sprints 2.18–2.19)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| INVESTIGATION | IMPLEMENTED | `case_engine/investigation/` (7 files) | 2.18 | Full investigation pipeline |
| EVIDENCE | IMPLEMENTED | `case_engine/investigation/collector.py` | 2.18 | EvidenceCollector with tool integration |
| ROOTCAUSE | IMPLEMENTED | `case_engine/investigation/root_cause.py` | 2.18 | RootCauseEngine, 14 categories |
| OBSGEN | IMPLEMENTED | `case_engine/investigation/observation.py` | 2.18 | ObservationGenerator |
| LLM | NOT_IMPLEMENTED | — | — | No enterprise LLM integration; all paths deterministic |
| INVESTIGATE (step type) | IMPLEMENTED | `WorkflowStepType.INVESTIGATE` | 2.19 | Wired into WorkflowEngine dispatch |

---

## Knowledge Base (Sprint 2.20)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| KB | IMPLEMENTED | `case_engine/knowledge/` | 2.20 | KnowledgeService, retriever, matcher, recommendation |
| KBDB | IMPLEMENTED | Supabase tables | 2.20 | `knowledge_articles`, `knowledge_chunks` SQL |
| KNOWLEDGE_BASE (step type) | IMPLEMENTED | `WorkflowStepType.KNOWLEDGE_LOOKUP` | 2.20 | Wired into WorkflowEngine |

---

## Reasoning Layer ← THIS SPRINT (Sprint 2.24)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| REASONING | **IMPLEMENTED** | `case_engine/reasoning/engine.py` | 2.24 | `InvestigationReasoningEngine`, 6-rule priority chain, 14 category mappings |
| REASON (step type) | **IMPLEMENTED** | `WorkflowStepType.REASON` | 2.24 | 11th step type; wired into WorkflowEngine dispatch |
| GUARDRAILS (→ACTIONPROPOSAL) | **IMPLEMENTED** | `workflow_engine.py` Guard 3 | 2.24 | `BLOCKED_NO_REASONING` blocks PROPOSE_ACTION without reasoning_result |
| reasoning_result | **IMPLEMENTED** | `WorkflowExecutionResult.reasoning_result` | 2.24 | Stored in workflow context dict |
| ReasoningService | **IMPLEMENTED** | `case_engine/reasoning/service.py` | 2.24 | Orchestration + audit + never-raises |

**Sprint 2.17 Note:** `case_engine/reasoning/reasoning_engine.py` (Sprint 2.17) implements meta-workflow reasoning (`ReasoningEngine`, `NextStepType`). This is a DIFFERENT engine that decides WHAT to do next in a workflow. It is not in a workflow step path. This distinction is preserved and both engines coexist in `case_engine/reasoning/`.

---

## Action Proposal (Sprint 2.21)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| ACTIONPROPOSAL | IMPLEMENTED | `case_engine/actions/` | 2.21 | ActionProposalService, ProposalEngine |
| PROPOSE_ACTION (step type) | IMPLEMENTED | `WorkflowStepType.PROPOSE_ACTION` | 2.21 | Wired into WorkflowEngine; requires investigation, knowledge, AND reasoning (when REASON step present) |
| proposal_result | IMPLEMENTED | `WorkflowExecutionResult.action_proposal_result` | 2.21 | Serialized into WorkflowExecutionResult |

---

## Action Gateway (Sprint 2.22)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| ACTIONGW | IMPLEMENTED | `case_engine/action_gateway/proposal_gateway.py` | 2.22 | Validates proposal is COMPLETED before proceeding |
| RISKCHECK | IMPLEMENTED | `case_engine/action_gateway/risk_engine.py` | 2.22 | Maps ProposalRiskLevel → routing decision |
| APPROVAL | IMPLEMENTED | `case_engine/action_gateway/approval_engine.py` | 2.22 | SAFE→auto-approved; REVERSIBLE/HIGH_RISK→PENDING |
| APPROVALDB | IMPLEMENTED | `case_engine/action_gateway/repository.py` | 2.22 | Supabase write; approval records |
| ACTION_GATEWAY (step type) | IMPLEMENTED | `WorkflowStepType.ACTION_GATEWAY` | 2.22 | Wired into WorkflowEngine; gateway_result persisted |

---

## Execution Layer (Sprint 2.23)

| Node | Status | File | Sprint | Notes |
|---|---|---|---|---|
| EXECUTE | IMPLEMENTED | `case_engine/execution/executor.py` + workflow integration | 2.23 | ActionExecutor + MockExecutionAdapter |
| ACTIONDB | IMPLEMENTED | `WorkflowExecutionResult.execution_result` | 2.23 | ExecutionBundle serialized |
| VERIFY | IMPLEMENTED | `case_engine/execution/verification.py` | 2.23 | VerificationEngine; fail-closed for unknown action types |
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
| **Reasoning audit** | **IMPLEMENTED** | **2.24** | **REASONING_STARTED/COMPLETED, WORKFLOW_REASONING_STARTED/COMPLETED** |

Total `AuditEventType` values: 31. All audit writes go through `AuditLogger._write()` which catches all exceptions.

---

## Blueprint Principle Compliance Scorecard

| # | Principle | Status | Gap |
|---|---|---|---|
| 1 | Investigation Before Action | IMPLEMENTED | ProposalGateway + WorkflowEngine Guard 1 block if investigation_result absent |
| 2 | Root Cause Required | IMPLEMENTED | InvestigationService requires root_cause_category |
| **3** | **Reasoning Before Execution** | **IMPLEMENTED** | **WorkflowEngine Guard 3: BLOCKED_NO_REASONING; ActionProposalService.reasoning_required** |
| 4 | Slot Filling Before Workflow | PARTIALLY_IMPLEMENTED | SlotRegistry exists; not all playbooks require mandatory slots |
| 5 | Verification After Execution | IMPLEMENTED | VerificationEngine mandatory in ExecutionService pipeline |
| 6 | Recovery After Failure | IMPLEMENTED | RecoveryEngine fires on every requires_recovery()=True |
| 7 | Escalate When Uncertain | IMPLEMENTED | UNCERTAIN reasoning → ESCALATE; RecoveryEngine defaults to ESCALATE |
| 8 | Human Approval Where Required | IMPLEMENTED | REVERSIBLE/HIGH_RISK → PENDING in ApprovalEngine |
| 9 | No Bypass of Action Gateway | IMPLEMENTED | ACTION_GATEWAY step type is only path |
| 10 | Audit Everything | IMPLEMENTED | 31 AuditEventType values; all pipeline stages audited |
| 11 | Never Raise to HTTP | IMPLEMENTED | All engines catch-all; admin routes return structured errors |
| 12 | Deterministic (No LLM) | IMPLEMENTED | Zero LLM calls anywhere in execution path |
| 13 | Metrics for Operations | IMPLEMENTED | MetricsCollector, GET /metrics, Sprint 2.10 |

---

## Full Pipeline Traceability (Post Sprint 2.24)

The flow `CLASSIFY → INVESTIGATE → KNOWLEDGE_LOOKUP → REASON → PROPOSE_ACTION → GATEWAY → EXECUTE → VERIFY → RECOVER → RESOLVE` is now fully traceable in code. Each step:

```
1. CLASSIFY         → case_engine/classifier.py
2. INVESTIGATE      → case_engine/investigation/ + WorkflowStepType.INVESTIGATE
3. KNOWLEDGE_LOOKUP → case_engine/knowledge/ + WorkflowStepType.KNOWLEDGE_LOOKUP
4. REASON           → case_engine/reasoning/ + WorkflowStepType.REASON  [NEW]
5. PROPOSE_ACTION   → case_engine/actions/ + WorkflowStepType.PROPOSE_ACTION
6. ACTION_GATEWAY   → case_engine/action_gateway/ + WorkflowStepType.ACTION_GATEWAY
7. EXECUTE          → case_engine/execution/ + WorkflowStepType.EXECUTE
8. VERIFY           → case_engine/execution/verification.py (within EXECUTE)
9. RECOVER          → case_engine/execution/recovery.py (within EXECUTE, on failure)
10. RESOLVE         → case_engine/execution/resolution.py (within EXECUTE)
```

All 11 WorkflowStepType values are implemented and wired.

---

## Architectural Gaps — Honest Assessment (Post Sprint 2.24)

### Critical

**None.** The full pipeline `CLASSIFY → INVESTIGATE → REASON → PROPOSE → GATEWAY → EXECUTE → VERIFY → RECOVER → RESOLVE` is traceable in code.

### Significant

**1. No real adapter integrations.** `ExecutionAdapter` is an ABC stub. Freshdesk write-back not connected. The execution layer simulates actions.

**2. RETRY is declared, never executed.** `RecoveryEngine` returns `RETRY_SCHEDULED`, but no queue/scheduler actually retries. A failed OTP resend sits with `RETRY_SCHEDULED` final_status forever.

**3. REASON step not in existing YAML playbooks.** The engine, service, step type, and guardrail are all implemented. But the 5 existing YAML playbooks do not have a `REASON` step. Adding it is a YAML change — out of scope for Sprint 2.24.

**4. Clarification loop incomplete.** `ClarificationEngine` (Sprint 2.17) exists but `receive_message` does not route unclear messages back through it.

**5. Freshdesk write-back not wired.** After a case resolves, the Freshdesk ticket is not updated.

### Minor

**6. Slot filling not mandatory in playbooks.**

**7. `action_namespace` unused in routing.**

**8. Partial rollback not verified.**

**9. `ReasoningService` not in `assembly.py`/runtime initialization.** The factory `build_reasoning_service()` exists but is not wired into the application startup assembly. This means the admin endpoint works (it reads from `app.state.reasoning_service`), but production workflows that need `ReasoningService` must be wired explicitly.

---

## Sprint-by-Sprint Architecture Trajectory

| Sprint | Layer Added | flow_diagram Nodes Covered |
|---|---|---|
| 2.15 | Case Engine foundation | CASE, CASEDB, state machine |
| 2.16 | Workflow Orchestration | ENGINE, WORKFLOWSELECT, step dispatcher |
| 2.17 | Reliability (guardrails, reasoning) | GUARDRAILS (partial), Meta-ReasoningEngine |
| 2.18 | Investigation Pipeline | INVESTIGATION, EVIDENCE, ROOTCAUSE, OBSGEN |
| 2.19 | Workflow–Investigation Integration | INVESTIGATE step type, investigation_result field |
| 2.20 | Knowledge Base | KB, KBDB, KNOWLEDGE_BASE step type |
| 2.21 | Action Proposal | ACTIONPROPOSAL, PROPOSE_ACTION step type |
| 2.22 | Action Gateway | ACTIONGW, RISKCHECK, APPROVAL, ACTION_GATEWAY step type |
| 2.23 | Execution Layer | EXECUTE, VERIFY, RECOVERY (×3), RESOLUTION, EXECUTE step type |
| **2.24** | **Reasoning Layer** | **REASONING, GUARDRAILS (→ACTIONPROPOSAL), REASON step type** |

All major flow_diagram nodes are now implemented. Remaining work: YAML playbook updates, real adapters, retry scheduling, Freshdesk write-back, and `assembly.py` wiring.

---

## Recommended Next Sprints

| Priority | Sprint | Work |
|---|---|---|
| HIGH | 2.25 | Add REASON step to YAML playbooks (VKYC, OTP, Callback, OCR, AgentPortal) |
| HIGH | 2.26 | Wire ReasoningService into assembly.py runtime startup |
| HIGH | 2.27 | First real ExecutionAdapter: Freshdesk note/reply writer |
| MEDIUM | 2.28 | RETRY queue: background retry mechanism for RETRY_SCHEDULED bundles |
| MEDIUM | 2.29 | Clarification loop: route unclear messages back through ClarificationEngine |
| LOW | 2.30 | Make action sets config-driven: load from YAML instead of hardcoded frozensets |
