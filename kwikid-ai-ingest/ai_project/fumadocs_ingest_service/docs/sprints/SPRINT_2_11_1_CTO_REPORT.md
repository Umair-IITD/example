# Sprint 2.11.1 — CTO Report
# Production Readiness, Integration Validation & Observability Hardening

**Date:** 2026-06-04
**Sprint:** 2.11.1
**Role:** Principal Staff Engineer / SRE Lead
**Test result:** 1846 passed, 0 failures, 4 skipped

---

## Executive Summary

Three confirmed production bugs were root-caused and fixed. All three shared a single architectural root cause: **the Sprint 2.11 observability work was implemented in `api/app.py` (the action-gateway factory), while the actual production HTTP server is `app/main.py` (a completely separate FastAPI application)**. The two apps share no routes, middleware, or startup lifecycle.

| Issue | Symptom | Root Cause | Fix | Regression Tests |
|-------|---------|-----------|-----|-----------------|
| 1 | `GET /metrics` → 401 | `/metrics` absent from `_UNPROTECTED_PATHS`; `PROMETHEUS_ENABLED` gate returned 404 | Added `/metrics` to exempt set; removed 404 gate | 8 tests |
| 2 | `GET /health/live` → 404 / `GET /health/ready` → 404 | Routes registered only in `api/app.py`, not `app/main.py`; also absent from `_UNPROTECTED_PATHS` | Added routes to `app/main.py`; added paths to exempt set | 10 tests |
| 3 | `RequestIdMiddleware` not wired | Middleware added only to `api/app.py` lifespan | Registered in `app/main.py` as outermost middleware | 5 tests |

---

## Root Cause Investigation

### Confirmed: Two Separate FastAPI Applications

The repository contains two independent FastAPI applications:

**`app/main.py`** — The PRODUCTION HTTP server
- Serves: `/health`, `/ready`, `/metrics`, `/ingest`, `/query`, `/chat`, `/rag/chat`, `/freshdesk/webhook`, etc.
- Lifespan: initialises Supabase client, embedder, ChatGenerator singletons
- Auth: global `api_key_auth_middleware` (outermost `@app.middleware("http")`)
- Health: `GET /health` → `{"status": "ok"}`, `GET /ready` → Supabase + embeddings check

**`api/app.py`** — The ACTION GATEWAY factory (NOT in production HTTP path)
- Serves: `/health/*`, `/actions/*`, `/worker/*`, `/watchdog/*`, `/audit/*`, `/admin/*`, `/metrics`
- Created by `create_app()` — a factory function, not a module-level app
- Auth: per-route `Depends(require_admin/require_approver/require_operator)`
- This app was NOT being served as the production server

The Sprint 2.11 hardening work (RequestIdMiddleware B5, `/health/live` and `/health/ready` B1, MetricsService B3) was correctly implemented in `api/app.py` — but since production traffic routes through `app/main.py`, none of it reached production.

### Issue 1: `GET /metrics` → 401

**Evidence chain:**

1. `app/main.py` line 522-523 registers `api_key_auth_middleware` as outermost middleware
2. `app/security.py:_UNPROTECTED_PATHS` (frozenset, evaluated at module import) contained only 6 paths — `/metrics` was absent
3. Prometheus sends no `X-API-Key` header (it's a scraper, not a user)
4. `api_key_auth_middleware` hits the auth check, finds no key, returns 401 before the route handler is reached
5. Secondary issue: `app/main.py:metrics_endpoint()` was gated on `_PROMETHEUS_ENABLED` — would have returned 404 even for authenticated requests when `PROMETHEUS_ENABLED=false` (the default)

**Why this gate was wrong:** `CONFIGURATION_REFERENCE.md` explicitly states `PROMETHEUS_ENABLED` is "Informational flag (endpoint always active)". The `observability.metrics` module correctly implements this at the data layer (all `record_*` calls are no-ops when disabled) — the endpoint should always return a valid response, just empty data.

### Issue 2: `GET /health/live` → 404, `GET /health/ready` → 404

**Evidence chain:**

1. Sprint 2.11 B1 added `/health/live` and `/health/ready` routes to `api/routes/health.py`
2. Those routes are registered by `api/app.py::create_app()` via `app.include_router(health.router)`
3. The production server is `app/main.py` — an entirely separate `app = FastAPI(...)` instance at module level
4. `app/main.py` routes are: `GET /health`, `GET /ready`, `GET /metrics`, `POST /ingest`, etc.
5. No `/health/live` or `/health/ready` was registered in `app/main.py` → Starlette returns 404

**Confirming evidence:** `GET /ready → 200` still worked because `/ready` IS registered in `app/main.py` (line 706 pre-fix) AND is in `_UNPROTECTED_PATHS`. This is the exact fingerprint of the production-vs-factory split.

**Additional issue:** Even if the routes had been added to `app/main.py`, they would have gotten 401 because `/health/live` and `/health/ready` were absent from `_UNPROTECTED_PATHS`. Kubernetes probes send no auth headers.

### Issue 3: RequestIdMiddleware not wired

**Evidence chain:**

1. Sprint 2.11 B5 added `RequestIdMiddleware` in `api/middleware/request_id.py`
2. `api/app.py` registers it: `app.add_middleware(RequestIdMiddleware)`
3. `app/main.py` had no import or registration of `RequestIdMiddleware`
4. All production requests had no `X-Request-ID` correlation — making log tracing impossible

---

## Changes Made

### `app/security.py`

**Change:** Added `/metrics`, `/health/live`, `/health/ready` to `_UNPROTECTED_PATHS`

```python
# Before:
_UNPROTECTED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/freshdesk/webhook",
})

# After:
_UNPROTECTED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/health/live",    # Kubernetes liveness probes — no auth header
    "/health/ready",   # Kubernetes readiness probes — no auth header
    "/metrics",        # Prometheus scraping — no auth header
    "/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/freshdesk/webhook",
})
```

**Why safe:** Liveness and readiness endpoints never return sensitive data. The Prometheus `/metrics` endpoint exposes performance counters only — never secrets, keys, or PII. The design intent (CONFIGURATION_REFERENCE.md) is explicit: endpoint always active.

### `app/main.py`

**Change 1:** Added `from datetime import datetime, timezone` import (required for `/health/live`)

**Change 2:** Added `from api.middleware.request_id import RequestIdMiddleware` import

**Change 3:** Fixed `metrics_endpoint()` — removed `PROMETHEUS_ENABLED` gate

```python
# Before:
@app.get("/metrics")
async def metrics_endpoint():
    if not _PROMETHEUS_ENABLED:
        raise HTTPException(status_code=404, detail={...})
    result = get_metrics_response()
    if result is None:
        raise HTTPException(status_code=503, detail={...})
    data, content_type = result
    return Response(content=data, media_type=content_type)

# After:
@app.get("/metrics")
async def metrics_endpoint():
    result = get_metrics_response()
    if result is None:
        return Response(
            content="# KwikID AI Ingest Service\n# Metrics disabled. Set PROMETHEUS_ENABLED=true.\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    data, content_type = result
    return Response(content=data, media_type=content_type)
```

**Change 4:** Added `GET /health/live` route

```python
@app.get("/health/live")
async def health_live() -> dict:
    return {
        "alive": True,
        "service": "kwikid-ai-ingest",
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
```

**Change 5:** Added `GET /health/ready` route (checks Supabase connectivity, returns 200/503)

```python
@app.get("/health/ready")
async def health_ready() -> dict:
    settings = get_settings()
    checks: dict[str, Any] = {}
    ok = True
    # ... Supabase health check ...
    body = {"ready": ok, "checks": checks, "checked_at": ...}
    if not ok:
        raise HTTPException(status_code=503, detail=body)
    return body
```

**Change 6:** Registered `RequestIdMiddleware` as outermost middleware

```python
# After all @app.middleware("http") registrations:
app.add_middleware(RequestIdMiddleware)
```

The `add_middleware` call makes `RequestIdMiddleware` the true outermost layer — it assigns a correlation ID to every request, including those rejected by auth or rate limiting.

---

## Middleware Execution Order (Post-fix)

```
Inbound request →
  RequestIdMiddleware        # assigns X-Request-ID (always)
    api_key_auth_middleware  # rate-limit + auth gate (skips _UNPROTECTED_PATHS)
      log_requests           # records method/path/status/duration
        CORSMiddleware       # adds CORS response headers
          Route handler
```

---

## Test Results

### New Regression Tests: `tests/test_sprint2111_regression.py`

27 tests, 7 sections:

| Section | Tests | Description |
|---------|-------|-------------|
| `TestUnprotectedPaths` | 5 | `_UNPROTECTED_PATHS` contains correct set |
| `TestRouteRegistration` | 5 | `app/main.py` has all required routes |
| `TestMiddlewareRegistration` | 2 | `RequestIdMiddleware` registered |
| `TestConstantTimeKeyCheck` | 6 | Auth middleware allows/rejects correctly |
| `TestMetricsEndpoint` | 3 | `/metrics` returns 200 text/plain, never 404 |
| `TestHealthLiveRoute` | 3 | `/health/live` returns `{"alive": true, ...}` |
| `TestRequestIdMiddlewareUnit` | 3 | `RequestIdMiddleware` generates/propagates IDs |

### Full Suite Results

```
1846 passed, 0 failures, 4 skipped
```

Previous: 1820 passed. Delta: +26 (27 new tests, 1 already existed from Sprint 2.11).

---

## Production Readiness: Evidence

| Requirement | Evidence |
|-------------|---------|
| `GET /metrics` returns 200 | `test_metrics_handler_produces_text_not_404` (PASS) |
| `GET /metrics` no auth required | `test_api_key_auth_middleware_allows_metrics` (PASS) + `test_metrics_in_unprotected_paths` (PASS) |
| `GET /health/live` returns 200 | `test_health_live_returns_alive_true` (PASS) |
| `GET /health/live` no auth required | `test_api_key_auth_middleware_allows_health_live` (PASS) + `test_health_live_in_unprotected_paths` (PASS) |
| `GET /health/ready` route exists | `test_health_ready_route_exists` (PASS) |
| `GET /health/ready` no auth required | `test_api_key_auth_middleware_allows_health_ready` (PASS) + `test_health_ready_in_unprotected_paths` (PASS) |
| `X-Request-ID` on all requests | `test_request_id_middleware_registered` (PASS) |
| No regression | Full suite: 0 failures |

---

## Deliverables

| Document | Status |
|----------|--------|
| `SPRINT_2_11_1_CTO_REPORT.md` | This document |
| `ROUTE_AUDIT.md` | Complete route inventory for both apps |
| `MIDDLEWARE_AUDIT.md` | Middleware chain analysis with ordering proof |
| `ENVIRONMENT_AUDIT.md` | `.env` vs `.env.example` drift + required vars |
| `GRAFANA_SETUP_VALIDATED.md` | Step-by-step Grafana setup with validated PromQL |
| `tests/test_sprint2111_regression.py` | 27 regression tests, all passing |

---

## Open Items

| Priority | Item | Owner |
|----------|------|-------|
| 🔴 CRITICAL | Rotate Supabase service-role key (committed to git history via deleted file) | DevOps |
| 🟠 HIGH | Set `PROMETHEUS_ENABLED=true` in production `.env` | Backend |
| 🟠 HIGH | Apply Prometheus alert rules to `rules.yml` | SRE |
| 🟡 MEDIUM | Add 6 undocumented vars to `.env.example` (`ADAPTIVE_*`, `FAST_PATH_*`, `B1_HNSW_V2_ENABLED`) | Backend |
| 🟡 MEDIUM | Add 3 missing vars to `.env` (`EMBEDDING_DIMENSIONS`, `OPENAI_TIMEOUT_S`, `RETRIEVAL_FTS_TIMEOUT_S`) | Backend |
| 🟢 LOW | Set up Grafana dashboards (manual step) | SRE |
| 🟢 LOW | Wire structured JSON logging (`STRUCTURED_LOGGING_ENABLED=true`) | Backend |
