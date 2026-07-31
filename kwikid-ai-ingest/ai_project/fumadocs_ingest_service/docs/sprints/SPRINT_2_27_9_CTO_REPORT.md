# Sprint 2.27.9 CTO Report — Multi-Tenant Client Resolution Layer

**Branch:** major-architecture-change  
**Sprint:** 2.27.9  
**Date:** 2026-06-17  
**Status:** COMPLETE

---

## Executive Summary

Sprint 2.27.9 delivers the Multi-Tenant Client Resolution Layer — a mandatory Layer 1.5 between Ticket Ingestion and the Case Engine. Every support ticket now resolves to a typed `TenantContext` before any automation proceeds. Unknown clients are hard-stopped and escalated to human review. The architecture supports unlimited tenants with zero code changes per new client.

---

## Part-by-Part Completion

### Part 1 — ClientResolver

**File:** `case_engine/tenant/resolver.py`

- `ClientResolver.resolve(email)` extracts domain → looks up TenantRegistry → builds TenantContext
- Raises `UnknownClientError` for unknown, malformed, or disabled tenants
- `resolve_or_none(email)` — soft variant (returns None instead of raising)
- `resolve_by_client_id(client_id)` — direct lookup without email
- `extract_domain(email)` — staticmethod, lowercase, handles edge cases
- PII protection: email is never logged; only domain is logged

### Part 2 — TenantRegistry

**File:** `case_engine/tenant/registry.py`

- Backed by two dicts: `_by_client_id` and `_by_domain` — zero if/else chains
- `lookup_by_domain(domain)` and `lookup_by_client_id(client_id)` — O(1) lookups
- `register(config)` — raises `ValueError` on duplicate client_id or domain
- `validate()` — returns list of errors (empty = valid)
- `count()`, `domain_count()`, `all_tenants()`, `all_domains()`, `all_client_ids()`
- Unity Bank pre-configured in `_DEFAULT_TENANTS` with 10 tools
- Adding new tenant: add one `TenantConfig` entry — zero other code changes

### Part 3 — TenantContext Propagation

**Flow:** `TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE`

- `TenantContext` is a frozen dataclass (immutable)
- Attached to `Case.tenant_context` after `open_case()` succeeds
- `Case.to_db_row()` excludes `tenant_context` (in-process only, not persisted)
- `TenantContext` carries: `client_id`, `client_name`, `domain`, `tenant_type`, `environment`, `enabled_tools`, `credentials_ref`, `workflow_overrides`, `portal_base_url`

### Part 4 — TenantAwareToolRegistry

**File:** `case_engine/tenant/tool_registry.py`

- `get_tools_for_client(client_id)` — sorted tuple from TenantConfig
- `get_tools_for_context(ctx)` — delegates to client_id lookup
- `is_tool_enabled(tool_name, client_id)` — True/False gate before every tool call
- `register_tool_for_client(tool_name, client_id)` — additive runtime extras
- `tools_diff(a, b)` — compare tool sets between two clients
- `tool_count_for_client(client_id)` — count of enabled tools

### Part 5 — TenantAdapter Architecture

**File:** `case_engine/tenant/adapters.py`

- `TenantAdapter` — abstract base: `authenticate()`, `health_check()`, `supported_tools()`, `execute()`
- All methods return result objects; none raise exceptions
- `UnityBankAdapter` — Sprint 2.27.9 stub: authenticate/health_check return success; execute returns `NOT_IMPLEMENTED`
- `_ADAPTER_MAP: dict[str, type[TenantAdapter]]` — data-driven factory (adding adapter = register in map)
- `build_adapter_for_context(ctx)` — factory from TenantContext
- `build_adapter_for_client(client_id)` — factory from client_id
- Sprint 2.28: Real Unity Bank API calls replace the stubs

### Part 6 — Unknown Client Handling

**File:** `case_engine/ticket_orchestration/orchestrator.py` (Step 1.5)

Hard-stop path:
1. `ClientResolver.resolve(email)` raises `UnknownClientError`
2. `audit.log_unknown_client(ticket_id, domain)` → `CLIENT_RESOLUTION_FAILED`
3. `audit.log_unknown_client_escalated(ticket_id, domain)` → `UNKNOWN_CLIENT_ESCALATED`
4. Returns `TicketOrchestrationResult(lifecycle_state=ESCALATED, success=False, error_code="UNKNOWN_CLIENT", case_id=None)`
5. Pipeline halts — no case opened, no investigation, no tool calls

Backward compatibility: `client_resolver=None` (default) → resolution is skipped entirely.

### Part 7 — Audit Integration

**File:** `case_engine/audit.py` (4 new methods)

| Method | Event Type | Notes |
|--------|-----------|-------|
| `log_client_resolved(ticket_id, client_id, client_name, domain)` | `CLIENT_RESOLVED` | outcome="SUCCESS" |
| `log_unknown_client(ticket_id, domain)` | `CLIENT_RESOLUTION_FAILED` | client="UNKNOWN", no email in detail |
| `log_tenant_context_attached(case_id, ticket_id, client_id, client_name, env, tool_count)` | `TENANT_CONTEXT_ATTACHED` | outcome="SUCCESS" |
| `log_unknown_client_escalated(ticket_id, domain, reason)` | `UNKNOWN_CLIENT_ESCALATED` | client="UNKNOWN", outcome="ESCALATED" |

6 new `AuditEventType` values added to `case_engine/models.py`:
- `CLIENT_RESOLVED`
- `CLIENT_RESOLUTION_FAILED`
- `TENANT_CONTEXT_ATTACHED`
- `UNKNOWN_CLIENT_ESCALATED`
- `TENANT_REGISTRY_VALIDATED`
- `TENANT_REGISTRY_VALIDATION_FAILED`

**Total AuditEventType count: 80**

### Part 8 — Startup Validation

**File:** `runtime/startup_validation.py`

- `_TENANT_SERVICES = ("tenant_registry", "client_resolver", "tenant_tool_registry")`
- All three checked at **IMPORTANT** tier (not CRITICAL — backward compatible)
- `validate_tenant_registry(runtime)` — checks: registry not None, count ≥ 1, validate() clean, reports tenant count and domain count
- Integrated into `validate_production_runtime(runtime)`

### Part 9 — Tests

**9 test files, 323 tests, 0 failures**

| File | Tests | Focus |
|------|-------|-------|
| `test_sprint2279_client_resolution.py` | 43 | ClientResolver: resolve, resolve_or_none, resolve_by_client_id, extract_domain |
| `test_sprint2279_tenant_registry.py` | 42 | TenantRegistry: lookups, register, validate, counts, default registry |
| `test_sprint2279_tenant_context.py` | 38 | TenantContext: immutability, to_dict, Case field, db_row exclusion |
| `test_sprint2279_tenant_tool_registry.py` | 37 | TenantAwareToolRegistry: get_tools, is_enabled, register, tools_diff |
| `test_sprint2279_tenant_adapters.py` | 45 | TenantAdapter ABC, UnityBankAdapter stub, factories |
| `test_sprint2279_unknown_client.py` | 28 | Orchestrator ESCALATED path, audit events, no case opened |
| `test_sprint2279_audit_propagation.py` | 40 | All 4 new audit methods, AuditEventType values |
| `test_sprint2279_startup_validation.py` | 26 | validate_tenant_registry, IMPORTANT tier, full validation integration |
| `test_sprint2279_conformance.py` | 24 | flow_diagram compliance, field counts, backward compat, tools |

### Part 10 — Architecture Verification

Verified against `flow_diagram.mermaid` and `SUPPORT_OPERATIONS_BLUEPRINT.md`:

| Requirement | Status |
|-------------|--------|
| `TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE` | VERIFIED |
| Data-driven registry (no if/else chains) | VERIFIED |
| Adding new client = config only | VERIFIED |
| TenantContext frozen/immutable | VERIFIED |
| credentials_ref is reference, never actual secret | VERIFIED |
| Unknown client → hard stop → human escalation | VERIFIED |
| Audit: all events include client_id | VERIFIED |
| Startup validation: IMPORTANT tier (backward compat) | VERIFIED |
| Unity Bank ONLY in current rollout | VERIFIED |
| UnityBankAdapter: NOT_IMPLEMENTED stubs | VERIFIED |

---

## Test Results

| Scope | Result |
|-------|--------|
| Sprint 2.27.9 tests | **323 passed, 0 failed** |
| Full regression | **6048 passed, 1 pre-existing failure (unrelated)** |

Pre-existing failure: `test_stackoverflow_fidelity_pipeline.py::test_embed_text_contains_tags_url_and_images` — unrelated to Sprint 2.27.9.

---

## New Files Created

```
case_engine/tenant/__init__.py           # Package init with __all__
case_engine/tenant/models.py             # TenantType, TenantConfig, TenantContext, UnknownClientError
case_engine/tenant/registry.py           # TenantRegistry, _DEFAULT_TENANTS, build_default_tenant_registry()
case_engine/tenant/resolver.py           # ClientResolver, build_client_resolver()
case_engine/tenant/tool_registry.py      # TenantAwareToolRegistry, build_tenant_tool_registry()
case_engine/tenant/adapters.py           # TenantAdapter ABC, UnityBankAdapter, _ADAPTER_MAP, factories
tests/test_sprint2279_client_resolution.py
tests/test_sprint2279_tenant_registry.py
tests/test_sprint2279_tenant_context.py
tests/test_sprint2279_tenant_tool_registry.py
tests/test_sprint2279_tenant_adapters.py
tests/test_sprint2279_unknown_client.py
tests/test_sprint2279_audit_propagation.py
tests/test_sprint2279_startup_validation.py
tests/test_sprint2279_conformance.py
```

## Modified Files

```
case_engine/models.py                    # tenant_context field on Case; 6 new AuditEventType values
case_engine/audit.py                     # 4 new audit methods (log_client_resolved, etc.)
case_engine/ticket_orchestration/orchestrator.py  # Step 1.5 CLIENT RESOLUTION; client_resolver param
runtime/assembly.py                      # 3 new fields on ProductionRuntime; build steps 17/18/19
runtime/startup_validation.py            # _TENANT_SERVICES; validate_tenant_registry(); integration
```

---

## Key Design Decisions

1. **IMPORTANT (not CRITICAL) for tenant services** — Existing runtimes built without tenant services remain bootable. Resolution failure is caught at ticket-processing time, not startup.

2. **`Case.tenant_context: Any = None`** — Uses `Any` type to avoid importing from `case_engine.tenant` in `case_engine/models.py`, preventing circular imports. Type safety enforced at caller sites.

3. **`credentials_ref` as a reference key** — Never stores or logs actual API keys. The reference name points to a secrets store that Sprint 2.28 will integrate.

4. **UnityBankAdapter stubs** — All 10 tools return `NOT_IMPLEMENTED`. Sprint 2.28 adds real Unity Bank Admin API calls. Architecture is complete; only the integration layer is pending.

5. **Backward compatibility maintained** — `client_resolver=None` in `TicketOrchestrator` and `build_ticket_orchestrator()` means all existing callers continue to work unchanged.

---

## Sprint 2.28 Handoff

Sprint 2.28 will:
1. Integrate real Unity Bank Admin API calls into `UnityBankAdapter.execute()`
2. Wire `TenantAwareToolRegistry` into the investigation/tool-execution layer
3. Add `tenant_context` to workflow execution context so tool calls are tenant-gated
4. Implement `TENANT_REGISTRY_VALIDATED` / `TENANT_REGISTRY_VALIDATION_FAILED` audit events in the startup path
