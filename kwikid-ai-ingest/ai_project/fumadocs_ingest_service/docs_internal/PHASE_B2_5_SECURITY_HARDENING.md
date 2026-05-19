# Phase B2.5 — Pre-production Security Hardening

**Date**: 2026-05-18  
**Status**: COMPLETE — all 20 security checks pass  
**Engineer**: Lead AI Systems Architect

---

## 1. Summary

Phase B2.5 adds the minimum security controls required before the service can accept
connections from outside a trusted internal network. It does NOT change any retrieval,
generation, or confidence-scoring logic — the B1/B2 pipelines are preserved exactly.

**Threat model**: The service is a FastAPI REST API deployed on a single-process server
(Uvicorn). The primary risks are:
- Unauthorized callers reading or triggering ingestion/chat without credentials
- Cost explosion from API abuse (unbounded OpenAI calls)
- Internal error details leaking to clients (stack traces, secret fragments)
- Misconfigured deployment starting silently with no auth

---

## 2. Files Changed

| File | Change |
|---|---|
| `app/security.py` | NEW — auth middleware, rate limiter, audit logger, startup validation |
| `app/main.py` | MODIFIED — lifespan, security middleware wired, docs disabled, exception handlers |
| `.env.example` | MODIFIED — added RAG_API_KEY, RAG_API_KEYS, RAG_CHAT_RATE_LIMIT, FASTAPI_DOCS_ENABLED |
| `scripts/validate_b2_5_security.py` | NEW — 20-check validation suite (13 offline + 7 HTTP) |

---

## 3. Security Controls Implemented

### 3.1 API Key Authentication

All routes require `X-API-Key` header except:
- `GET /health` — public (liveness probe, no business logic)
- `GET /ready` — public (readiness probe, no business logic)
- `POST /freshdesk/webhook` — protected by HMAC-SHA256 instead

**Key matching**: Uses `hmac.compare_digest` in a bitwise-OR loop across all configured
keys. No short-circuit evaluation — timing is O(n_keys) regardless of which key matched:

```python
result = 0
for key in keys:
    result |= int(hmac.compare_digest(provided_bytes, key.encode("utf-8")))
return bool(result)
```

**Fail closed**: An empty key set always returns False. The lifespan refuses to start
the service if no keys are configured.

**Key loading**: Keys are loaded once at startup (`app/security.py::initialize()`) and
cached as a module-level `frozenset`. Supports:
- `RAG_API_KEY` — single key
- `RAG_API_KEYS` — comma-separated list (for zero-downtime key rotation)

**Key logging**: Never. Only the first 8 characters + `...` are included in audit logs.

### 3.2 Rate Limiting

Per-IP sliding-window rate limiting using `collections.deque` + `threading.Lock`.
No external dependency (no Redis, no slowapi).

| Route | Limit | Configurable |
|---|---|---|
| `POST /rag/chat` | `RAG_CHAT_RATE_LIMIT` req/60s (default: 20) | yes |
| `POST /freshdesk/webhook` | 60 req/60s | no |
| All other routes | 30 req/60s | no |

Rate limiting applies to ALL paths including `/health` and `/ready` to prevent
DoS on health-check endpoints. Rate-limited requests receive:
```
HTTP 429  Retry-After: 60
{"error": "rate_limit_exceeded", "message": "Too many requests. Please retry later."}
```

**Implementation**: `RateLimiter.is_allowed(ip)` evicts expired timestamps on each call,
so memory is bounded to `max_requests` timestamps per active IP.

### 3.3 Webhook HMAC (pre-existing, preserved)

`POST /freshdesk/webhook` was already protected by HMAC-SHA256 in `app/freshdesk_webhook.py`
(`verify_webhook_token` uses `hmac.compare_digest`). B2.5 exempts this path from API key
auth (the two mechanisms serve different callers: the webhook is called by Freshdesk, which
cannot provide an API key, but can be configured to include an HMAC token).

### 3.4 Startup Environment Validation

The FastAPI `lifespan` context manager validates security config before the app accepts
any requests:

```python
@asynccontextmanager
async def lifespan(_app):
    api_keys = load_api_keys()
    errors = validate_startup_security(api_keys, os.getenv("OPENAI_API_KEY", ""))
    if errors:
        raise RuntimeError(f"Security configuration error: {errors[0]}")
    security_initialize(api_keys)
    yield
```

Fatal conditions (service refuses to start):
- `RAG_API_KEY` / `RAG_API_KEYS` both empty or unset
- `OPENAI_API_KEY` not set (generation pipeline unusable)

### 3.5 Audit Logging

All authentication events are written to the `audit` Python logger (separate from the
main application logger). Log events:

| Event | Level | Fields |
|---|---|---|
| `auth_ok` | DEBUG | ip, path, key_prefix (first 8 chars) |
| `auth_missing_key` | WARNING | ip, path |
| `auth_invalid_key` | WARNING | ip, path, key_prefix |
| `rate_limit_exceeded` | WARNING | ip, path |

To route audit events to a separate file or log aggregator:
```python
# In logging config:
logging.getLogger("audit").addHandler(audit_file_handler)
```

### 3.6 Safe Exception Handling

Two `@app.exception_handler` registrations prevent internal details from reaching clients:

**Unhandled exceptions** → HTTP 500 with generic message (no stack trace):
```json
{"error": "internal_server_error", "message": "An unexpected error occurred."}
```

**Pydantic validation errors** → HTTP 422 with structured field errors (no internal paths):
```json
{"error": "validation_error", "message": "Request validation failed.", "details": [...]}
```

### 3.7 FastAPI Docs Disabled in Production

By default (`FASTAPI_DOCS_ENABLED=false`), the service hides:
- `GET /docs` (Swagger UI)
- `GET /redoc` (ReDoc)
- `GET /openapi.json` (OpenAPI specification)

All three return 404. This prevents route enumeration by unauthenticated callers.

Set `FASTAPI_DOCS_ENABLED=true` only in development environments.

---

## 4. New Environment Variables

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `RAG_API_KEY` | _(none)_ | **YES** | Primary API key (single) |
| `RAG_API_KEYS` | _(none)_ | if no RAG_API_KEY | Comma-separated list for rotation |
| `RAG_CHAT_RATE_LIMIT` | `20` | no | Max /rag/chat requests per 60s per IP |
| `FASTAPI_DOCS_ENABLED` | `false` | no | Enable /docs, /redoc, /openapi.json |

**Key generation** (run once, store securely):
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 5. Middleware Execution Order

Starlette processes `@app.middleware("http")` decorators in LIFO order (last registered = outermost = first on inbound requests). In `app/main.py`:

```
Inbound request
  └─► CORSMiddleware            (app.add_middleware — outermost)
        └─► api_key_auth_middleware  (registered after log_requests → outermost http middleware)
              └─► log_requests        (registered first → inner http middleware)
                    └─► Route handler
```

This means:
1. CORS headers are set on all responses (even 401/429)
2. Rate limiting and auth check happen before request logging starts the timer
3. Auth-rejected requests are logged by `log_requests` (which wraps the entire stack including the middleware return)

---

## 6. Validation Suite

```bash
# Run all 20 checks (offline + HTTP)
python scripts/validate_b2_5_security.py

# Write markdown report
python scripts/validate_b2_5_security.py --report
```

| Check | Mode | What it validates |
|---|---|---|
| S1 | offline | `_constant_time_key_check`: correct key → True |
| S2 | offline | `_constant_time_key_check`: second key in set → True |
| S3 | offline | `_constant_time_key_check`: wrong key → False |
| S4 | offline | `_constant_time_key_check`: empty key set → False (fail closed) |
| S5 | offline | `load_api_keys`: reads RAG_API_KEY |
| S6 | offline | `load_api_keys`: reads RAG_API_KEYS CSV, trims whitespace |
| S7 | offline | `load_api_keys`: both vars merged and deduplicated |
| S8 | offline | `load_api_keys`: empty env → empty frozenset |
| S9 | offline | `validate_startup_security`: no API keys → error |
| S10 | offline | `validate_startup_security`: no OpenAI key → error |
| S11 | offline | `validate_startup_security`: both set → no errors |
| S12 | offline | `RateLimiter`: allows requests up to max |
| S13 | offline | `RateLimiter`: rejects request exceeding limit |
| S14 | HTTP | GET /health → 200 (no API key required) |
| S15 | HTTP | POST /rag/chat without X-API-Key → 401 |
| S16 | HTTP | POST /rag/chat with wrong key → 401 |
| S17 | HTTP | GET /freshdesk/filter-options with correct key → not 401 |
| S18 | HTTP | GET /chat/suggestions with correct key → not 401 |
| S19 | HTTP | GET /docs → 404 (docs disabled) |
| S20 | HTTP | GET /openapi.json → 404 (spec hidden) |

---

## 7. Protected Route Inventory

| Route | Method | Auth Required | Rate Limit |
|---|---|---|---|
| `/health` | GET | No | 30/60s |
| `/ready` | GET | No | 30/60s |
| `/freshdesk/webhook` | POST | No (HMAC) | 60/60s |
| `/ingest` | POST | **Yes** | 30/60s |
| `/query` | POST | **Yes** | 30/60s |
| `/chat` | POST | **Yes** | 30/60s |
| `/chat/suggestions` | GET | **Yes** | 30/60s |
| `/train/chat` | POST | **Yes** | 30/60s |
| `/train/commit` | POST | **Yes** | 30/60s |
| `/train/cards` | GET | **Yes** | 30/60s |
| `/train/cards/{id}` | GET | **Yes** | 30/60s |
| `/train/cards/{id}` | DELETE | **Yes** | 30/60s |
| `/rag/chat` | POST | **Yes** | 20/60s (configurable) |
| `/freshdesk/filter-options` | GET | **Yes** | 30/60s |

---

## 8. Remaining Production Blockers

| Blocker | Impact | Status |
|---|---|---|
| **TLS/HTTPS** | Traffic in transit is unencrypted without TLS termination | Must be handled by nginx/load-balancer or uvicorn `--ssl-*` flags |
| **Supabase RLS** | Row-Level Security must be verified per-tenant | Pre-existing from B1 — not changed |
| **Secret rotation procedure** | No documented key rotation runbook | Supported via RAG_API_KEYS (comma list) — add new key, remove old key, restart |
| **Webhook HMAC enforcement** | FRESHDESK_WEBHOOK_SECRET is optional | Should be made required before enabling FRESHDESK_WEBHOOK_ENABLED=true |

---

## 9. Manual Verification Commands

After deploying with a real `RAG_API_KEY`:

```bash
# Should return 200
curl http://localhost:8000/health

# Should return 401 (no key)
curl -X POST http://localhost:8000/rag/chat \
  -H "Content-Type: application/json" \
  -d '{"query_text":"test","client":"unity_bank"}'

# Should return 401 (wrong key)
curl -X POST http://localhost:8000/rag/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wrong-key" \
  -d '{"query_text":"test","client":"unity_bank"}'

# Should return 200 or 502 (key accepted, but DB/LLM may fail)
curl -X POST http://localhost:8000/rag/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_REAL_KEY_HERE" \
  -d '{"query_text":"test","client":"unity_bank"}'

# Should return 404 (docs hidden)
curl http://localhost:8000/docs
curl http://localhost:8000/openapi.json
```

---

## 10. B3 Entry Conditions (unchanged from B2)

Before enabling `FRESHDESK_WEBHOOK_ENABLED=true`:
1. `RAG_API_KEY` must be set (now enforced at startup)
2. `FRESHDESK_WEBHOOK_SECRET` must be set (make it required, not optional)
3. At least 10 real webhook payloads tested in shadow mode
4. Confidence threshold calibrated against agent feedback
