# Dependency Graph — Final (Sprint 2.27.8)

**Date:** 2026-06-16
**Status:** FINAL — Golden Path enforced, dependency graph stabilized

---

## Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│  INBOUND LAYER                                          │
│  api/routes/tickets.py  (Golden Path — Sprint 2.27.8)  │
│  api/routes/webhook.py  (NON_PRODUCTION_PATH — legacy)  │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│  ORCHESTRATION LAYER                                    │
│  case_engine/ticket_orchestration/orchestrator.py       │
│  TicketOrchestrator → manages lifecycle state machine   │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│  AGENT LAYER                                            │
│  case_engine/runtime/support_agent_runtime.py           │
│  SupportAgentRuntime → 8-step pipeline                  │
└──┬────────────┬──────────────┬──────────────┬───────────┘
   │            │              │              │
   ▼            ▼              ▼              ▼
CASE SERVICE  WORKFLOW     KNOWLEDGE     RESPONSE GEN
service.py    workflow_    orchestrator  response_gen/
              engine.py    /orchestrator service.py
                           .py
                     │
                     ▼
              ACTION GATEWAY
              action_gateway.py → action_runtime.py
                     │
                     ▼
              ADAPTER ROUTER
              provider_router.py → adapters/
                     │
                     ▼
              ENGINEERING
              ESCALATION
              engineering/service.py
```

---

## Module Dependency Map

### api/routes/tickets.py
```
→ security.dependencies (require_operator)
→ api.error_models (error_body)
→ case_engine.ticket_orchestration (TicketContext) [lazy import]
→ app.state.ticket_orchestrator [runtime wire]
```

### runtime/assembly.py (ProductionRuntime)
```
→ case_engine.service (CaseService)
→ case_engine.workflows.workflow_engine (WorkflowEngine)
→ case_engine.audit (AuditLogger)
→ case_engine.action_gateway (ActionGatewayService)
→ case_engine.action_runtime (ExecutionService)
→ case_engine.clarification.service (ClarificationService)
→ case_engine.investigation.service (InvestigationService)
→ case_engine.knowledge.service (KnowledgeService)
→ case_engine.reasoning.service (ReasoningService)
→ case_engine.provider_router (AdapterRouter)
→ case_engine.integrations.router_service (RouterService)
→ case_engine.knowledge.orchestrator (KnowledgeOrchestrator)
→ case_engine.response_generation.service (ResponseGenerationService)
→ case_engine.engineering.service (EngineeringEscalationService)
→ case_engine.runtime.support_agent_runtime (SupportAgentRuntime)
→ case_engine.ticket_orchestration.orchestrator (TicketOrchestrator)
→ runtime.startup_validation (validate_production_runtime) [Sprint 2.27.8]
```

### case_engine/runtime/support_agent_runtime.py
```
→ case_engine.runtime.agent_models (SupportAgentMode, AgentStatus, AgentExecutionResult)
→ case_engine.audit (AuditLogger)
→ case_engine.service (CaseService)
→ case_engine.engineering.service (EngineeringEscalationService)
→ case_engine.knowledge.orchestrator (KnowledgeOrchestrator) [optional]
→ case_engine.response_generation.service (ResponseGenerationService) [optional]
```

### runtime/startup_validation.py (NEW Sprint 2.27.8)
```
→ case_engine.workflows.models (WorkflowStepType) [lazy import]
(no other imports — intentionally isolated)
```

### runtime/invariants.py (NEW Sprint 2.27.8)
```
(no imports — pure logic, InvariantViolation extends Exception)
```

---

## Circular Dependency Status

**No circular imports detected.**

- `runtime/` imports from `case_engine/` but NOT vice versa
- `api/routes/` imports from `case_engine/` and `security/` but NOT vice versa
- `case_engine/audit.py` imports from `case_engine/models.py` only
- `runtime/startup_validation.py` has one lazy import of `WorkflowStepType` (guarded by try/except)

---

## External Dependencies

| Dependency | Usage | Required |
|-----------|-------|---------|
| Supabase | Case/action persistence, audit log | Yes (production) |
| OpenAI | LLM injection point (not used in DRY_RUN) | No (injectable) |
| Asana | Engineering escalation tickets | No (DRY_RUN default) |
| Freshdesk | Source of tickets (inbound via webhook) | Yes (integration) |
| Redis | Rate limiting (Sprint 2.5) | No (optional) |

---

## Key Invariants

1. **Golden Path Direction** — Traffic flows top-down: API → Orchestrator → Agent → Services → Adapters. Never bottom-up.
2. **No Cross-Layer Imports** — `case_engine/` never imports from `api/`, `runtime/`, or `tests/`.
3. **Never-Raises Boundary** — Every `run_case()`, `process_ticket()` is wrapped in try/except. Exceptions are caught, logged, and returned as error results.
4. **DRY_RUN Default** — `SupportAgentRuntime` defaults to `DRY_RUN`. Production requires explicit opt-in via `SUPPORT_AGENT_MODE=PRODUCTION`.
