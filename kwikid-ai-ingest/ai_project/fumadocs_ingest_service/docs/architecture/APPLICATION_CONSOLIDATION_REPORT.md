# Application Consolidation Report — Sprint 2.11.2 Phase 3

**Date:** 2026-06-04
**Result:** Single production `create_app()` factory — 1846 tests, 0 failures

---

## Objective

Consolidate the two-application architecture into a single, authoritative `create_app()` factory in `app/main.py`. All Sprint 2.7–2.11 gateway routes must be reachable from the uvicorn entry point. The test suite must remain green.

---

## Changes Made

### `app/main.py` — Complete rewrite

**Before:** Module-level `app = FastAPI(...)` with RAG routes only registered via `@app.xxx(...)` decorators. No gateway routes. No injectable dependencies for tests.

**After:** `create_app()` factory with full dependency injection surface:

```python
def create_app(
    *,
    stack: Any = None,
    processor: Any = None,
    authenticator: Any = None,
    audit_logger: Any = None,
    audit_service: Any = None,
    audit_repository: Any = None,
    metrics_service: Any = None,
    skip_config_validation: bool = False,
) -> FastAPI:
```

Key structural changes:

1. **`_rag_router = APIRouter()`** — all 19 RAG routes moved to this router, registered with `@_rag_router.xxx()`. `create_app()` calls `local_app.include_router(_rag_router)`.

2. **Gateway router imports** — all 8 gateway routers imported at module level:
   ```python
   from api.routes import (
       actions as _gw_actions, admin as _gw_admin, audit as _gw_audit,
       health as _gw_health, metrics as _gw_metrics,
       watchdog as _gw_watchdog, webhook as _gw_webhook, worker as _gw_worker,
   )
   ```

3. **`_build_lifespan(skip_config_validation)` factory** — returns a lifespan context manager that:
   - Runs security validation (when `skip_config_validation=False`)
   - Pre-warms Supabase + OpenAI + ChatGenerator singletons
   - Pre-warms CaseService singleton
   - Builds action gateway stack from env (or uses injected values)
   - Wires audit logger/service, authenticator, metrics service to `app.state`

4. **Gateway routes registered with prefix** — to avoid path conflicts with RAG routes:
   ```python
   # Direct gateway routes (no prefix)
   local_app.include_router(_gw_actions.router,  tags=["Actions"])
   local_app.include_router(_gw_worker.router,   tags=["Worker"])
   local_app.include_router(_gw_watchdog.router, tags=["Watchdog"])
   local_app.include_router(_gw_audit.router,    tags=["Audit"])
   local_app.include_router(_gw_admin.router,    tags=["Admin"])
   local_app.include_router(_gw_webhook.router,  tags=["Webhooks"])

   # Gateway health/metrics under /gateway prefix (RAG owns /health and /metrics)
   local_app.include_router(_gw_health.router,   prefix="/gateway", tags=["Gateway Health"])
   local_app.include_router(_gw_metrics.router,  prefix="/gateway", tags=["Gateway Metrics"])
   ```

5. **Path-aware `_http_exception_handler`** — gateway paths use `{"error": {"code":..., "message":...}}` format; RAG paths use `{"detail": ...}` format.

6. **Module-level `app = create_app()`** — uvicorn still serves `app.main:app`.

### `api/app.py` — Replaced with thin wrapper

```python
from app.main import create_app  # noqa: F401
__all__ = ["create_app"]
```

All 19 test files using `from api.app import create_app` continue to work unchanged.

---

## Route Inventory (Post-Consolidation)

| Method | Path | Handler | Auth |
|--------|------|---------|------|
| GET | /health | RAG health (`{"status":"ok"}`) | None |
| GET | /health/live | RAG liveness | None |
| GET | /health/ready | RAG readiness (supabase + config) | None |
| GET | /metrics | RAG Prometheus metrics | None |
| GET | /ready | RAG readiness (legacy) | None |
| GET | /freshdesk/filter-options | Filter options | API key |
| POST | /ingest | Document ingestion | API key |
| POST | /query | Vector search | API key |
| POST | /chat | Conversational RAG | API key |
| GET | /chat/suggestions | Query suggestions | API key |
| POST | /train/chat | Knowledge card draft | API key |
| POST | /train/commit | Commit knowledge card | API key |
| GET | /train/cards | List knowledge cards | API key |
| GET | /train/cards/{id} | Get knowledge card | API key |
| DELETE | /train/cards/{id} | Delete knowledge card | API key |
| POST | /rag/chat | B1 RAG chat | API key |
| POST | /rag/chat/stream | B1 streaming chat | API key |
| POST | /freshdesk/webhook | FD webhook (HMAC) | None |
| POST | /feedback | Feedback ingestion | API key |
| POST | /webhook/{client} | Gateway webhook | HMAC |
| GET | /actions/{id} | Action inspection | APPROVER/OPERATOR |
| POST | /actions/{id}/approve | Approve action | APPROVER |
| POST | /actions/{id}/reject | Reject action | APPROVER |
| POST | /worker/tick | Worker execution | OPERATOR |
| POST | /watchdog/run | Watchdog expiry | OPERATOR/WATCHDOG |
| GET | /audit | Audit query | ADMIN |
| GET | /admin/dead-letter | Dead letter list | ADMIN |
| GET | /gateway/health | Gateway runtime health | None |
| GET | /gateway/health/live | Gateway liveness | None |
| GET | /gateway/health/ready | Gateway readiness | None |
| GET | /gateway/metrics | Gateway action metrics | None |

---

## Test Results

| Run | Passed | Failed | Notes |
|-----|--------|--------|-------|
| Before consolidation | 1846 | 0 | All tests used api/app.py (test-only factory) |
| After Phase 3 first attempt | 1686 | 160 | Rate limiter 429 + wrong exception format |
| After rate limiter + exception handler fix | 1827 | 19 | Health/metrics path conflicts + _ENABLED import |
| After Phase 3 final (all fixes) | 1846 | 0 | ✅ Production-ready |

---

## Middleware Stack (Outermost → Innermost)

```
RequestIdMiddleware (add_middleware — true outermost)
  ↓
api_key_auth_middleware (@middleware — outermost of @middleware pair)
  ↓ [early-exit for /gateway/*, /actions/*, /worker/*, /watchdog/*, /audit/*, /admin/*, /webhook/*]
_log_requests_dispatch (@middleware — innermost)
  ↓
FastAPI routing
```
