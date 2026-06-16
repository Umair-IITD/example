# Sprint 2.26 Runtime Dependency Graph
**`ProductionRuntime` — All 25 Services**
Date: 2026-06-15 | Author: Engineering (Claude Code)

---

## Build Order (Dependency Resolution)

Services are built in strict dependency order inside `_build_workflow_services()` and `build_production_runtime()`. Each layer depends only on services from earlier layers.

```
Layer 0 — Infrastructure (no runtime dependencies)
  ├── MetricsCollector            [metrics/collector.py]
  └── MetricsService              [metrics/service.py]

Layer 1 — Storage
  ├── ActionRepository            [case_engine/action_repository.py]  ← supabase_client
  ├── AuditRepository             [audit/repository.py]               ← supabase_client
  └── AuditService                [audit/service.py]                  ← AuditRepository

Layer 2 — Logging + Gateway (depends on Layer 0+1)
  ├── AuditLogger                 [case_engine/audit.py]              ← AuditRepository
  └── ActionGateway               [case_engine/action_gateway.py]     ← ActionRepository, MetricsService

Layer 3 — Workflow Services (depends on Layer 2)
  ├── ClarificationService        [case_engine/clarification/]        ← AuditLogger
  ├── InvestigationService        [case_engine/investigation/]        ← AuditLogger
  ├── KnowledgeService            [case_engine/knowledge/]            ← (standalone)
  ├── ReasoningService            [case_engine/reasoning/]            ← (standalone)
  ├── ActionProposalService       [case_engine/action_proposal/]      ← AuditLogger
  ├── ActionGatewayService        [case_engine/action_gateway_svc/]   ← ActionGateway
  └── ExecutionService            [case_engine/execution/]            ← (standalone)

Layer 4 — Orchestration (depends on Layer 3)
  ├── PlaybookRegistry            [case_engine/workflows/playbook_registry.py]
  └── WorkflowEngine              [case_engine/workflows/workflow_engine.py]
        ├── clarification_service
        ├── investigation_service
        ├── knowledge_service
        ├── reasoning_service
        ├── action_proposal_service
        ├── action_gateway_service
        └── execution_service

Layer 5 — Case (depends on Layer 4)
  └── CaseService                 [case_engine/service.py]
        ├── repository            (ActionRepository)
        ├── audit_logger          (AuditLogger)
        ├── workflow_engine       (WorkflowEngine — injected post-build)
        └── playbook_registry     (PlaybookRegistry)

Layer 6 — Action Execution Stack (parallel to Layer 3-5)
  ├── ExecutorRegistry            [case_engine/action_runtime.py]
  ├── ProviderRegistry            [case_engine/provider_router.py]
  ├── ProviderRouter              [case_engine/provider_router.py]
  ├── ActionWorker                [case_engine/action_runtime.py]
  ├── HealthService               [case_engine/action_runtime.py]
  └── WatchdogService             [case_engine/action_runtime.py]

Layer 7 — Retry Framework (standalone, no runtime deps)
  ├── RetryRepository             [case_engine/retry/repository.py]
  ├── RetryScheduler              [case_engine/retry/scheduler.py]   ← RetryRepository
  ├── RetryWorker                 [case_engine/retry/worker.py]      ← RetryRepository, RetryScheduler
  └── DeadLetterQueue             [case_engine/retry/dlq.py]        ← RetryRepository
```

---

## `ProductionRuntime` Dataclass (25 Fields)

```python
@dataclass
class ProductionRuntime:
    # ── Layer 6: Action execution stack (Sprint 2.6–2.11) ──────────────────
    runtime:            Any   # ActionRuntime
    gateway:            Any   # ActionGateway
    repository:         Any   # ActionRepository
    executor_registry:  Any   # ExecutorRegistry
    provider_registry:  Any   # ProviderRegistry
    router:             Any   # ProviderRouter
    worker:             Any   # ActionWorker
    health:             Any   # HealthService
    watchdog:           Any   # WatchdogService
    audit_repository:   Any   # AuditRepository
    audit_service:      Any   # AuditService
    audit_logger:       Any   # AuditLogger
    metrics_service:    Any   # MetricsService
    operations:         Any   # OperationsService
    recovery:           Any   # RecoveryService

    # ── Layer 3–5: Workflow services (Sprint 2.26) ──────────────────────────
    clarification_service:    Any = None  # ClarificationService
    investigation_service:    Any = None  # InvestigationService
    knowledge_service:        Any = None  # KnowledgeService
    reasoning_service:        Any = None  # ReasoningService
    action_proposal_service:  Any = None  # ActionProposalService
    action_gateway_service:   Any = None  # ActionGatewayService
    execution_service:        Any = None  # ExecutionService
    workflow_engine:          Any = None  # WorkflowEngine
    playbook_registry:        Any = None  # PlaybookRegistry
    case_service:             Any = None  # CaseService
```

All 10 new fields default to `None` (fail-safe). A service that fails to build does not crash the runtime — only that service is unavailable.

---

## `app.state` Keys After Startup

The FastAPI `app.state` namespace is populated by the lifespan handler. After a successful startup with no Supabase/Freshdesk:

```
app.state.stack                    → ProductionRuntime (all 25 fields)
app.state.case_service             → CaseService (fully wired)
app.state.workflow_engine          → WorkflowEngine (7 services injected)
app.state.playbook_registry        → PlaybookRegistry (5 playbooks loaded)
app.state.clarification_service    → ClarificationService
app.state.investigation_service    → InvestigationService
app.state.knowledge_service        → KnowledgeService
app.state.reasoning_service        → ReasoningService
app.state.action_proposal_service  → ActionProposalService
app.state.action_gateway_service   → ActionGatewayService
app.state.execution_service        → ExecutionService
```

---

## `WorkflowEngine` Internal Dependency Injection

```python
WorkflowEngine(
    clarification_service  = stack.clarification_service,   # _exec_clarify()
    investigation_service  = stack.investigation_service,   # _exec_investigate()
    knowledge_service      = stack.knowledge_service,       # _exec_knowledge_lookup()
    reasoning_service      = stack.reasoning_service,       # _exec_reason()
    action_proposal_service = stack.action_proposal_service, # _exec_propose_action()
    action_gateway_service = stack.action_gateway_service,  # _exec_action_gateway()
    execution_service      = stack.execution_service,       # _exec_execute()
)
```

Each service is optional. When `None`, the corresponding step logs a WARNING and navigates `on_success` (SKIPPED path) — legacy compatibility is preserved.

---

## Retry Queue Dependencies

```
RetryRepository  ←──────── (in-memory, no external deps)
      ↑
RetryScheduler   ←──────── base_delay_s, max_delay_s, max_attempts
      ↑
RetryWorker      ←──────── RetryRepository, RetryScheduler, RetryExecutor (Protocol)
      │
      └─ RetryExecutor (Protocol)
             → Implement with: ActionGateway.execute() call  [next sprint]

DeadLetterQueue  ←──────── RetryRepository
```

The `RetryExecutor` protocol allows any object with `execute(job: RetryJob) -> Any` to be used as an executor. Sprint 2.27 will wire `ActionGateway` as the production executor.

---

## Case Lifecycle — All Entry Points

```
POST /webhook/freshdesk
  → CaseService.receive_message()
      → start_workflow()
          → WorkflowEngine.start(case, registry, slot_values)

POST /cases/{id}/resume                   ← ACTION_GATEWAY approval
  → CaseService.resume_workflow()
      → WorkflowEngine.resume_after_action()

POST /cases/{id}/clarify                  ← Customer slot response (Sprint 2.27)
  → CaseService.resume_clarification_workflow(case, updated_slots)
      → WorkflowEngine.resume_after_clarification(case, registry, slots)

RetryWorker.run_once()                    ← Background retry processing
  → RetryExecutor.execute(job)
      → ActionGateway.execute(action)     ← to be wired in Sprint 2.27
```

---

## Workflow Step Execution Map

```
WorkflowEngine._execute_step(step, ...)
  CLARIFY           → _exec_clarify()           → ClarificationService.clarify()
  INVESTIGATE       → _exec_investigate()       → InvestigationService.run()
  KNOWLEDGE_LOOKUP  → _exec_knowledge_lookup()  → KnowledgeService.search()
  REASON            → _exec_reason()            → ReasoningService.reason()
  PROPOSE_ACTION    → _exec_propose_action()    → ActionProposalService.propose()
  ACTION_GATEWAY    → _exec_action_gateway()    → ActionGatewayService.route()
  EXECUTE           → _exec_execute()           → ExecutionService.execute()
  RESOLVE_CASE      → _exec_resolve_case()      → (inline: RESOLVED state)
  ESCALATE_CASE     → _exec_escalate_case()     → (inline: ESCALATED state)
  REQUEST_APPROVAL  → _exec_request_approval()  → (inline: PAUSED state)
  CHECK_CONDITION   → _exec_check_condition()   → (inline: condition eval)
  COLLECT_INFORMATION → _exec_collect_info()    → (inline: slot collection)
```

---

## Audit Event Coverage (54 total)

```
Case lifecycle:      CASE_CREATED, CASE_UPDATED, CASE_CLOSED, CASE_ESCALATED, ...
Workflow:            WORKFLOW_STARTED, WORKFLOW_COMPLETED, WORKFLOW_FAILED, WORKFLOW_ESCALATED
                     WORKFLOW_STEP_COMPLETED, WORKFLOW_PAUSED, WORKFLOW_RESUMED
Clarification:       CLARIFICATION_STARTED, CLARIFICATION_COMPLETED
                     WORKFLOW_CLARIFICATION_STARTED, WORKFLOW_CLARIFICATION_COMPLETED
                     WORKFLOW_CLARIFICATION_RESUMED   ← Sprint 2.26
                     CLARIFICATION_ATTEMPT_INCREMENTED ← Sprint 2.26
Investigation:       WORKFLOW_INVESTIGATION_STARTED, WORKFLOW_INVESTIGATION_COMPLETED
Knowledge:           KNOWLEDGE_SEARCH_STARTED, KNOWLEDGE_SEARCH_COMPLETED
Reasoning:           REASONING_STARTED, REASONING_COMPLETED
Action:              ACTION_CREATED, ACTION_APPROVED, ACTION_REJECTED, ACTION_EXECUTED, ...
Security:            SECURITY_VIOLATION, AUTHENTICATION_FAILED, ...
```
