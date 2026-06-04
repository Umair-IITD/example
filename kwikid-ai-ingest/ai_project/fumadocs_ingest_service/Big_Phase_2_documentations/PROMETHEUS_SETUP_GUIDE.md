# Prometheus Setup Guide — KwikID AI Ingest Service

## Overview

The KwikID AI Ingest Service exposes a Prometheus-compatible metrics endpoint at `GET /metrics`. This guide covers setup for local development, staging, and production.

## Prerequisites

- Docker and Docker Compose (for the bundled stack)
- OR Prometheus 2.x installed manually
- KwikID service running and reachable

## Quick Start (Docker Compose)

```bash
# From the service root directory:
cd monitoring
docker-compose -f docker-compose.monitoring.yml up -d

# Verify Prometheus is scraping:
curl http://localhost:9090/api/v1/targets | python -m json.tool

# View metrics in browser:
open http://localhost:9090/graph?g0.expr=actions_executed_total
```

## Metrics Endpoint

```
GET /metrics
Content-Type: text/plain; version=0.0.4
```

No authentication required. The endpoint is public for Prometheus scraping compatibility.

**Sample output:**

```
# HELP actions_created_total Total actions proposed by the AI system
# TYPE actions_created_total counter
actions_created_total 157

# HELP actions_executed_total Total actions successfully executed
# TYPE actions_executed_total counter
actions_executed_total 143

# HELP actions_failed_total Total actions that failed execution (all severities)
# TYPE actions_failed_total counter
actions_failed_total 12

# HELP execution_latency_ms_sum Execution latency from executor.execute() in milliseconds (sum)
# TYPE execution_latency_ms_sum gauge
execution_latency_ms_sum 45823.456

# HELP execution_latency_ms_count Execution latency from executor.execute() in milliseconds (count)
# TYPE execution_latency_ms_count counter
execution_latency_ms_count 143
```

## Scrape Configuration

### prometheus.yml (provided in monitoring/)

```yaml
scrape_configs:
  - job_name: kwikid_ai_ingest
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets:
          - localhost:8000  # change for production
```

### Kubernetes Service Monitor (Prometheus Operator)

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: kwikid-ai-ingest
  namespace: monitoring
spec:
  selector:
    matchLabels:
      app: kwikid-ai-ingest
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
```

## Key Metrics Reference

### Throughput

```promql
# Action throughput (per second)
rate(actions_executed_total[5m])

# Action failure rate
rate(actions_failed_total[5m]) / rate(actions_executed_total[5m])

# Auth failure rate
rate(auth_failure_total[5m])
```

### Latency

```promql
# Average execution latency (ms)
execution_latency_ms_sum / execution_latency_ms_count

# Average rollback latency (ms)
rollback_latency_ms_sum / rollback_latency_ms_count
```

### Queue Depth

```promql
# Dead letter queue growth
increase(actions_dead_lettered_total[10m])

# Audit outbox depth (current)
audit_outbox_size

# Audit retry failure rate
rate(audit_retry_failure_total[5m])
```

### Watchdog

```promql
# Actions expired by SLA watchdog (per hour)
increase(watchdog_expired_actions_total[1h])
```

## Grafana Setup

1. Start the monitoring stack: `docker-compose -f monitoring/docker-compose.monitoring.yml up -d`
2. Open Grafana: http://localhost:3000 (admin/admin)
3. Add data source: Prometheus → URL: `http://prometheus:9090`
4. Create dashboard with panels using the PromQL queries above

### Recommended Dashboard Panels

| Panel | PromQL | Visualization |
|-------|--------|---------------|
| Action throughput | `rate(actions_executed_total[5m])` | Time series |
| Failure rate | `rate(actions_failed_total[5m]) / (rate(actions_executed_total[5m]) + 1)` | Gauge |
| Avg execution latency | `execution_latency_ms_sum / execution_latency_ms_count` | Stat |
| Dead letter total | `actions_dead_lettered_total` | Stat |
| Auth failures | `rate(auth_failure_total[5m])` | Time series |
| Audit outbox depth | `audit_outbox_size` | Time series |
| Watchdog expirations | `increase(watchdog_expired_actions_total[1h])` | Time series |

## Alert Rules

Add to a Prometheus `rules.yml` file:

```yaml
groups:
  - name: kwikid_action_gateway
    interval: 30s
    rules:
      - alert: HighActionFailureRate
        expr: rate(actions_failed_total[5m]) / (rate(actions_executed_total[5m]) + 0.001) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Action failure rate >10% for 2 minutes"
          runbook: "Check executor logs; verify downstream APIs are reachable"

      - alert: DeadLetterQueueGrowing
        expr: increase(actions_dead_lettered_total[10m]) > 5
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "5+ actions dead-lettered in 10 minutes"
          runbook: "Check worker logs; verify external API health"

      - alert: AuditOutboxHigh
        expr: audit_outbox_size > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Audit outbox depth >100 for 5 minutes — Supabase may be unreachable"
          runbook: "Check Supabase connectivity; audit events will be lost on restart"

      - alert: WatchdogNotRunning
        expr: increase(watchdog_runs_total[15m]) == 0
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "SLA watchdog has not run in 15 minutes"

      - alert: AuthFailureSpike
        expr: rate(auth_failure_total[5m]) > 10
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "High auth failure rate — possible brute force attempt"
```

## Production Configuration

For production deployments, update `monitoring/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: kwikid_ai_ingest
    scrape_interval: 15s
    scheme: https  # if behind TLS
    static_configs:
      - targets:
          - your-production-host:8000
    # If service is behind a load balancer with multiple instances:
    # tls_config:
    #   insecure_skip_verify: false
    #   ca_file: /etc/ssl/certs/ca.crt
```

## Troubleshooting

**`/metrics` returns empty or error:**
- Verify the service is running: `curl http://localhost:8000/health/live`
- Check that `PROMETHEUS_ENABLED` (informational only — `/metrics` is always enabled) is not causing confusion

**Prometheus target shows `DOWN`:**
- Check the target address in `prometheus.yml`
- From inside Docker, use `host.docker.internal:8000` to reach the host machine
- Verify firewall rules allow port 8000

**Metrics not updating:**
- Confirm actions are flowing through the gateway
- Check that `MetricsService` is wired into the runtime (see `runtime/assembly.py`)
