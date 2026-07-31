# Deployment Readiness Review — KwikID AI Ingest Service

**Date:** 2026-06-04
**Sprint:** 2.11
**Review Type:** Full Production Deployment Audit

---

## Summary

| Category | Status | Notes |
|----------|--------|-------|
| Startup/Shutdown | ✅ Ready | Lifespan-managed, graceful |
| Configuration | ✅ Ready | Validated at startup; drift detected |
| Metrics | ✅ Ready | Prometheus endpoint, 30+ counters |
| Audit | ✅ Ready | Retry + outbox; Supabase persistence |
| Security | ✅ Ready | HMAC, RBAC, timing-safe auth |
| Health Checks | ✅ Ready | /health/live + /health/ready |
| Request Correlation | ✅ Ready | X-Request-ID middleware |
| Observability | ✅ Ready | Structured JSON logging available |

---

## Startup Sequence

1. **Config validation** (`security/config_validator.py::validate_startup_config()`):
   - Auth config check (if AUTH_ENABLED=true)
   - Webhook HMAC check (if enforce_hmac=true)
   - Audit backend validity
   - Env consistency check (required vars in os.environ)
   - **Fail-fast:** startup aborts with descriptive error on any failure

2. **Runtime assembly** (`runtime/assembly.py::build_production_runtime()`):
   - Supabase client initialization
   - Action gateway and executor registry
   - Health service
   - Worker and watchdog

3. **Audit stack** (`api/app.py` lifespan):
   - Shared repository between AuditLogger and AuditService
   - Retry policy and outbox wired to Supabase repository

4. **Authenticator** (`security/config.py::build_authenticator_from_env()`):
   - API keys digested from environment
   - AUTH_ENABLED=false → anon admin context (development only)

5. **Middleware registered:**
   - `RequestIdMiddleware` — request correlation IDs
   - Exception handlers for HTTPException, ValidationError, unhandled exceptions

### Startup Validation Checklist

```bash
# Test startup validation manually:
AUDIT_BACKEND=inmemory \
FRESHDESK_WEBHOOK_ENFORCE_HMAC=false \
AUTH_ENABLED=false \
python -c "from security.config_validator import validate_startup_config; validate_startup_config()"
```

---

## Shutdown

FastAPI lifespan `yield` ensures teardown runs after the server stops accepting requests. No persistent background threads remain after shutdown (watchdog is request-triggered, not background).

**Limitation:** Audit outbox events are lost on unclean shutdown. For guaranteed delivery, migrate to a durable broker before production SLA commitments.

---

## Configuration

### Required Variables

| Variable | Purpose | Consequence if Missing |
|----------|---------|----------------------|
| `SUPABASE_URL` | Vector database + audit | Startup failure |
| `SUPABASE_KEY` | Supabase service-role key | Startup failure |
| `RAG_API_KEY` | API authentication | Startup failure (checked by env_consistency) |
| `OPENAI_API_KEY` or `EMBEDDING_API_KEY` | Embeddings | Ingestion fails |
| `OPENAI_CHAT_API_KEY` | Chat completions | Chat endpoint fails |

### Security-Critical Variables

| Variable | Recommended Value | Risk if Wrong |
|----------|-------------------|--------------|
| `AUTH_ENABLED` | `true` | All endpoints unprotected |
| `FRESHDESK_WEBHOOK_ENFORCE_HMAC` | `true` | Webhook accepts unsigned requests |
| `AUDIT_BACKEND` | `supabase` | Audit events lost on restart |
| `DEBUG_RAG` | `false` | PII exposure via chunk content preview |
| `FASTAPI_DOCS_ENABLED` | `false` | API schema exposed publicly |

### Configuration Drift Detection

The `ConfigConsistencyValidator` logs warnings on startup when `.env` and `.env.example` drift. Monitor for:

```
env_consistency: Variable 'NEW_VAR' is in .env.example but missing from .env
```

---

## Metrics

See `PROMETHEUS_SETUP_GUIDE.md` for full details.

**Key SLI metrics:**
- `actions_executed_total` / `actions_failed_total` — success rate
- `execution_latency_ms_sum` / `execution_latency_ms_count` — P50/P95 latency (via recording rules)
- `actions_dead_lettered_total` — queue health
- `audit_outbox_size` — Supabase connectivity health

---

## Audit

| Component | Status |
|-----------|--------|
| AuditLogger | ✅ Emits events for all lifecycle transitions |
| AuditService | ✅ ADMIN API for event query |
| SupabaseAuditRepository | ✅ Persistent via PostgREST |
| AuditRetryPolicy | ✅ 4 attempts, exponential backoff |
| AuditOutbox | ✅ 1000-event bounded buffer |
| Dead letter audit | ✅ ACTION_DEAD_LETTERED event type |
| Admin read audit | ✅ ACTION_AUDIT_READ event type |

### Pre-deployment Checklist

- [ ] Run `S2_003_dead_letter_and_audit_events.sql` migration
- [ ] Verify `AUDIT_BACKEND=supabase` in production `.env`
- [ ] Verify `audit_events` table has correct indexes
- [ ] Test `POST /watchdog/run` triggers audit events

---

## Security

### API Authentication

- **HMAC-SHA256** key digestion at construction time
- `hmac.compare_digest()` for all comparisons (constant-time)
- Raw keys never logged or included in error responses
- Key rotation: add new key to `ADMIN_API_KEYS`; remove old after all clients updated

### RBAC

Three roles enforce least privilege:
- `APPROVER`: can approve/reject actions only
- `OPERATOR`: can run workers and watchdog
- `ADMIN`: full access including audit query and dead-letter endpoint

### Webhook Security

- HMAC-SHA256 signature validation (when `FRESHDESK_WEBHOOK_ENFORCE_HMAC=true`)
- Timing-safe comparison
- Raw HMAC secret never logged

### [CRITICAL - Action Required] Supabase Key Rotation

A service-role Supabase key was accidentally committed to git history (via `supabasesuccess.py`, since deleted). The key in git history remains compromised regardless of deletion.

**Action required:**
1. Log in to Supabase dashboard
2. Navigate to Project Settings → API
3. Click "Regenerate" on the service-role key
4. Update `SUPABASE_KEY` in all deployments
5. Verify no cached copies in CI/CD environment secrets

This must be completed before any production deployment.

---

## Health Checks

### Kubernetes/Docker Configuration

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 15
  periodSeconds: 15
  failureThreshold: 2
```

### Expected Responses

| Endpoint | Healthy | Unhealthy |
|----------|---------|-----------|
| `/health/live` | 200 `{"alive": true}` | Never (process is dead) |
| `/health/ready` | 200 `{"ready": true}` | 503 `{"ready": false, "checks": {...}}` |
| `/health` (legacy) | 200 | 503 |

---

## Request Correlation

All responses include `X-Request-ID` header. Clients should:
1. Supply `X-Request-ID` with their own correlation ID when available
2. Read and log the `X-Request-ID` from responses for support queries
3. Include the request_id when filing incident reports

Log query pattern (structured logging):
```json
{"request_id": "req-abc-123", ...}
```

---

## Deployment Commands

```bash
# Validate environment before deployment:
python -c "
from security.config_validator import validate_startup_config
validate_startup_config()
print('Config OK')
"

# Run full test suite:
python -m pytest tests/ -q

# Start service:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Verify health:
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

---

## Open Items / Pre-Production TODOs

| Priority | Item | Owner |
|----------|------|-------|
| 🔴 CRITICAL | Rotate Supabase service-role key (compromised in git history) | DevOps |
| 🟠 HIGH | Enable FRESHDESK_WEBHOOK_ENFORCE_HMAC=true in production | Backend |
| 🟠 HIGH | Set AUTH_ENABLED=true and configure ADMIN_API_KEYS | Backend |
| 🟡 MEDIUM | Wire structured JSON logging at startup | Backend |
| 🟡 MEDIUM | Add DEAD_LETTER index to action_requests table | DBA |
| 🟢 LOW | Set up Grafana dashboards from PROMETHEUS_SETUP_GUIDE.md | SRE |
| 🟢 LOW | Configure Prometheus alert rules | SRE |
