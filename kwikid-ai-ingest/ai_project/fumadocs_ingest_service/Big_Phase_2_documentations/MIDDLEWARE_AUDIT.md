# Middleware Audit — KwikID AI Ingest Service

**Date:** 2026-06-04
**Sprint:** 2.11.1
**Auditor:** Principal Staff Engineer / SRE Lead

---

## Production App Middleware Stack (`app/main.py`)

### Registration Order and Execution Order

Starlette's middleware stack uses LIFO ordering: the **last middleware registered is outermost** (first to process inbound requests, last to process outbound responses).

#### Registration sequence in `app/main.py` (post-fix)

```
1. app.add_middleware(CORSMiddleware, ...)         → registered first
2. @app.middleware("http") log_requests            → second
3. app.middleware("http")(api_key_auth_middleware) → third
4. app.add_middleware(RequestIdMiddleware)          → last (NEW — Sprint 2.11.1)
```

#### Execution order on inbound request

```
RequestIdMiddleware (outermost)     ← assigns/propagates X-Request-ID
  └─ api_key_auth_middleware        ← rate limit then auth gate
       └─ log_requests              ← records method/path/status/duration
            └─ CORSMiddleware       ← adds CORS response headers
                 └─ Route handler
```

#### Why this ordering is correct

- `RequestIdMiddleware` must be outermost so that every log record — including auth rejections — carries a correlation ID
- `api_key_auth_middleware` is outermost among the `@app.middleware("http")` registrations (LIFO: registered last = outermost among that group); it short-circuits to 401/429 before reaching log_requests for rejected requests
- `CORSMiddleware` is innermost; it only needs to add response headers to requests that pass auth

---

## Middleware Details

### 1. RequestIdMiddleware (`api/middleware/request_id.py`)

**Sprint 2.11 B5 — NEW to `app/main.py` in Sprint 2.11.1**

| Property | Value |
|----------|-------|
| Type | `starlette.middleware.base.BaseHTTPMiddleware` |
| Registered via | `app.add_middleware(RequestIdMiddleware)` |
| Execution position | Outermost |

**Behaviour:**
1. Reads `X-Request-ID` header from inbound request
2. If absent/empty: generates UUID4
3. Calls `set_request_id()` — injects into structured logging `ContextVar`
4. Sets `request.state.request_id` for route handlers
5. Echoes `X-Request-ID` in response header

**Pre-fix state:** Was NOT registered in `app/main.py`. Present only in `api/app.py`. No correlation IDs on any production request.

---

### 2. `api_key_auth_middleware` (`app/security.py`)

| Property | Value |
|----------|-------|
| Type | `@app.middleware("http")` coroutine function |
| Registered via | `app.middleware("http")(api_key_auth_middleware)` |
| Execution position | Second (after RequestIdMiddleware) |

**Behaviour:**
1. Extracts client IP (respects X-Forwarded-For)
2. Rate limiting: sliding-window deque per IP
   - `/rag/chat`: `RAG_CHAT_RATE_LIMIT` req/60s (default 20)
   - `/freshdesk/webhook`: 60 req/60s
   - all others: 30 req/60s
3. Auth exemption check: skips auth if `path in _UNPROTECTED_PATHS`
4. API key check: reads `X-API-Key` header, constant-time comparison via `hmac.compare_digest()`
5. Returns 401 if key missing or invalid

**Exempt paths (post-fix):**
```
/health, /health/live, /health/ready, /metrics, /ready,
/docs, /openapi.json, /redoc, /freshdesk/webhook
```

**Pre-fix missing exemptions:** `/metrics`, `/health/live`, `/health/ready` — causing 401 on Prometheus scraping and 404/401 on Kubernetes health probes.

---

### 3. `log_requests` (`app/main.py`)

| Property | Value |
|----------|-------|
| Type | `@app.middleware("http")` coroutine function |
| Registered via | `@app.middleware("http")` decorator |
| Execution position | Third (after auth gate) |

**Behaviour:**
1. Records request start time
2. Calls `call_next(request)` — passes to inner middleware/handler
3. On success: calls `record_request()` for Prometheus HTTP counter + duration
4. On exception: records 500 status, re-raises
5. Logs structured dict with method/path/status_code/duration_s/client_ip

---

### 4. CORSMiddleware (`fastapi.middleware.cors`)

| Property | Value |
|----------|-------|
| Type | `starlette.middleware.cors.CORSMiddleware` |
| Registered via | `app.add_middleware(CORSMiddleware, ...)` |
| Execution position | Innermost |

**Configuration:**
```python
allow_origins=CORS_ALLOWED_ORIGINS or ["http://localhost:3000", "http://127.0.0.1:3000"]
allow_credentials=True
allow_methods=["GET", "POST", "DELETE", "OPTIONS"]
allow_headers=["Content-Type", "X-API-Key", "X-Webhook-Token", "Authorization"]
```

**Security note:** `CORS_ALLOWED_ORIGINS` is validated at startup to reject non-HTTP origins (data:, javascript:, etc.).

---

## Action Gateway App Middleware Stack (`api/app.py`)

This is a separate application not served in production.

| Middleware | Type | Notes |
|-----------|------|-------|
| RequestIdMiddleware | `add_middleware` | Outermost |
| Per-route `Depends()` | FastAPI dependencies | Not global middleware |

No global auth middleware in `api/app.py` — authentication is per-route via `require_admin`, `require_approver`, `require_operator` dependencies.

---

## Exception Handlers

Both apps register exception handlers (not middleware, but part of the ASGI pipeline):

| Handler | Scope | Notes |
|---------|-------|-------|
| `unhandled_exception_handler` | All uncaught exceptions | Returns 500, logs exception with traceback |
| `validation_exception_handler` | `RequestValidationError` | Returns 422 with error details |

---

## Starlette Middleware Order Notes

The Starlette documentation on middleware order is subtle:

- `app.add_middleware(X)` inserts X as the new outermost wrapper. Multiple `add_middleware` calls: later calls = more outermost.
- `@app.middleware("http")` adds to the ASGI stack. Among http middlewares, last registered = outermost.
- `add_middleware`-registered middleware wraps the entire ASGI app including all `@app.middleware("http")` entries.

The post-fix stack is correct: `RequestIdMiddleware` (via `add_middleware`, last registered) wraps everything.
