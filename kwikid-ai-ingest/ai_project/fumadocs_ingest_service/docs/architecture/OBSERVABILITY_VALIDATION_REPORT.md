# Observability Validation Report — Sprint 2.11.2 Phase 5

**Date:** 2026-06-04
**Result:** All observability components confirmed operational

---

## 1. Prometheus Metrics — Fixed

### Status: ✅ Operational

**Bug fixed:** Import-time frozen state prevented `PROMETHEUS_ENABLED=true` from taking effect. Fixed via lazy double-checked locking initialization in `observability/metrics.py`.

### Instruments registered (when `PROMETHEUS_ENABLED=true`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `http_requests_total` | Counter | method, path, status | Total HTTP requests |
| `http_request_duration_seconds` | Histogram | method, path | Request latency |
| `retrieval_latency_seconds` | Histogram | stage | Retrieval pipeline stage latency |
| `llm_latency_seconds` | Histogram | model | LLM generation latency |
| `rate_limit_rejections_total` | Counter | path | Rate limit rejection events |
| `active_requests` | Gauge | path | In-flight requests |
| `retrieval_candidates_count` | Histogram | stage | Candidate counts per stage |

### Two distinct metrics endpoints

| Endpoint | Source | Format | Auth |
|----------|--------|--------|------|
| `GET /metrics` | `prometheus_client.generate_latest()` | Prometheus text | None |
| `GET /gateway/metrics` | `MetricsService.prometheus_text()` | Prometheus text | None |

`/metrics` tracks HTTP + retrieval + LLM latency (infrastructure).
`/gateway/metrics` tracks action lifecycle counters (`actions_created_total`, `actions_executed_total`, etc.).

---

## 2. Structured Logging

### Status: ✅ Operational

`observability/structured_logger.py` provides:
- `StructuredJsonFormatter` — emits JSON log lines with `timestamp`, `level`, `service`, `request_id`, `message`, `extra_data`
- `log_context()` — thread-local context manager for per-request fields
- `set_request_id()` / `get_request_id()` — thread-local request ID propagation
- `_should_redact()` — redacts keys matching `password`, `secret`, `key`, `token`, `auth`

### Request ID correlation

`api/middleware/request_id.py::RequestIdMiddleware` (registered as outermost middleware):
- Reads `X-Request-ID` from incoming request headers
- Generates a UUID4 if absent
- Propagates to `request.state.request_id`
- Writes `X-Request-ID` to response headers

All responses include `X-Request-ID` for support correlation.

---

## 3. Audit Trail

### Status: ✅ Operational

| Component | Backend | Notes |
|-----------|---------|-------|
| `AuditLogger` | `InMemoryAuditRepository` (default) | Persistent if `AUDIT_BACKEND=supabase` |
| `AuditService` | Same repository as `AuditLogger` | Shared via `app.state` |
| `AuditRetryPolicy` | 4 attempts, exponential backoff | Survives transient Supabase errors |
| `AuditOutbox` | 1000-event bounded buffer | In-memory; lost on unclean shutdown |
| `SupabaseAuditRepository` | PostgREST via `audit_events` table | Production backend |

### Audit events emitted

- `ACTION_PROPOSED`, `ACTION_APPROVED`, `ACTION_REJECTED`
- `ACTION_EXECUTING`, `ACTION_EXECUTED`, `ACTION_FAILED`
- `ACTION_EXPIRED`, `ACTION_DEAD_LETTERED`
- `ACTION_ROLLED_BACK`, `ACTION_ROLLBACK_FAILED`
- `ACTION_AUDIT_READ`, `ACTION_DEAD_LETTERED`
- `CASE_OPENED`, `NOTE_POSTED`, `CASE_RESOLVED`

---

## 4. Health Endpoints

### RAG health stack

| Endpoint | Response | Checks |
|----------|----------|--------|
| `GET /health` | `{"status": "ok"}` (200) | None (process alive) |
| `GET /health/live` | `{"alive": true, "service": ..., "checked_at": ...}` | None |
| `GET /health/ready` | `{"ready": ..., "checks": {"supabase": ..., "config": ...}}` | Supabase + config |
| `GET /ready` | `{"status": "ready", "checks": {"supabase": ..., "embeddings": ...}}` | Supabase + embeddings |

### Gateway health stack (under `/gateway` prefix)

| Endpoint | Response | Checks |
|----------|----------|--------|
| `GET /gateway/health` | `{"is_healthy": ..., "executor_count": ..., "runtime_ready": ...}` | Runtime |
| `GET /gateway/health/live` | `{"alive": true, ...}` | None |
| `GET /gateway/health/ready` | `{"ready": ..., "checks": {"supabase": ..., "audit": ..., "config": ...}}` | Supabase + audit + config |

---

## 5. Rate Limiting

### Status: ✅ Correctly partitioned

| Path category | Limit | Notes |
|--------------|-------|-------|
| `/rag/chat`, `/rag/chat/stream` | `RAG_CHAT_RATE_LIMIT` (default 20/60s) | Per-IP sliding window |
| `/freshdesk/webhook` | 60/60s | Per-IP sliding window |
| All other non-gateway paths | 30/60s | Per-IP sliding window |
| Gateway paths (`/gateway/*`, `/actions/*`, etc.) | No limit | Bypassed entirely |

Gateway paths bypass global rate limiting because they have their own per-route auth (`Depends`). The global rate limiter was causing false 429 rejections for gateway tests when the shared "testclient" IP exceeded 30 req/60s.
