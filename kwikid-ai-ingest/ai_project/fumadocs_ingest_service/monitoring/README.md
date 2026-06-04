# Monitoring Stack — KwikID AI Ingest Service

This directory contains Prometheus and Grafana configuration for observing the KwikID Action Gateway.

## Prerequisites

- Docker and Docker Compose installed
- KwikID service running (default: `localhost:8000`)
- `PROMETHEUS_ENABLED=true` in `.env` (optional — `/metrics` always responds)

## Quick Start

```bash
# From the service root directory:
docker-compose -f monitoring/docker-compose.monitoring.yml up -d

# Check Prometheus targets:
open http://localhost:9090/targets

# Open Grafana:
open http://localhost:3000  # admin / admin (change on first login)
```

## Metrics Endpoint

The service exposes metrics at:

```
GET /metrics
```

No authentication required. Returns Prometheus text exposition format (`text/plain; version=0.0.4`).

Example response:

```
# HELP actions_created_total Total actions proposed by the AI system
# TYPE actions_created_total counter
actions_created_total 42
# HELP actions_executed_total Total actions successfully executed
# TYPE actions_executed_total counter
actions_executed_total 38
...
```

## Available Metrics

### Action Gateway Counters

| Metric | Type | Description |
|--------|------|-------------|
| `actions_created_total` | counter | AI-proposed actions |
| `actions_approved_total` | counter | Human-approved actions |
| `actions_rejected_total` | counter | Human-rejected actions |
| `actions_executed_total` | counter | Successfully executed actions |
| `actions_failed_total` | counter | Execution failures |
| `actions_expired_total` | counter | SLA watchdog expirations |
| `actions_rolled_back_total` | counter | Successful rollbacks |
| `actions_rollback_failed_total` | counter | Failed rollback attempts |
| `actions_dead_lettered_total` | counter | Dead-lettered actions |

### Authentication

| Metric | Type | Description |
|--------|------|-------------|
| `auth_success_total` | counter | Successful API key authentications |
| `auth_failure_total` | counter | Failed authentication attempts |

### Watchdog

| Metric | Type | Description |
|--------|------|-------------|
| `watchdog_runs_total` | counter | SLA watchdog invocations |
| `watchdog_expired_actions_total` | counter | Actions expired per watchdog run |

### Audit

| Metric | Type | Description |
|--------|------|-------------|
| `audit_events_written_total` | counter | Audit events persisted |
| `audit_events_read_total` | counter | Audit events read via API |
| `audit_retry_total` | counter | Audit write retries |
| `audit_retry_success_total` | counter | Retries that eventually succeeded |
| `audit_retry_failure_total` | counter | Exhausted retry attempts |
| `audit_outbox_size` | gauge | Current in-memory outbox depth |
| `audit_outbox_flush_success` | counter | Events flushed from outbox |
| `audit_outbox_flush_failure` | counter | Outbox flush failures |

### Worker

| Metric | Type | Description |
|--------|------|-------------|
| `worker_execution_total` | counter | Worker execution attempts |
| `worker_failure_total` | counter | Worker execution failures |
| `worker_rollback_total` | counter | Worker rollback attempts |

### Latency Summaries

| Metric | Description |
|--------|-------------|
| `execution_latency_ms_sum` | Total execution time in ms |
| `execution_latency_ms_count` | Number of observed executions |
| `rollback_latency_ms_sum` | Total rollback time in ms |
| `rollback_latency_ms_count` | Number of observed rollbacks |
| `approval_latency_ms_sum` | Total approval latency (proposed→approved) in ms |
| `approval_latency_ms_count` | Number of observed approvals |

## Prometheus Configuration

Edit `prometheus.yml` to change the scrape target:

```yaml
static_configs:
  - targets:
      - your-service-host:8000  # replace with actual host
```

For production deployments with multiple instances, use service discovery:

```yaml
scrape_configs:
  - job_name: kwikid_ai_ingest
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: kwikid-ai-ingest
        action: keep
```

## Grafana

After starting the stack, Grafana is available at `http://localhost:3000` (admin/admin).

1. Add a Prometheus data source: `http://prometheus:9090`
2. Create dashboards using the metrics listed above.

Key panels to create:

- **Action throughput**: `rate(actions_executed_total[5m])`
- **Failure rate**: `rate(actions_failed_total[5m]) / rate(actions_executed_total[5m])`
- **Auth failure rate**: `rate(auth_failure_total[5m])`
- **Execution latency (avg)**: `execution_latency_ms_sum / execution_latency_ms_count`
- **Dead letter queue**: `actions_dead_lettered_total`
- **Audit outbox depth**: `audit_outbox_size`

## Alert Rules

Example alert rules for a `prometheus_rules.yml`:

```yaml
groups:
  - name: kwikid_action_gateway
    rules:
      - alert: HighFailureRate
        expr: rate(actions_failed_total[5m]) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High action failure rate"

      - alert: DeadLetterQueueGrowing
        expr: increase(actions_dead_lettered_total[10m]) > 5
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "Dead letter queue accumulating rapidly"

      - alert: AuditOutboxDepthHigh
        expr: audit_outbox_size > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Audit outbox depth is high — Supabase may be unreachable"
```

## Stopping the Stack

```bash
docker-compose -f monitoring/docker-compose.monitoring.yml down

# Remove volumes (data will be lost):
docker-compose -f monitoring/docker-compose.monitoring.yml down -v
```
