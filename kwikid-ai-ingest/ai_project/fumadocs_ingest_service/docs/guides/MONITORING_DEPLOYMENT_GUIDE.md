# Monitoring Deployment Guide — Sprint 2.11.2 Phase 6

**Date:** 2026-06-04

---

## Overview

The KwikID AI Ingest Service exposes two Prometheus metrics endpoints:

| Endpoint | Purpose | Content |
|----------|---------|---------|
| `GET /metrics` | RAG infrastructure metrics | HTTP requests, retrieval latency, LLM latency, rate limit rejections |
| `GET /gateway/metrics` | Action gateway metrics | Action lifecycle counters, execution latency |

Both endpoints require no authentication and return Prometheus text exposition format.

---

## 1. Prerequisites

### Enable Prometheus metrics

Add to `.env`:
```
PROMETHEUS_ENABLED=true
```

Install the client library:
```bash
pip install prometheus-client>=0.21
```

### Required env vars for the service

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_KEY=<service-role-key>
RAG_API_KEY=<your-api-key>
OPENAI_API_KEY=<your-openai-key>
PROMETHEUS_ENABLED=true
```

---

## 2. Prometheus Configuration

Add to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: kwikid-rag-infrastructure
    static_configs:
      - targets: ['kwikid-ai-ingest:8000']
    metrics_path: /metrics
    scrape_interval: 15s

  - job_name: kwikid-action-gateway
    static_configs:
      - targets: ['kwikid-ai-ingest:8000']
    metrics_path: /gateway/metrics
    scrape_interval: 15s
```

Replace `kwikid-ai-ingest:8000` with the actual service hostname/IP.

---

## 3. Key Metrics Reference

### RAG Infrastructure (`/metrics`)

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Request count by method, path, status |
| `http_request_duration_seconds` | Histogram | Latency by method, path |
| `retrieval_latency_seconds` | Histogram | Pipeline stage latency (embedding, semantic, keyword, fusion, rerank) |
| `llm_latency_seconds` | Histogram | Generation latency by model |
| `rate_limit_rejections_total` | Counter | Rate limit events by path |
| `active_requests` | Gauge | In-flight requests by path |
| `retrieval_candidates_count` | Histogram | Candidate counts per stage |

### Action Gateway (`/gateway/metrics`)

| Metric | Type | Description |
|--------|------|-------------|
| `actions_created_total` | Counter | Proposed actions |
| `actions_approved_total` | Counter | Human-approved actions |
| `actions_rejected_total` | Counter | Human-rejected actions |
| `actions_executed_total` | Counter | Successfully executed actions |
| `actions_failed_total` | Counter | Failed execution attempts |
| `actions_dead_lettered_total` | Counter | Actions exhausting retry budget |
| `actions_rolled_back_total` | Counter | Rolled back actions |
| `execution_latency_ms_sum` / `_count` | Latency | Execution time histogram |
| `rollback_latency_ms_sum` / `_count` | Latency | Rollback time histogram |

---

## 4. Grafana Dashboard Queries

### RAG Service SLIs

**Request rate (5m):**
```promql
rate(http_requests_total[5m])
```

**Error rate:**
```promql
rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])
```

**p95 request latency:**
```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```

**RAG retrieval p95:**
```promql
histogram_quantile(0.95, rate(retrieval_latency_seconds_bucket[5m]))
```

**LLM generation p95:**
```promql
histogram_quantile(0.95, rate(llm_latency_seconds_bucket[5m]))
```

### Action Gateway SLIs

**Action success rate:**
```promql
rate(actions_executed_total[5m]) /
(rate(actions_executed_total[5m]) + rate(actions_failed_total[5m]))
```

**Dead letter rate:**
```promql
rate(actions_dead_lettered_total[5m])
```

**Execution latency p95:**
```promql
rate(execution_latency_ms_sum[5m]) / rate(execution_latency_ms_count[5m])
```

---

## 5. Health Check Endpoints for Kubernetes

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

Both endpoints are unauthenticated (no `X-API-Key` required).

---

## 6. Recommended Alert Rules

```yaml
groups:
  - name: kwikid-rag
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 2m
        annotations:
          summary: "KwikID RAG error rate > 5%"

      - alert: HighLLMLatency
        expr: histogram_quantile(0.95, rate(llm_latency_seconds_bucket[5m])) > 30
        for: 5m
        annotations:
          summary: "LLM p95 latency > 30s"

      - alert: DeadLetterQueueGrowing
        expr: increase(actions_dead_lettered_total[1h]) > 5
        for: 5m
        annotations:
          summary: "Actions accumulating in dead letter queue"

      - alert: RateLimitHigh
        expr: rate(rate_limit_rejections_total[5m]) > 1
        for: 2m
        annotations:
          summary: "Rate limit rejections occurring"
```
