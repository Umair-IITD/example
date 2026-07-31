# Sprint 2.27 Runtime Dependency Graph

**Date:** 2026-06-15

---

## ProductionRuntime Fields (27 total after Sprint 2.27)

### Layer 1 — Infrastructure (Sprint 2.6)
```
metrics_collector       MetricsCollector
metrics_service         MetricsService(collector)
```

### Layer 2 — Core Persistence (Sprint 2.6)
```
repository              ActionRepository(supabase_client)
gateway                 ActionGateway(repository, metrics_service)
```

### Layer 3 — Audit (Sprint 2.10)
```
audit_repository        AuditRepository (from factory)
audit_service           AuditService(repository=audit_repository)
audit_logger            AuditLogger(repository=audit_repository)
```

### Layer 4 — Provider + Executor (Sprint 2.5–2.6)
```
provider_registry       ProviderRegistry
router                  ProviderRouter(provider_registry)
executor_registry       ActionExecutorRegistry
```

### Layer 5 — Runtime (Sprint 2.6)
```
runtime                 ActionRuntime(gateway, repository, executor_registry, ...)
worker                  ActionWorker(runtime, repository, ...)
health                  HealthService(provider_registry, executor_registry, runtime, worker)
watchdog                SLAWatchdog(gateway, repository, audit_service, metrics_service)
operations              ActionGatewayOperationsService(repository, metrics_service)
recovery                ActionGatewayRecoveryService(repository, gateway, audit_service)
```

### Layer 6 — Adapter Framework (Sprint 2.27) ← NEW
```
adapter_registry        AdapterRegistry.build_default()
                          ├─ FreshdeskAdapter    (READ/WRITE/UPDATE/CREATE/EXECUTE)
                          ├─ AdminPortalAdapter  (READ/WRITE/UPDATE/CREATE/EXECUTE)
                          ├─ AsanaAdapter        (READ/CREATE/UPDATE)
                          └─ MonitoringAdapter   (READ/WRITE/EXECUTE)

adapter_router          AdapterRouter(registry=adapter_registry, audit_logger=None)
```

### Layer 7 — Workflow Services (Sprint 2.26 + 2.27)
```
clarification_service   ClarificationService(audit_logger)
investigation_service   InvestigationService(tool_executor, audit_logger)
knowledge_service       KnowledgeService(audit_logger)
reasoning_service       ReasoningService(audit_logger)
action_proposal_service ActionProposalService(audit_logger)
action_gateway_service  ActionGatewayService(audit_logger)
execution_service       build_execution_service(adapter_router=adapter_router)  ← Sprint 2.27
workflow_engine         WorkflowEngine(all 7 services above)
playbook_registry       PlaybookRegistry.build()
case_service            CaseService(playbook_registry, action_gateway, wf_engine, audit_logger)
```

---

## Build Order Dependencies

```
1. MetricsCollector
2. MetricsService(1)
3. ActionRepository
4. ActionGateway(3, 2)
5. AuditRepository
6. AuditService(5)
7. AuditLogger(5)
8. ProviderRegistry
9. ProviderRouter(8)
10. ActionExecutorRegistry + register_all_executors(10, 9)
11. ActionRuntime(4, 3, 10, 6, 2)
12. ActionWorker(11, 3)
13. HealthService(8, 10, 11, 12)
14. SLAWatchdog(4, 3, 6, 2)
15. ActionGatewayOperationsService(3, 2)
16. ActionGatewayRecoveryService(3, 4, 6)
17. AdapterRegistry.build_default()            ← Sprint 2.27
18. AdapterRouter(17, audit_logger=None)       ← Sprint 2.27
19. [Workflow services 1-6, with 7=audit_logger]
20. ExecutionService via build_execution_service(adapter_router=18)  ← Sprint 2.27
21. WorkflowEngine(19, 20)
22. PlaybookRegistry.build()
23. CaseService(22, 4, 21, 7)
24. ProductionRuntime(all above)
```

---

## app.state Keys (after Sprint 2.27)

```
app.state.stack                   ProductionRuntime (full runtime)
app.state.processor               FreshdeskProcessor
app.state.authenticator           ApiKeyAuthenticator
app.state.audit_logger            AuditLogger (audit/logger.py)
app.state.audit_service           AuditService
app.state.audit_repository        AuditRepository
app.state.metrics_service         MetricsService
app.state.case_service            CaseService
app.state.playbook_registry       PlaybookRegistry
app.state.tool_registry           ToolRegistry
app.state.tool_executor           ToolExecutor
app.state.reasoning_engine        ReasoningService (legacy alias)
app.state.investigation_service   InvestigationService
app.state.clarification_service   ClarificationService
app.state.knowledge_service       KnowledgeService
app.state.action_proposal_service ActionProposalService
app.state.action_gateway_service  ActionGatewayService
app.state.execution_service       ExecutionService (adapter-backed)
app.state.workflow_engine         WorkflowEngine
app.state.adapter_registry        AdapterRegistry          ← Sprint 2.27 NEW
app.state.adapter_router          AdapterRouter            ← Sprint 2.27 NEW
```

---

## Admin API Endpoints Registered (Sprint 2.27)

| Method | Path | Handler | Auth |
|--------|------|---------|------|
| POST | `/admin/adapters/run` | `adapter_admin.run_adapter()` | ADMIN |
| GET | `/admin/adapters/health` | `adapter_admin.adapter_health()` | ADMIN |
