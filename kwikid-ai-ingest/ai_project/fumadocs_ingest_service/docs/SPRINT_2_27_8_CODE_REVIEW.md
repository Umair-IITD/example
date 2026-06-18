# Code Review — Sprint 2.27.8

**Date:** 2026-06-16
**Reviewer:** Internal architecture review
**Verdict:** APPROVED — no blocking issues

---

## Files Reviewed

1. `api/routes/tickets.py` — Golden Path API
2. `runtime/startup_validation.py` — Startup Validation Framework
3. `runtime/invariants.py` — Runtime Invariants
4. `case_engine/runtime/agent_models.py` — SupportAgentMode
5. `case_engine/runtime/support_agent_runtime.py` — DRY_RUN integration
6. `case_engine/audit.py` — Sprint 2.27.8 audit methods
7. `runtime/assembly.py` — startup_validation wiring
8. `api/routes/webhook.py` — NON_PRODUCTION_PATH marker

---

## Security Review

### ✅ PASS — No Secrets Exposed

- Audit log entries (`action_detail`, `error_msg` fields) contain only structured metadata — no API keys, no credentials
- The DRY_RUN audit methods log `simulated=True` and `reason` strings — no runtime values that could contain secrets
- `startup_validation_failed` logs tier and service name only — not runtime configuration values
- `invariant_violation` logs invariant name and violation detail — not internal state

### ✅ PASS — HMAC Comparison Preserved

No new API key comparisons introduced. All existing `hmac.compare_digest()` calls in `security/` are untouched.

### ✅ PASS — Auth on All New Endpoints

All 5 endpoints in `api/routes/tickets.py` use `Depends(require_operator)`. No unauthenticated routes added.

### ✅ PASS — No Command Injection Surface

No subprocess calls, shell commands, or user-controlled string interpolation in new code.

### ✅ PASS — No SQL Injection Surface

No raw SQL in new Python code. SQL migrations use `ALTER TABLE` / `DROP CONSTRAINT` — no user-controlled input.

---

## Code Quality

### ✅ tickets.py

- Each handler is short and readable (< 30 lines)
- `_get_orchestrator()` helper cleanly separates the 503 logic
- Lazy import `from case_engine.ticket_orchestration import TicketContext` inside handler — avoids circular import risk
- JSONResponse used consistently — no implicit Pydantic serialization at the route level
- Logging includes `ticket_id`, `lifecycle_state`, `success` — all structured, no PII

### ✅ startup_validation.py

- `RuntimeValidationCheck` and `RuntimeValidationResult` are frozen dataclasses — immutable, hashable
- `validate_production_runtime()` never raises — all exceptions caught in `validate_playbooks()` and `validate_adapters()`
- `_check_service()` uses `getattr(runtime, name, None)` — safe against missing attributes
- Playbook terminal step validation uses string comparison against a frozen set — no import required at runtime (lazy import with fallback)
- Logging tiers are correct: PASSED → INFO, FAILED CRITICAL → ERROR, FAILED IMPORTANT → WARNING

### ✅ invariants.py

- `InvariantViolation.invariant_name` allows callers to catch and dispatch on specific invariant types
- All checks are pure functions — no side effects, no logging inside them
- `_TERMINAL_STEP_TYPES` is a module-level frozenset — fast membership test
- `_APPROVAL_REQUIRED_RISK_LEVELS` and `_NEVER_APPROVE_RISK_LEVELS` are named clearly

### ✅ agent_models.py (SupportAgentMode)

- `SupportAgentMode(str, Enum)` — comparison with plain strings works: `mode == "DRY_RUN"` is True
- `_mode_from_env()` handles all edge cases: missing key, empty string, case-insensitive, invalid value
- Falls back to DRY_RUN on any error — default-safe

### ✅ support_agent_runtime.py (DRY_RUN changes)

- DRY_RUN gate is at ASANACREATE (Step 6) only — all other steps run equally in both modes
- `_emit_dry_run_execution()` and `_emit_dry_run_action()` catch exceptions internally — audit failure cannot crash the agent run
- `engineering_result = None` explicitly set in DRY_RUN branch — caller can check `result.engineering_result is None` to detect DRY_RUN execution

### ✅ audit.py (new methods)

- All 7 new methods follow the same pattern as existing methods — `AuditEntry` built and `_write()` called
- `log_dry_run_route()` takes `case_id: str` (not a `Case` object) — correct for routing context
- `log_invariant_violation()` includes `error_code=f"INVARIANT_{invariant_name.upper()}"` — enables structured query in Supabase

---

## Concerns and Non-Issues

### Non-Issue: `metadata: dict = {}` mutable default in Pydantic

Pydantic handles mutable defaults correctly — each request gets a new dict instance. This is not the Python class variable mutation bug.

### Non-Issue: Lazy imports inside route handlers

`from case_engine.ticket_orchestration import TicketContext` inside the `process_ticket` handler avoids any circular import that might arise from assembly wiring. This is an accepted FastAPI pattern.

### Non-Issue: `try/except Exception` in route handlers

Broad exception catching at the HTTP boundary is correct. Exceptions must not leak to the caller. All exceptions are logged with full traceback via `LOGGER.exception()`.

### Minor: `startup_validation_result` field on non-frozen dataclass

`ProductionRuntime` is a regular (non-frozen) dataclass. Direct assignment `production_runtime.startup_validation_result = validation_result` works correctly. If `ProductionRuntime` were frozen, this would require `object.__setattr__`. Not a concern currently.

---

## Test Quality

| Test File | Coverage Style | Quality |
|-----------|---------------|---------|
| `test_sprint2278_startup_validation.py` | Unit + integration against mock runtimes | High — covers all tiers, edge cases (None services, empty registry) |
| `test_sprint2278_dry_run.py` | Unit — enum values, env var, step gating | High — covers both modes, audit assertions |
| `test_sprint2278_invariants.py` | Unit — each invariant condition | High — positive and negative cases |
| `test_sprint2278_golden_path.py` | HTTP API — TestClient against mock orchestrator | High — all endpoints, 503/500 guards, router structure |
| `test_sprint2278_conformance.py` | Structural — field counts, exports, module attributes | High — catches regressions in assembly and model structure |

---

## Verdict

**APPROVED.** No blocking issues. No security vulnerabilities introduced. All new code follows existing conventions. The system is ready for Freshdesk integration testing in DRY_RUN mode.
