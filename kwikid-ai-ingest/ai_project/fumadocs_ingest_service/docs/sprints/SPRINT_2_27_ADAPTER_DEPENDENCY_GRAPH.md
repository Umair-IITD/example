# Sprint 2.27 Adapter Dependency Graph

**Date:** 2026-06-15

---

## Module Dependency Tree

```
case_engine/adapters/
  models.py                 ← AdapterType, AdapterOperation, AdapterStatus,
  │                            AdapterRequest, AdapterResponse, AdapterExecutionResult
  │
  base.py                   ← abstract Adapter(ABC)
  │   depends: models.py
  │
  freshdesk_adapter.py      ← FreshdeskAdapter(Adapter)
  │   depends: base.py, models.py
  │   supports: READ, WRITE, UPDATE, CREATE, EXECUTE
  │
  portal_adapter.py         ← AdminPortalAdapter(Adapter)
  │   depends: base.py, models.py
  │   supports: READ, WRITE, UPDATE, CREATE, EXECUTE
  │
  asana_adapter.py          ← AsanaAdapter(Adapter)
  │   depends: base.py, models.py
  │   supports: READ, CREATE, UPDATE
  │
  monitoring_adapter.py     ← MonitoringAdapter(Adapter)
  │   depends: base.py, models.py
  │   supports: READ, WRITE, EXECUTE
  │
  adapter_registry.py       ← AdapterRegistry, DuplicateAdapterError
  │   depends: base.py, models.py
  │   uses (optional): FreshdeskAdapter, AdminPortalAdapter, AsanaAdapter, MonitoringAdapter
  │                    (via importlib in build_default())
  │
  adapter_router.py         ← AdapterRouter
  │   depends: adapter_registry.py, models.py
  │   TYPE_CHECKING: case_engine.audit.AuditLogger
  │
  execution_adapter.py      ← AdapterBackedExecutionAdapter, routing table
  │   depends: adapter_router.py, models.py
  │   depends: case_engine.execution.executor.ExecutionAdapter (base class)
  │
  __init__.py               ← re-exports all public symbols
```

---

## Adapter Operation Support Matrix

| Adapter | READ | WRITE | UPDATE | CREATE | SEARCH | EXECUTE |
|---------|------|-------|--------|--------|--------|---------|
| FreshdeskAdapter | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| AdminPortalAdapter | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| AsanaAdapter | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| MonitoringAdapter | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |

---

## Action Type to Adapter Routing

```
action_type                →  AdapterType     Operation
──────────────────────────────────────────────────────
otp_resend                 →  FRESHDESK        EXECUTE
post_ticket_note           →  FRESHDESK        WRITE
close_ticket               →  FRESHDESK        UPDATE
update_ticket_status       →  FRESHDESK        UPDATE
vkyc_session_reset         →  ADMIN_PORTAL     EXECUTE
agent_session_refresh      →  ADMIN_PORTAL     EXECUTE
document_ocr_reprocess     →  ADMIN_PORTAL     EXECUTE
api_callback_retry         →  ADMIN_PORTAL     EXECUTE
emit_metric                →  MONITORING       WRITE
trigger_alert              →  MONITORING       EXECUTE
create_l2_ticket           →  ASANA            CREATE
(unknown)                  →  ADMIN_PORTAL     EXECUTE  [DEFAULT]
```

---

## AdapterRouter State Machine

```
route(AdapterRequest)
  ├─ _emit_started(request)         → audit log (no-op if audit_logger=None)
  │
  ├─ registry.get_adapter(type)
  │    is None? ──→ _emit_routing_failed() → return BLOCKED result
  │
  ├─ adapter.supports(operation)
  │    False? ────→ _emit_completed(UNSUPPORTED) → return UNSUPPORTED result
  │
  ├─ adapter.execute(request)
  │    raises? ───→ catch all → return FAILED result
  │
  └─ _emit_completed(response)      → audit log (no-op if audit_logger=None)
       return AdapterExecutionResult.from_response(request, response, adapter.adapter_name)
```

---

## AdapterExecutionResult Shape

```python
@dataclass(frozen=True)
class AdapterExecutionResult:
    result_id:    str             # UUID
    request:      AdapterRequest  # original request
    response:     AdapterResponse # adapter's response
    adapter_name: str             # which adapter handled it
    success:      bool            # True iff response.status == SUCCESS
    retryable:    bool            # True iff response.status == RETRYABLE
    executed_at:  str             # ISO timestamp
```

Accessed via `result.request.request_id`, `result.response.status`, `result.response.data`, etc.

---

## Test File Coverage Map

| Test File | Components Covered |
|-----------|-------------------|
| test_sprint227_adapter_models.py | AdapterType, AdapterOperation, AdapterStatus, AdapterRequest, AdapterResponse, AdapterExecutionResult |
| test_sprint227_adapter_base.py | Adapter ABC, supported_operations, supports() |
| test_sprint227_freshdesk_adapter.py | FreshdeskAdapter all operations |
| test_sprint227_portal_adapter.py | AdminPortalAdapter all operations |
| test_sprint227_asana_adapter.py | AsanaAdapter all operations |
| test_sprint227_monitoring_adapter.py | MonitoringAdapter all operations |
| test_sprint227_adapter_registry.py | AdapterRegistry, DuplicateAdapterError, build_default(), thread-safety |
| test_sprint227_adapter_router.py | AdapterRouter routing, BLOCKED, UNSUPPORTED, FAILED, audit, health |
| test_sprint227_execution_integration.py | AdapterBackedExecutionAdapter, routing table, build_execution_service(adapter_router=...) |
| test_sprint227_audit_events.py | 4 new AuditEventType values + 4 log_adapter_* methods |
| test_sprint227_admin_api.py | POST /admin/adapters/run, GET /admin/adapters/health |
| test_sprint227_assembly.py | ProductionRuntime 27 fields, _build_adapter_stack(), router health |
| test_sprint227_e2e.py | Full pipeline: action_type → AdapterBackedExecutionAdapter → AdapterRouter → Adapter → response |
