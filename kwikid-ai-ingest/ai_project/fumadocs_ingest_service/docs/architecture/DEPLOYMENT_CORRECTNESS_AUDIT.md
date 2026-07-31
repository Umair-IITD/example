# Deployment Correctness Audit — Sprint 2.11.2 Phase 7

**Date:** 2026-06-04
**Auditor:** Sprint 2.11.2 automated review

---

## Summary

| Category | Pre-Sprint 2.11.2 | Post-Sprint 2.11.2 |
|----------|-------------------|---------------------|
| Production entry point | `app/main.py` (RAG only) | `app/main.py` (RAG + Gateway) |
| Gateway routes reachable | ❌ None | ✅ All 12 |
| Metrics initialization | ❌ Import-time frozen | ✅ Lazy, load_dotenv()-safe |
| Test suite alignment | ❌ Tests used test-only factory | ✅ Tests use production factory |
| Rate limit (gateway) | ❌ 429 false positives | ✅ Bypassed for gateway paths |
| HTTPException format | ❌ Wrong format for gateway | ✅ Path-aware handler |

---

## 1. Entry Point Audit

### Production uvicorn command

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

`app` is assigned at module level: `app = create_app()`. The `create_app()` factory includes both RAG routes and all 8 gateway routers (6 direct + 2 under `/gateway` prefix).

### Verification

```bash
# After starting the service:
curl http://localhost:8000/health/live           # RAG liveness
curl http://localhost:8000/gateway/health        # Gateway health
curl http://localhost:8000/gateway/metrics       # Gateway action metrics
curl http://localhost:8000/metrics               # RAG Prometheus metrics
```

---

## 2. Environment Variable Audit

### Required for startup

| Variable | Purpose | Startup behavior if missing |
|----------|---------|---------------------------|
| `RAG_API_KEY` | API authentication | RuntimeError — service refuses to start |
| `OPENAI_API_KEY` | Chat + embeddings | RuntimeError — service refuses to start |
| `SUPABASE_URL` | Vector database | Singleton warmup fails (cold start) |
| `SUPABASE_KEY` | Supabase auth | Singleton warmup fails (cold start) |

### Security-critical

| Variable | Production value | Risk if wrong |
|----------|-----------------|--------------|
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` | `true` | Webhook accepts unsigned requests |
| `FASTAPI_DOCS_ENABLED` | `false` | API schema exposed publicly |
| `DEBUG_RAG` | `false` | PII exposure in chunk previews |
| `AUDIT_BACKEND` | `supabase` | Audit events lost on restart |

### Metrics

| Variable | Value | Effect |
|----------|-------|--------|
| `PROMETHEUS_ENABLED` | `true` | Enables all 7 prometheus_client instruments |
| `PROMETHEUS_ENABLED` | `false` (default) | Returns placeholder text at `/metrics` |

---

## 3. Middleware Stack Audit

Starlette applies middleware in LIFO order. Registration order and effective execution order:

| Registration order | Middleware | Effective order (inbound) |
|-------------------|-----------|--------------------------|
| 1st (`add_middleware`) | CORSMiddleware | 4th (innermost) |
| 2nd (`@middleware`) | `_log_requests_dispatch` | 3rd |
| 3rd (`@middleware`) | `api_key_auth_middleware` | 2nd |
| 4th (`add_middleware`) | RequestIdMiddleware | 1st (outermost) |

**Note:** `add_middleware` is prepended, `@middleware` is appended. The effective order matches the intended outermost-to-innermost flow above.

---

## 4. Route Conflict Audit

### Potential conflicts (resolved)

The gateway's `api/routes/health.py` and `api/routes/metrics.py` register `/health`, `/health/live`, `/health/ready`, and `/metrics` — identical paths to RAG routes.

**Resolution:** Gateway health/metrics routers are included under `/gateway` prefix:
- `GET /gateway/health` — gateway runtime health
- `GET /gateway/health/live` — gateway liveness
- `GET /gateway/health/ready` — gateway readiness (supabase + audit + config)
- `GET /gateway/metrics` — action lifecycle metrics

RAG routes retain their original paths (`/health`, `/metrics`, etc.).

---

## 5. Security Audit

### API Key Authentication

- Global `api_key_auth_middleware` requires `X-API-Key` header for all non-exempt paths
- Exempt paths (`_UNPROTECTED_PATHS`): `/health`, `/health/live`, `/health/ready`, `/metrics`, `/ready`, `/docs`, `/openapi.json`, `/redoc`, `/freshdesk/webhook`
- Gateway paths bypass global middleware entirely (per-route `Depends()` handles their auth)
- Constant-time key comparison via `hmac.compare_digest()` — no timing oracle

### Key Integrity

- `CRITICAL`: Supabase service-role key was accidentally committed to git history via `supabasesuccess.py` (since deleted). The key remains compromised in git history. **Must rotate before production.**
- See `DEPLOYMENT_READINESS_REVIEW.md` for rotation steps

---

## 6. Singleton Lifecycle Audit

| Singleton | Init | Thread safety | Shutdown |
|-----------|------|--------------|---------|
| Supabase client | Lifespan startup | `httpcore.ConnectionPool` (safe) | Not closed (httpx manages) |
| OpenAI embedder | Lifespan startup | `_EMBEDDER_CALL_LOCK` (serialized) | `embedder.close()` in lifespan teardown |
| ChatGenerator | Lifespan startup | `_GENERATOR_LOCK` (DCL) | `generator._llm.close()` in lifespan teardown |
| CaseService | Lifespan startup | `_CASE_SERVICE_LOCK` (DCL) | No close needed |
| LRU embed cache | Module-level | `_EMBED_CACHE_LOCK` | No close needed |

---

## 7. Open Items

| Priority | Item |
|----------|------|
| 🔴 CRITICAL | Rotate Supabase service-role key (compromised in git history) |
| 🟠 HIGH | Set `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true` in production |
| 🟠 HIGH | Set `AUTH_ENABLED=true` and configure `ADMIN_API_KEYS` |
| 🟡 MEDIUM | Enable structured JSON logging at startup |
| 🟡 MEDIUM | Set `AUDIT_BACKEND=supabase` in production |
| 🟢 LOW | Configure Grafana dashboards from `MONITORING_DEPLOYMENT_GUIDE.md` |
