# Observability and Logging

## Structured Logging

All service logs use structured JSON format via `observability/logger.py`.

**Log format:**
```json
{
  "timestamp": "2026-05-22T10:30:45.123Z",
  "level": "INFO",
  "logger": "kwikid.ingest",
  "event": "rag_chat",
  "client": "kwikid",
  "session": "sess_abc123",
  "confidence": "high",
  "chunks": 5,
  "insufficient": false,
  "hybrid": true,
  "route": "SOP",
  "duration_ms": 342
}
```

Key log events:

| Event | Level | Description |
|-------|-------|-------------|
| `rag_chat` | INFO | Every /rag/chat request (client, session, confidence, chunk count) |
| `retrieval_complete` | INFO | Retrieval latency, candidate counts, hybrid mode |
| `embedding_error` | ERROR | Failed embedding batch with retry attempt |
| `circuit_breaker_open` | WARNING | Embedding circuit breaker activated |
| `circuit_breaker_reset` | INFO | Circuit breaker recovered |
| `api_key_rejected` | WARNING | Invalid API key attempt (includes IP, key hint) |
| `rate_limit_exceeded` | WARNING | Per-IP rate limit hit |
| `webhook_signature_invalid` | WARNING | Freshdesk webhook HMAC mismatch |
| `ready_check_failed` | ERROR | Supabase or embeddings not reachable |
| `ingest_complete` | INFO | Ingestion run summary (docs, chunks, embeddings) |

**Log level**: Controlled by `LOG_LEVEL=info`. Set `debug` for verbose tracing (not for production).

## Log Storage

Logs are written to:
- **Container stdout**: Default — collected by Docker logging driver
- **`./logs/` directory**: Via log file handler (gitignored, volume-mounted in Docker)

Log directory is bind-mounted in Docker Compose to persist across container restarts.

## Request Tracing

When `DEBUG_TRACE=true`, each request writes a trace file to `TRACE_DIR=./traces/`:

```
traces/
├── 20260519_123109_69f33d8a.json
├── 20260519_125403_9728a82a.json
└── ...
```

**Trace file format:**
```json
{
  "trace_id": "69f33d8a",
  "timestamp": "2026-05-19T12:31:09Z",
  "query": "account lockout issue",
  "query_hash": "a1b2c3d4",
  "tenant": "kwikid",
  "retrieval_candidates_count": 5,
  "retrieval_latency_ms": 287,
  "top_matches": [
    {"id": "...", "source_type": "sop", "similarity": 0.742}
  ]
}
```

Traces are gitignored (runtime artifacts) but volume-mounted in Docker.

**PRODUCTION**: `DEBUG_TRACE=false`. Enable only for specific debugging sessions, disable immediately after.

## Prometheus Metrics

When `PROMETHEUS_ENABLED=true`, the `/metrics` endpoint exposes Prometheus text format.

**Available metrics:**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `http_requests_total` | Counter | method, path, status | Total HTTP requests |
| `http_request_duration_seconds` | Histogram | method, path | Request duration |
| `retrieval_latency_seconds` | Histogram | stage | Retrieval phase latency |
| `llm_latency_seconds` | Histogram | model | LLM generation latency |
| `retrieval_candidates` | Histogram | stage | Retrieved chunk counts |
| `active_requests` | Gauge | path | Currently processing requests |
| `rate_limit_rejections_total` | Counter | path | Rate limit hits |

**Scraping config (Prometheus):**
```yaml
scrape_configs:
  - job_name: 'kwikid-ingest'
    static_configs:
      - targets: ['kwikid-ingest:8000']
    bearer_token: '<your-api-key>'   # /metrics requires X-API-Key
```

**Graceful degradation**: If `prometheus-client` is not installed or metrics fail to initialize, all `record_*` calls silently no-op. The service starts normally.

## Ingestion Run Reports

Each ingestion run produces structured reports in `data/reports/`:

**JSON report** (`ingestion_{timestamp}_{run_id}.json`):
```json
{
  "run_id": "274854d2",
  "started_at": "2026-05-15T09:37:53Z",
  "completed_at": "2026-05-15T09:42:11Z",
  "duration_seconds": 258.3,
  "status": "completed",
  "run_mode": "delta",
  "total_source_rows": 1247,
  "documents_processed": 312,
  "documents_skipped": 935,
  "documents_failed": 0,
  "chunks_created": 874,
  "chunks_skipped": 0,
  "chunks_failed": 0,
  "embeddings_generated": 874,
  "embedding_api_calls": 14,
  "embedding_tokens_used": 1048576,
  "automation_label_counts": {"safe_to_automate": 156, "requires_human": 156},
  "client_counts": {"kwikid": 312}
}
```

These are gitignored (runtime) but volume-mounted for persistence.

## Redis Rate Limit Observability

When Redis rate limiting is enabled, rate limit state is visible via Redis:

```bash
# Connect to Redis
redis-cli -h localhost -p 6379

# Check rate limit keys (sliding window entries)
KEYS "rl:*"
TTL "rl:192.168.1.1:/rag/chat"
```

Rate limit state is intentionally ephemeral — not persisted to disk. It resets on Redis restart.

## Health Monitoring

Use the `/health` and `/ready` endpoints for monitoring:

```bash
# Liveness (is the process alive?)
curl http://localhost:8000/health

# Readiness (can it serve traffic?)
curl http://localhost:8000/ready
```

Both return HTTP 200 on success. Configure monitoring (Uptime Kuma, StatusPage, Datadog) to alert on non-200 responses.

**Note**: `/ready` calls Supabase and the embedding provider. It may be slightly slow (50–200ms). Use `/health` for Kubernetes liveness probes (fast) and `/ready` for readiness probes.
