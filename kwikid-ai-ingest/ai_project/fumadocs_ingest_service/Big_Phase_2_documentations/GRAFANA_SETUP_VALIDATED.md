# Grafana Setup — Validated Configuration

**Date:** 2026-06-04
**Sprint:** 2.11.1
**Auditor:** Principal Staff Engineer / SRE Lead

---

## Status

| Component | Status | Notes |
|-----------|--------|-------|
| `/metrics` endpoint (app/main.py) | Fixed — returns 200 unauthenticated | Was returning 401 before Sprint 2.11.1 |
| Prometheus scrape config | Present (`monitoring/prometheus.yml`) | Target: `host.docker.internal:8000` |
| Docker Compose stack | Present (`monitoring/docker-compose.monitoring.yml`) | Prometheus + Grafana |
| Grafana data source | Manual setup required | See steps below |
| Alert rules | Defined in `PROMETHEUS_SETUP_GUIDE.md` | Not yet applied |

---

## Pre-fix vs Post-fix: Prometheus Target Status

### Before Sprint 2.11.1

```
GET /metrics → 401 Unauthorized
```
Root cause: `/metrics` was not in `_UNPROTECTED_PATHS` (`app/security.py`). Additionally, `PROMETHEUS_ENABLED=false` (the default) caused the handler to return 404 even for authenticated requests.

### After Sprint 2.11.1

```
GET /metrics → 200 text/plain; version=0.0.4
```
Both issues resolved:
1. `/metrics` added to `_UNPROTECTED_PATHS` — Prometheus scrapes without X-API-Key
2. `PROMETHEUS_ENABLED` gate removed from handler — endpoint always returns data

---

## Prometheus Configuration (Validated)

**File:** `monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    service: kwikid-ai-ingest
    environment: production

scrape_configs:
  - job_name: kwikid_ai_ingest
    scrape_interval: 15s
    scrape_timeout: 10s
    metrics_path: /metrics
    scheme: http
    static_configs:
      - targets:
          - host.docker.internal:8000    # From inside Docker → reaches host machine port 8000
        labels:
          service: kwikid-ai-ingest
          component: action-gateway

  - job_name: prometheus
    scrape_interval: 30s
    static_configs:
      - targets:
          - localhost:9090
```

**Key: `host.docker.internal:8000`** is the correct target when running Prometheus in Docker on Windows/Mac. It resolves to the host machine, where `app/main.py` is running on port 8000. For Linux Docker deployments, use the host's LAN IP instead (e.g., `172.17.0.1:8000`).

---

## Docker Compose Stack

**File:** `monitoring/docker-compose.monitoring.yml`

### Starting the Stack

```bash
cd monitoring
docker-compose -f docker-compose.monitoring.yml up -d
```

### Verifying Prometheus is Scraping

```bash
# Check Prometheus targets (should show kwikid_ai_ingest as UP):
curl http://localhost:9090/api/v1/targets | python -m json.tool | grep -A5 "kwikid"

# Query a metric directly:
curl "http://localhost:9090/api/v1/query?query=http_requests_total" | python -m json.tool
```

### Verifying the Metrics Endpoint Directly

```bash
# From host machine:
curl http://localhost:8000/metrics

# Expected: HTTP 200, Content-Type: text/plain; version=0.0.4
# When PROMETHEUS_ENABLED=false (default): returns placeholder text
# When PROMETHEUS_ENABLED=true: returns full Prometheus exposition format
```

---

## Enabling Prometheus Metrics Collection

To collect real metrics (not just the placeholder), set `PROMETHEUS_ENABLED=true` in `.env`:

```bash
echo "PROMETHEUS_ENABLED=true" >> .env
# Then restart the service:
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

When enabled, the following metrics are collected:

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | HTTP requests by method/path/status |
| `http_request_duration_seconds` | Histogram | Request latency by method/path |
| `retrieval_latency_seconds` | Histogram | Retrieval pipeline stage latency |
| `llm_latency_seconds` | Histogram | LLM generation latency by model |
| `rate_limit_rejections_total` | Counter | Rate limit rejection events by path |
| `active_requests` | Gauge | In-flight requests by path |
| `retrieval_candidates_count` | Histogram | Retrieval candidates at each stage |

---

## Grafana Setup (Step by Step)

### 1. Start the monitoring stack

```bash
cd monitoring
docker-compose -f docker-compose.monitoring.yml up -d
```

### 2. Open Grafana

Navigate to: `http://localhost:3000`
Default credentials: `admin` / `admin` (change immediately in production)

### 3. Add Prometheus data source

1. Gear icon → Data Sources → Add data source
2. Select: Prometheus
3. URL: `http://prometheus:9090` (within Docker Compose network)
4. Click "Save & Test" — should show "Data source is working"

### 4. Create Dashboard

Import panels using these PromQL queries:

#### Throughput Panel

```promql
# RAG chat requests per second
rate(http_requests_total{path="/rag/chat"}[5m])
```

#### Error Rate Panel

```promql
# HTTP 5xx error rate across all paths
rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])
```

#### P95 Latency Panel

```promql
# 95th percentile request latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

#### Rate Limit Rejections Panel

```promql
rate(rate_limit_rejections_total[5m])
```

#### Active Requests Panel

```promql
sum(active_requests)
```

#### Retrieval Stage Latency Panel

```promql
# Average latency at each retrieval stage
rate(retrieval_latency_seconds_sum[5m]) / rate(retrieval_latency_seconds_count[5m])
```

---

## Alert Rules

Apply to Prometheus `rules.yml` (defined in full in `PROMETHEUS_SETUP_GUIDE.md`):

| Alert | Condition | Severity |
|-------|-----------|----------|
| HighErrorRate | >10% 5xx for 2 min | warning |
| HighLatency | P95 > 5s for 5 min | warning |
| RateLimitSpike | >5 rejections/min | info |

---

## Production Prometheus Configuration

For production deployments (service behind TLS), update `monitoring/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: kwikid_ai_ingest
    scheme: https
    tls_config:
      insecure_skip_verify: false
    static_configs:
      - targets:
          - your-production-host:443
```

**Important:** The `/metrics` endpoint has no authentication. If the service is internet-facing, protect it at the load balancer level (restrict `/metrics` to internal IPs only) rather than adding auth to the endpoint.

---

## Validation Checklist

```
[x] GET /metrics returns 200 without X-API-Key (regression-tested)
[x] Content-Type is text/plain; version=0.0.4 (regression-tested)
[x] /metrics not in authenticated route set (regression-tested)
[x] monitoring/prometheus.yml present with correct target
[x] monitoring/docker-compose.monitoring.yml present
[ ] Prometheus target status = UP (requires running service + Docker)
[ ] Grafana dashboard created (manual step)
[ ] Alert rules applied (manual step)
[ ] PROMETHEUS_ENABLED=true set in production .env
```
