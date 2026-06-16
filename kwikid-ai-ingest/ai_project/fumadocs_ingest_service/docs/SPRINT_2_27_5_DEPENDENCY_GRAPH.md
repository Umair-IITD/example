# Sprint 2.27.5 Dependency Graph

**Date:** 2026-06-16
**Sprint:** 2.27.5

---

## Module Dependency Tree (New Components)

```
case_engine/
├── integrations/                          [Sprint 2.27.5 NEW]
│   ├── __init__.py                        → RouterResult, RouterService, build_router_service
│   └── router_service.py
│       └── imports: case_engine/adapters/ (AdapterRouter, AdapterRequest, AdapterExecutionResult)
│
├── knowledge/                             [Sprint 2.27.5 EXTENDED]
│   ├── __init__.py                        → KnowledgeOrchestrator, UnifiedKnowledgeBundle, RAGEvidence, SOPEvidence
│   ├── orchestrator.py                    [NEW]
│   │   └── imports: case_engine/knowledge/unified_bundle.py
│   │   └── imports: case_engine/knowledge/knowledge_service.py (existing)
│   └── unified_bundle.py                  [NEW]
│       └── imports: (stdlib only)
│
├── response_generation/                   [Sprint 2.27.5 NEW]
│   ├── __init__.py                        → ResponseContext, ResponseDraft, ResponseMetadata, ResponseType, build_response_generation_service
│   ├── models.py                          [NEW]
│   │   └── imports: (stdlib only)
│   └── service.py                        [NEW]
│       └── imports: case_engine/response_generation/models.py
│       └── imports: case_engine/models.py (AuditEventType — optional)
│
├── engineering/                           [Sprint 2.27.5 NEW]
│   ├── __init__.py                        → EngineeringEscalationResult, EngineeringEscalationService, EngineeringPriority, EngineeringStatus, EngineeringTicket, build_engineering_escalation_service
│   ├── models.py                          [NEW]
│   │   └── imports: (stdlib only)
│   └── service.py                        [NEW]
│       └── imports: case_engine/engineering/models.py
│       └── imports: case_engine/models.py (AuditEventType — optional)
│
├── runtime/                               [Sprint 2.27.5 NEW — inside case_engine/]
│   ├── __init__.py                        → AgentExecutionResult, AgentStatus, SupportAgentRuntime, build_support_agent_runtime
│   ├── agent_models.py                    [NEW]
│   │   └── imports: (stdlib only)
│   └── support_agent_runtime.py          [NEW]
│       └── imports: case_engine/runtime/agent_models.py
│       └── imports: case_engine/case_state.py
│       └── imports: case_engine/response_generation/
│       └── imports: case_engine/engineering/
│       └── imports: case_engine/models.py (Case, AuditEventType)
│
├── ticket_orchestration/                  [Sprint 2.27.5 NEW]
│   ├── __init__.py                        → TicketContext, TicketLifecycleState, TicketOrchestrationResult, TicketOrchestrator, build_ticket_orchestrator
│   ├── models.py                          [NEW]
│   │   └── imports: (stdlib only)
│   └── orchestrator.py                   [NEW]
│       └── imports: case_engine/ticket_orchestration/models.py
│       └── imports: case_engine/runtime/support_agent_runtime.py
│       └── imports: case_engine/service.py (CaseService)
│
├── models.py                              [EXTENDED — 7 new AuditEventType values]
└── audit.py                               [EXTENDED — 7 new log_* methods]
```

---

## runtime/ (top-level) Changes

```
runtime/
└── assembly.py                            [EXTENDED]
    ├── ProductionRuntime (27 → 33 fields)
    │   ├── router_service                 [NEW]
    │   ├── knowledge_orchestrator         [NEW]
    │   ├── response_generation_service    [NEW]
    │   ├── engineering_escalation_service [NEW]
    │   ├── support_agent_runtime          [NEW]
    │   └── ticket_orchestrator            [NEW]
    ├── _build_adapter_stack(audit_logger) [FIXED — audit_logger now passed to AdapterRouter]
    └── _build_workflow_services()         [EXTENDED — steps 11–16 build new services]
```

---

## Service Construction Order (in `_build_workflow_services()`)

```
Step  1: AuditLogger
Step  2: SupabaseRuntime
Step  3: ActionRepository
Step  4: AuditRepository
Step  5: ActionGateway
Step  6: AdapterRegistry + AdapterRouter  ← uses audit_logger (fixed Sprint 2.27.5)
Step  7: ExecutionService
Step  8: ActionRuntime
Step  9: WorkflowEngine
Step 10: CaseService
Step 11: RouterService                    ← NEW
Step 12: KnowledgeOrchestrator            ← NEW
Step 13: ResponseGenerationService        ← NEW
Step 14: EngineeringEscalationService     ← NEW
Step 15: SupportAgentRuntime              ← NEW (depends on steps 10, 13, 14)
Step 16: TicketOrchestrator               ← NEW (depends on steps 10, 15)
```

---

## Data Flow: Ticket → Resolution

```
TicketContext
  │
  ▼
TicketOrchestrator.process_ticket()
  │  opens case: CaseService.open_case()
  │
  ▼
SupportAgentRuntime.run_case(case, message_text)
  │
  ├─ CLASSIFY  ─────► CaseService.classify_case()
  │                     └─ sets case.topic, case.confidence
  │
  ├─ RECEIVE   ─────► CaseService.receive_message()
  │                     └─ returns MessageProcessingResult
  │                          ├─ all_slots_filled=False → CLARIFY (early return)
  │                          └─ all_slots_filled=True  → continue
  │
  ├─ WORKFLOW  ─────► CaseService.start_workflow(case, playbook_name)
  │                     └─ WorkflowEngine.start()
  │                          ├─ INVESTIGATE steps → InvestigationService
  │                          ├─ KNOWLEDGE step  → KnowledgeService
  │                          ├─ PROPOSE_ACTION  → ActionGateway
  │                          └─ EXECUTE         → ExecutionService → AdapterRouter
  │
  ├─ L2CHECK   ─────► _needs_engineering_escalation(topic, workflow_result)
  │                     ├─ False → skip
  │                     └─ True  → ASANACREATE
  │
  ├─ ASANACREATE ──► EngineeringEscalationService.create_ticket()
  │                     ├─ In-memory store (Sprint 2.27.5)
  │                     └─ Asana client call (Sprint 2.28)
  │
  └─ USERRESPONSE ► ResponseGenerationService.generate(ResponseContext)
                      ├─ Template engine (deterministic, Sprint 2.27.5)
                      └─ LLM generator callable (Sprint 2.28)
  │
  ▼
AgentExecutionResult
  │
  ▼
TicketOrchestrationResult (CLOSED / WAITING / ESCALATED / FAILED)
```

---

## Audit Event Flow

```
SupportAgentRuntime._emit_agent_started()   → AGENT_RUN_STARTED
  │
  [pipeline steps]
  │
SupportAgentRuntime._emit_agent_completed() → AGENT_RUN_COMPLETED
  │
  [on knowledge orchestration]
AuditLogger.log_knowledge_orchestration_completed() → KNOWLEDGE_ORCHESTRATION_COMPLETED
  │
  [on response generation]
AuditLogger.log_response_generated()         → RESPONSE_GENERATED
  │
  [on engineering escalation create]
AuditLogger.log_engineering_escalation_created() → ENGINEERING_ESCALATION_CREATED
  │
  [on engineering escalation resolve]
AuditLogger.log_engineering_escalation_resolved() → ENGINEERING_ESCALATION_RESOLVED
  │
  [on action routing]
AuditLogger.log_action_routed()              → (adapter routing event — existing type)
```

---

## Circular Import Prevention

All new modules use `from __future__ import annotations` for forward references. Optional service imports are deferred to function bodies (never at module level). The dependency direction is strictly:

```
ticket_orchestration → runtime/support_agent_runtime → response_generation, engineering
runtime/support_agent_runtime → case_engine/service, case_engine/models
response_generation → (stdlib only)
engineering → (stdlib only)
knowledge/orchestrator → knowledge/unified_bundle, knowledge/knowledge_service
integrations/router_service → adapters/ (TYPE_CHECKING only at module level)
```

No circular imports exist. `case_engine/runtime/` is entirely independent of `runtime/` (top-level assembly).

---

## ProductionRuntime Field Count

| Sprint | Fields | Delta |
|--------|--------|-------|
| 2.25 | 22 | +3 metrics fields |
| 2.26 | 24 | +2 knowledge fields |
| 2.27 | 27 | +3 adapter fields |
| **2.27.5** | **33** | **+6 convergence service fields** |
