# Sprint 2.13 — Production Hardening & Operational Visibility

## Overview

Sprint 2.13 adds read-only operational visibility into the Action Gateway without modifying any existing behaviour. Operators can now answer the following questions via API without touching the database directly:

| Question | Endpoint |
|---|---|
| How many actions are in each state? | `GET /admin/action-gateway/summary` |
| What failed and why? | `GET /admin/action-gateway/dead-letter` |
| What retried, how many times? | `GET /admin/action-gateway/retries` |
| What is blocked and for how long? | `GET /admin/action-gateway/stuck` |
| Which worker processed what? | `GET /admin/action-gateway/workers` |

---

## Architecture

### New components

```
case_engine/action_gateway_operations.py   — ActionGatewayOperationsService
api/admin_response_models.py               — Pydantic response models
api/routes/gateway_admin.py                — 5 admin endpoints
```

### Modified components

```
case_engine/action_repository.py           — 5 new read-only query methods
metrics/collector.py                       — COUNTER_ACTIONS_RETRIED constant
metrics/service.py                         — record_action_retried(), get_gateway_totals()
case_engine/action_gateway.py              — retried/dead-letter metrics wired
runtime/assembly.py                        — ProductionRuntime.operations field
app/main.py                                — gateway_admin router registered
```

### Data flow

```
HTTP request
  → require_admin (RBAC gate)
  → gateway_admin router
  → ActionGatewayOperationsService
  → ActionRepository (read-only Supabase queries)
  → frozen dataclasses
  → Pydantic response models
  → JSON response
```

---

## ActionGatewayOperationsService

`case_engine/action_gateway_operations.py`

Read-only service that wraps the repository and metrics service. All methods:
- Never raise — repository exceptions are caught and returned as empty results.
- Return frozen dataclasses (not dicts, not Pydantic models).
- Are safe to call concurrently.

### Constructor

```python
ActionGatewayOperationsService(
    repository: ActionRepository,
    metrics_service: MetricsService | None = None,
)
```

### Methods

#### `get_state_summary(client=None) → StateSummary`

Returns action counts grouped by state plus in-process metrics totals.

- `state_counts` — DB-backed current truth (one `StateCount` per `ActionState`).
- `total_live` — sum of counts for `PROPOSED`, `AWAITING_APPROVAL`, `APPROVED`, `EXECUTING`, `ROLLING_BACK`.
- `metrics_totals` — in-process counters from `MetricsService.get_gateway_totals()`. Resets on service restart.

#### `get_dead_letter_summary(client=None, limit=50, offset=0) → DeadLetterSummary`

Returns dead-lettered actions with failure details. Pagination is applied in-process.

- `total` — total dead-letter count (before pagination).
- `items` — `DeadLetterItem` slice `[offset : offset + limit]`.

#### `get_retry_summary(client=None) → RetrySummary`

Returns all actions with `execution_attempt >= 2` across all final states. Covers succeeded, failed, and dead-lettered retries.

#### `get_stuck_actions(executing_threshold_seconds=900, approval_threshold_seconds=86400, client=None) → StuckActionsSummary`

Detects two categories of stuck actions:

- `stuck_executing` — `EXECUTING` actions where `now - execution_started_at > executing_threshold_seconds` (default 15 min).
- `stale_approval` — `AWAITING_APPROVAL` actions where `now - proposed_at > approval_threshold_seconds` (default 24 h).

This endpoint is read-only. It does NOT modify any action state and does NOT expire actions (the SLA watchdog handles expiry).

#### `get_worker_summary(client=None, window_hours=24) → WorkerSummary`

Derives per-worker statistics from recently terminal actions (`EXECUTED`, `FAILED`, `DEAD_LETTER`) grouped by `executor_id`. Bounded by `window_hours` to avoid full-table scans.

---

## Admin API Endpoints

All endpoints:
- Prefix: `/admin/action-gateway/`
- Authorization: `X-API-Key` with ADMIN role required (403 if non-admin, 401 if unauthenticated).
- Read-only: zero state mutations.
- Never 500 on empty data — return empty lists with 200.
- Return 503 if the gateway runtime is not yet initialised.

### GET /admin/action-gateway/summary

**Query params:** `client` (optional tenant slug filter)

**Response:**
```json
{
  "client": null,
  "state_counts": [
    {"state": "APPROVED", "count": 12},
    {"state": "DEAD_LETTER", "count": 3},
    {"state": "EXECUTING", "count": 1},
    {"state": "PROPOSED", "count": 5}
  ],
  "total_live": 18,
  "metrics_totals": {
    "actions_created_total": 450,
    "actions_executed_total": 421,
    "actions_failed_total": 26,
    "actions_retried_total": 9,
    "actions_dead_lettered_total": 3
  },
  "checked_at": "2024-06-08T12:00:00+00:00"
}
```

### GET /admin/action-gateway/dead-letter

**Query params:** `client`, `limit` (default 100, max 500), `offset` (default 0)

**Response:**
```json
{
  "client": null,
  "actions": [
    {
      "action_id": "abc-123",
      "action_type": "add_note",
      "action_namespace": "ticket",
      "client": "unity_bank",
      "proposed_at": "2024-06-07T10:00:00+00:00",
      "dead_lettered_at": "2024-06-07T10:45:00+00:00",
      "failure_code": "EXEC_FAILED",
      "failure_reason": "Provider returned 503",
      "execution_attempt": 3,
      "max_attempts": 3
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0,
  "checked_at": "2024-06-08T12:00:00+00:00"
}
```

### GET /admin/action-gateway/retries

**Query params:** `client`

**Response:**
```json
{
  "client": null,
  "actions": [
    {
      "action_id": "def-456",
      "action_type": "add_note",
      "action_namespace": "ticket",
      "client": "unity_bank",
      "current_state": "EXECUTED",
      "execution_attempt": 2,
      "max_attempts": 3,
      "failure_code": null,
      "failure_reason": null,
      "proposed_at": "2024-06-08T09:00:00+00:00"
    }
  ],
  "total": 1,
  "checked_at": "2024-06-08T12:00:00+00:00"
}
```

### GET /admin/action-gateway/stuck

**Query params:** `client`, `executing_threshold_seconds` (default 900), `approval_threshold_seconds` (default 86400)

Thresholds are floored at 60 seconds to prevent nonsensical values.

**Response:**
```json
{
  "client": null,
  "stuck_executing": [
    {
      "action_id": "ghi-789",
      "action_type": "add_note",
      "action_namespace": "ticket",
      "client": "unity_bank",
      "executor_id": "worker-1",
      "execution_started_at": "2024-06-08T10:00:00+00:00",
      "execution_attempt": 1,
      "stuck_for_seconds": 3612.4
    }
  ],
  "stale_approval": [],
  "total_stuck": 1,
  "executing_threshold_seconds": 900,
  "approval_threshold_seconds": 86400,
  "checked_at": "2024-06-08T12:00:00+00:00"
}
```

### GET /admin/action-gateway/workers

**Query params:** `client`, `window_hours` (default 24, max 720)

**Response:**
```json
{
  "client": null,
  "workers": [
    {
      "worker_id": "worker-1",
      "successful": 120,
      "failed": 8,
      "dead_lettered": 2,
      "total_processed": 130,
      "last_execution_at": "2024-06-08T11:58:00+00:00"
    }
  ],
  "total_workers": 1,
  "window_hours": 24,
  "checked_at": "2024-06-08T12:00:00+00:00"
}
```

---

## Metrics (D2)

### New counter: `actions_retried_total`

Added to `MetricsCollector` and `MetricsService`.

- Incremented in `ActionGateway.record_failure()` when an action is re-queued (retry path).
- Separate from `actions_failed_total` which tracks all failure events.

### `MetricsService.get_gateway_totals()`

Returns a `dict[str, int]` of 13 gateway lifetime counters:

```python
{
    "actions_created_total": int,
    "actions_approved_total": int,
    "actions_rejected_total": int,
    "actions_executed_total": int,
    "actions_failed_total": int,
    "actions_expired_total": int,
    "actions_retried_total": int,
    "actions_rolled_back_total": int,
    "actions_rollback_failed_total": int,
    "actions_dead_lettered_total": int,
    "worker_execution_total": int,
    "worker_failure_total": int,
    "worker_rollback_total": int,
}
```

These are in-process counters and reset on service restart. They complement the DB-level counts returned by `get_state_summary()`.

---

## Repository Methods (new read-only queries)

Added to `case_engine/action_repository.py`:

| Method | Description |
|---|---|
| `count_by_state(client)` | One DB query per `ActionState`, returns `{state: count}` dict. Returns `{}` in offline mode. |
| `list_retried_actions(client)` | `execution_attempt >= 2`, all states, ordered by `proposed_at DESC`. |
| `list_stale_approval_actions(older_than_seconds, client)` | `AWAITING_APPROVAL` with `proposed_at < cutoff`. |
| `list_all_executing_actions(client)` | All `EXECUTING` actions regardless of age. |
| `list_recently_terminal_actions(hours, client)` | `EXECUTED`, `FAILED`, `DEAD_LETTER` from last N hours with `executor_id` set. |

All return empty lists in offline mode (`supabase_client=None`) and on query error.

---

## Assembly Wiring

`runtime/assembly.py` — `ProductionRuntime` dataclass now includes:

```python
@dataclass
class ProductionRuntime:
    ...
    operations: ActionGatewayOperationsService
```

`build_production_runtime()` instantiates it as:

```python
operations = ActionGatewayOperationsService(
    repository=repository,   # shared instance — same as gateway/runtime/watchdog
    metrics_service=metrics_service,
)
```

The `gateway_admin` router accesses it via `request.app.state.stack.operations`.

---

## Test Coverage

`tests/test_sprint213_operations.py` — 128 tests, all passing.

| Section | Tests |
|---|---|
| `get_state_summary` | 12 |
| `get_dead_letter_summary` | 10 |
| `get_retry_summary` | 8 |
| `get_stuck_actions` | 10 |
| `get_worker_summary` | 10 |
| Metrics aggregation | 15 |
| `GET /summary` endpoint | 8 |
| `GET /dead-letter` endpoint | 12 |
| `GET /retries` endpoint | 8 |
| `GET /stuck` endpoint | 10 |
| `GET /workers` endpoint | 10 |
| Response model completeness | 10 |
| Assembly wiring | 5 |

Full suite: **2046 passed, 4 skipped** (pre-existing skips unrelated to this sprint).

---

## Operational Runbook

### Checking gateway health

```bash
# All tenant summary
curl -H "x-api-key: $ADMIN_KEY" https://api.example.com/admin/action-gateway/summary

# Single tenant
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/summary?client=unity_bank"
```

### Investigating a dead-letter queue spike

```bash
# First page of dead-letter actions
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/dead-letter?limit=20"

# Specific tenant
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/dead-letter?client=unity_bank&limit=50"
```

### Finding stuck actions

```bash
# Default thresholds: executing > 15 min, approval > 24 h
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/stuck"

# Custom: executing > 5 min
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/stuck?executing_threshold_seconds=300"
```

### Worker attribution

```bash
# Last 24 hours (default)
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/workers"

# Last 48 hours
curl -H "x-api-key: $ADMIN_KEY" "https://api.example.com/admin/action-gateway/workers?window_hours=48"
```

---

## Design Constraints Honoured

- **Read-only**: no state mutations in any Sprint 2.13 code path.
- **No new background jobs**: all endpoints are synchronous request-response.
- **No new external dependencies**: no new packages added.
- **No state machine changes**: `ActionState`, retry rules, rollback rules, optimistic locking — all untouched.
- **Never 500 on empty data**: all endpoints return empty lists with 200.
- **Stack traces never in responses**: all error responses use `error_body()` with opaque messages.
- **Shared repository**: `operations` uses the same `ActionRepository` instance as the gateway, runtime, and watchdog — no split-brain.
