# Sprint 2.27 Architecture Review

**Date:** 2026-06-15
**Reviewer:** System Architecture (automated audit)

---

## 8-Phase Pipeline Compliance

The core CLARIFY → INVESTIGATE → KNOWLEDGE_LOOKUP → REASON → PROPOSE_ACTION →
ACTION_GATEWAY → EXECUTE → RESOLVE_CASE pipeline is fully intact.

Sprint 2.27 extends the EXECUTE node only — replacing MockExecutionAdapter with
AdapterBackedExecutionAdapter when an AdapterRouter is available at startup.

| Phase | Node | Status | Sprint 2.27 Impact |
|-------|------|--------|-------------------|
| 1 | CLARIFY | ✅ Unchanged | None |
| 2 | INVESTIGATE | ✅ Unchanged | None |
| 3 | KNOWLEDGE_LOOKUP | ✅ Unchanged | None |
| 4 | REASON | ✅ Unchanged | None |
| 5 | PROPOSE_ACTION | ✅ Unchanged | None |
| 6 | ACTION_GATEWAY | ✅ Unchanged | None |
| 7 | EXECUTE | ✅ Extended | Now routed via AdapterRouter |
| 8 | RESOLVE_CASE | ✅ Unchanged | None |

---

## Two-Level Adapter Architecture

Sprint 2.27 introduces a two-level adapter architecture. The existing `ExecutionAdapter`
ABC (Sprint 2.23) is preserved. A new `Adapter` ABC sits below it, handling per-system routing.

```
WorkflowEngine
  └─ ExecutionService.process()
       └─ ActionExecutor.execute()
            └─ AdapterBackedExecutionAdapter.run()   [NEW Sprint 2.27]
                 └─ AdapterRouter.route()            [NEW Sprint 2.27]
                      └─ Adapter.execute()           [NEW Sprint 2.27]
                           ├─ FreshdeskAdapter
                           ├─ AdminPortalAdapter
                           ├─ AsanaAdapter
                           └─ MonitoringAdapter
```

### Design Rationale

The two-level design was chosen to preserve:
1. **Backward compatibility**: `ExecutionService` and `ActionExecutor` are untouched
2. **Single responsibility**: `AdapterBackedExecutionAdapter` only translates action_type → (AdapterType, AdapterOperation)
3. **Drop-in replacement**: Any code using `MockExecutionAdapter` can switch to `AdapterBackedExecutionAdapter` with no interface change

---

## Component Interaction Contract

### AdapterRouter.route()

```
Input:  AdapterRequest(adapter_type, operation, payload, case_id, action_type)
Output: AdapterExecutionResult(result_id, request, response, adapter_name, success, retryable, executed_at)

States:
  BLOCKED     — adapter_type not in registry → no retry
  UNSUPPORTED — adapter exists but operation not supported → no retry
  SUCCESS     — adapter.execute() returned SUCCESS status
  FAILED      — adapter.execute() returned FAILED, or internal exception
  RETRYABLE   — adapter.execute() returned RETRYABLE status → caller may retry
```

### AdapterRegistry

```
Thread model: threading.RLock on all reads and writes
Fail-closed:  DuplicateAdapterError raised on duplicate type (programmer error, startup fails fast)
Never-raises: get_adapter(), list_adapters(), health_summary() all catch all exceptions
```

### Adapter Contract

```python
@abstractproperty adapter_name -> str
@abstractproperty adapter_type -> AdapterType
@abstractmethod  execute(request) -> AdapterResponse   # never raises
@abstractmethod  health_check() -> dict                # never raises
@abstractmethod  supported_operations() -> frozenset[AdapterOperation]

concrete:
  supports(operation) -> bool  # implemented in base, delegates to supported_operations()
```

---

## Assembly Startup Sequence

```
build_production_runtime()
  1. MetricsCollector + MetricsService
  2. ActionRepository (shared instance)
  3. ActionGateway
  4. Audit stack (repository, service, logger)
  5. Provider layer (FreshdeskProvider if configured)
  6. Executor registry
  7. ActionRuntime, ActionWorker, HealthService, SLAWatchdog, Operations, Recovery
  8. _build_adapter_stack()                    ← Sprint 2.27
     a. AdapterRegistry.build_default()       → registers 4 placeholder adapters
     b. AdapterRouter(registry, audit=None)   → ready to route
  9. _build_workflow_services(adapter_router=router)
     a-f. 6 workflow services (unchanged)
     g.   ExecutionService via build_execution_service(adapter_router=router)  ← Sprint 2.27
     h-j. WorkflowEngine, PlaybookRegistry, CaseService (unchanged)
 10. ProductionRuntime(27 fields)
```

---

## Safety Principle Verification

### 1. Never-raises Contract
- `AdapterRouter.route()`: all paths end in `try/except Exception → FAILED result`
- `AdapterRegistry.get_adapter()`, `list_adapters()`, `health_summary()`: all `try/except`
- `FreshdeskAdapter.execute()`: `try/except Exception → FAILED response`
- `AdminPortalAdapter.execute()`: same pattern
- `AsanaAdapter.execute()`: same pattern
- `MonitoringAdapter.execute()`: same pattern
- `AdapterBackedExecutionAdapter.run()`: `try/except Exception → failure dict`

### 2. Fail-closed Routing
- Unknown adapter type → `BLOCKED` result (not 200 OK, not silent drop)
- Unsupported operation → `UNSUPPORTED` result
- No default "succeed silently" path

### 3. No Secrets in Logs or Responses
- Adapters log: `operation.value`, `case_id`, `action_type` — no payloads at WARNING+
- Error messages include exception type and message but no stack traces in responses
- Admin API: `/admin/adapters/run` response omits full exception details

### 4. No External Calls (Sprint 2.27)
- All 4 adapters: pure in-process computation
- No HTTP clients, no network sockets, no external service calls
- `http_configured: False` in all health_check() responses

### 5. Thread Safety
- `AdapterRegistry._lock`: `threading.RLock` (reentrant, handles nested calls)
- All `register_adapter()`, `get_adapter()`, `list_adapters()`, `count()` use `with self._lock:`
- Concurrent test: 20 threads reading simultaneously — verified no crashes

### 6. Admin Auth
- `POST /admin/adapters/run`: `dependencies=[Depends(require_admin)]`
- `GET /admin/adapters/health`: `dependencies=[Depends(require_admin)]`
- No endpoint omits auth dependency

### 7. Audit Completeness
- `ADAPTER_REQUEST_STARTED` at route() entry
- `ADAPTER_REQUEST_COMPLETED` at route() exit (success or failure)
- `ADAPTER_ROUTING_FAILED` when adapter not registered
- `ADAPTER_HEALTH_CHECK` available for health monitoring

### 8. Idempotent Health Checks
- All `health_check()` methods are read-only; no side effects
- `AdapterRouter.is_healthy()` and `health_summary()` never mutate state

---

## Known Architecture Gaps (Sprint 2.28 Targets)

| Gap | Risk | Sprint 2.28 Mitigation |
|-----|------|------------------------|
| Placeholder adapters | No real work done | Replace execute() in each adapter class |
| audit_logger=None in AdapterRouter | Adapter events not logged | Wire case_engine.AuditLogger into router at build time |
| No retry on RETRYABLE | Failed transient ops not retried | Add RetryPolicy to ExecutionService |
| No circuit breaker | Unhealthy adapter called repeatedly | Add CircuitBreaker per AdapterType |
| No HTTP client injection | Cannot add real calls without structural change | Planned: inject `httpx.AsyncClient` per adapter |
