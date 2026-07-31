# Action Gateway Deployment Verification — Sprint 2.11.2 Phase 4

**Date:** 2026-06-04
**Result:** All gateway routes verified reachable from production entry point

---

## Verification Objective

Confirm that every action gateway route registered in Sprint 2.7–2.11 is now reachable from `uvicorn app.main:app` — the actual production entry point.

---

## Gateway Route Reachability Matrix

| Route | Sprint Added | Previously Reachable | Now Reachable |
|-------|-------------|---------------------|---------------|
| `POST /webhook/{client}` | 2.7 | ❌ | ✅ |
| `GET /actions/{id}` | 2.7 | ❌ | ✅ |
| `POST /actions/{id}/approve` | 2.7 | ❌ | ✅ |
| `POST /actions/{id}/reject` | 2.7 | ❌ | ✅ |
| `POST /worker/tick` | 2.7 | ❌ | ✅ |
| `POST /watchdog/run` | 2.8 | ❌ | ✅ |
| `GET /audit` | 2.10 | ❌ | ✅ |
| `GET /admin/dead-letter` | 2.10 | ❌ | ✅ |
| `GET /gateway/health` | 2.11 | ❌ | ✅ |
| `GET /gateway/health/live` | 2.11 | ❌ | ✅ |
| `GET /gateway/health/ready` | 2.11 | ❌ | ✅ |
| `GET /gateway/metrics` | 2.10 | ❌ | ✅ |

---

## Authentication Verification

### RBAC Middleware (Gateway Routes)

Gateway routes bypass the global `api_key_auth_middleware` (which enforces `X-API-Key` for RAG routes). Instead, they use per-route `Depends()`:

| Role | Key Source | Routes Protected |
|------|-----------|-----------------|
| APPROVER | `ADMIN_API_KEYS` env var | `/actions/{id}/approve`, `/actions/{id}/reject` |
| OPERATOR | `ADMIN_API_KEYS` env var | `/worker/tick`, `/watchdog/run` |
| ADMIN | `ADMIN_API_KEYS` env var | `/audit`, `/admin/dead-letter` |
| WATCHDOG | `ADMIN_API_KEYS` env var | `/watchdog/run` |
| None | — | `/webhook/{client}` (HMAC), `/gateway/health*`, `/gateway/metrics` |

### Global Middleware Bypass

`app/security.py::_GATEWAY_PREFIXES` defines which path prefixes bypass the global rate limiter and auth middleware:

```python
_GATEWAY_PREFIXES: tuple[str, ...] = (
    "/actions", "/worker", "/watchdog", "/audit",
    "/admin", "/webhook", "/gateway",
)
```

When a request path starts with any of these prefixes, `api_key_auth_middleware` calls `await call_next(request)` immediately — no rate limiting, no API key check. Gateway routes then run their own per-route `Depends()` auth.

---

## State Injection Verification

Gateway routes read from `request.app.state`:

| State attribute | Set by | Used by |
|----------------|--------|---------|
| `app.state.stack` | lifespan (from env or inject) | `/actions`, `/worker`, `/watchdog`, `/admin` |
| `app.state.processor` | lifespan | `/webhook/{client}` |
| `app.state.authenticator` | lifespan | All gateway routes via `Depends()` |
| `app.state.audit_logger` | lifespan | `/watchdog/run` |
| `app.state.audit_service` | lifespan | `/audit` |
| `app.state.metrics_service` | lifespan (from stack) | `/gateway/metrics` |

In production, the lifespan builds these from environment variables. In tests, `create_app(stack=..., processor=..., authenticator=..., ...)` injects stubs directly.

---

## Test Evidence

All 1846 tests pass including:

- `tests/test_sprint27_api.py` (Sprint 2.7 gateway tests): 100+ tests covering webhook, actions, approval, rejection, worker tick
- `tests/test_sprint28_security.py` (Sprint 2.8 security + watchdog): RBAC, HMAC, watchdog
- `tests/test_sprint29_health.py`: Health endpoints
- `tests/test_sprint210_production.py`: Dead letter, audit repository, gateway metrics
- `tests/test_sprint211_operational.py`: Config consistency, audit retry/outbox, health providers

The test suite that was previously exercising test-only `api/app.py` now exercises the production `app/main.py::create_app()` via the `api/app.py` re-export shim.
