# Security Post-Consolidation Review — Sprint 2.11.2 Phase 9

**Date:** 2026-06-04
**Scope:** Full security review after app/main.py consolidation

---

## 1. Authentication Architecture

### RAG Service (X-API-Key)

All RAG routes except `/health*`, `/ready`, `/metrics`, `/docs`, `/openapi.json`, `/redoc`, and `/freshdesk/webhook` require `X-API-Key`.

**Implementation:**
- `app/security.py::api_key_auth_middleware` — HTTP middleware
- Keys loaded from `RAG_API_KEY` (single) and/or `RAG_API_KEYS` (CSV)
- Constant-time comparison via `hmac.compare_digest()` across all keys (bitwise-OR loop — no short-circuit timing oracle)
- Empty key set → always reject (fail closed)
- Keys never logged in full — only 8-char prefix

### Action Gateway (RBAC)

Gateway routes bypass the global middleware and use per-route `Depends()`:

```python
# security/dependencies.py
async def require_admin(request: Request) -> AuthContext:     # ADMIN role
async def require_approver(request: Request) -> AuthContext:  # APPROVER role
async def require_operator(request: Request) -> AuthContext:  # OPERATOR role
```

Keys sourced from `ADMIN_API_KEYS` env var (separate from `RAG_API_KEY`). All comparisons use `hmac.compare_digest()`.

**Role hierarchy:**
- `ADMIN` — full access including audit query and dead-letter
- `OPERATOR` — worker tick and watchdog
- `APPROVER` — approve/reject actions
- `WATCHDOG` — watchdog run only

### Freshdesk Webhook (HMAC-SHA256)

`POST /freshdesk/webhook` (RAG) and `POST /webhook/{client}` (gateway) are exempt from API key auth but protected by HMAC-SHA256:

- Raw request body is signed (not re-serialized JSON — byte-exact)
- `hmac.compare_digest()` for constant-time comparison
- `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` → missing/invalid signature returns 403
- Secret never logged in full

---

## 2. Middleware Security Analysis

### Middleware execution order (outermost → innermost)

```
RequestIdMiddleware
  → api_key_auth_middleware (rate limit + auth)
      → _log_requests_dispatch (request logging)
          → FastAPI routing (HTTPException handlers, route handlers)
```

### Rate Limiting

Per-IP sliding-window rate limiter using `collections.deque` + `threading.Lock`:

| Path | Limit |
|------|-------|
| `/rag/chat` | `RAG_CHAT_RATE_LIMIT` (default 20/60s) |
| `/freshdesk/webhook` | 60/60s |
| All other non-gateway | 30/60s |
| Gateway paths | Bypassed |

**Security property:** Rate limiting applies to health endpoints (`/health`, `/metrics`) to prevent DoS on health probes. Gateway paths bypass because they have their own per-route auth which limits abuse surface.

### Gateway Prefix Bypass

```python
_GATEWAY_PREFIXES: tuple[str, ...] = (
    "/actions", "/worker", "/watchdog", "/audit",
    "/admin", "/webhook", "/gateway",
)
```

Paths starting with these prefixes skip global rate limit AND API key auth. Security maintained by per-route `Depends()`. The `/gateway` prefix covers `/gateway/health*` and `/gateway/metrics` — these are intentionally public (no auth) like standard health/metrics endpoints.

---

## 3. Secret Handling Audit

| Secret | Logged? | Stored? | Rotation |
|--------|---------|---------|---------|
| `RAG_API_KEY` | 8-char prefix only | `frozenset` in memory (never disk) | Replace env var + restart |
| `ADMIN_API_KEYS` | Never | HMAC-digested at construction | Add new key + remove old |
| `OPENAI_API_KEY` | Never | Passed to httpx client | Replace env var + restart |
| `SUPABASE_KEY` | Never | Passed to supabase client | ⚠️ COMPROMISED — rotate now |
| `FRESHDESK_WEBHOOK_SECRET` | Never | Passed to hmac.new() | Replace env var + restart |
| `FRESHDESK_API_KEY` | Never | Passed to requests.auth | Replace env var + restart |

### ⚠️ CRITICAL: Supabase Key Compromise

A service-role Supabase key was accidentally committed to git history via `supabasesuccess.py` (file since deleted). The key is permanently compromised in git history.

**Required action before any production deployment:**
1. Log in to Supabase dashboard → Project Settings → API
2. Click "Regenerate" on the service-role key
3. Update `SUPABASE_KEY` in all deployments
4. Verify no cached copies in CI/CD environment secrets

---

## 4. Input Validation

| Boundary | Validation |
|----------|-----------|
| Pydantic models | `ChatRequest`, `QueryRequest`, `RagChatRequest`, etc. — field constraints enforced |
| `RequestValidationError` | Returns 422 with structured error (no stack traces) |
| Webhook HMAC | Byte-exact comparison on raw body |
| Aadhaar masking | `mask_aadhaar()` applied at Freshdesk webhook ingress |
| CORS origins | Must start with `http://` or `https://` — validated at startup |
| Source thresholds | Range validated [0.0, 1.0] in Pydantic validators |

---

## 5. Error Response Security

All error responses use structured format without stack traces:

- Gateway errors: `{"error": {"code": "...", "message": "..."}}`
- RAG errors: `{"detail": ...}` (FastAPI standard)
- 500 errors: `{"error": "internal_server_error", "message": "An unexpected error occurred."}`

**Verified:** No Python tracebacks in any HTTP response body (`"Traceback"` and `"File "` string checks in `test_sprint27_api.py::TestSecurityEdgeCases`).

---

## 6. PII Protection

- `DEBUG_RAG=false` (default) — suppresses chunk content in logs
- Aadhaar numbers masked at Freshdesk webhook ingress before any storage or logging
- Structured logger `_should_redact()` redacts values for keys matching `password`, `secret`, `key`, `token`, `auth`
- OpenAI API keys never appear in log output

---

## 7. Security Test Coverage

| Test file | Security aspects covered |
|-----------|-------------------------|
| `test_sprint27_api.py::TestSecurityEdgeCases` | compare_digest usage, no stack traces, no secret in responses |
| `test_sprint27_api.py::TestWebhookEndpointHMAC` | HMAC success/fail, wrong secret, missing sig, raw bytes signing |
| `test_sprint28_security.py` | RBAC roles, key separation, gateway auth |
| `test_sprint2111_regression.py::TestConstantTimeKeyCheck` | Timing-safe auth, path bypass prevention |
| `test_sprint2111_regression.py::TestUnprotectedPaths` | Correct exempt path set |
