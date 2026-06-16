"""
metrics/collector.py

Sprint 2.10: Thread-safe metrics storage.

MetricsCollector holds counters and latency accumulators. All mutations
are protected by a single threading.Lock — safe under concurrent workers
sharing one process. For multi-process deployments, replace with a
shared-memory or external backend (Redis, StatsD, etc.).

Design:
  - increment()       — O(1) counter increment
  - record_latency()  — O(1) sum/count accumulation
  - snapshot()        — returns a point-in-time copy (held under lock)
  - All operations are fire-and-forget; failures are logged, never raised.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

LOGGER = logging.getLogger(__name__)

# Canonical counter names — Action Gateway
COUNTER_ACTIONS_CREATED        = "actions_created_total"
COUNTER_ACTIONS_APPROVED       = "actions_approved_total"
COUNTER_ACTIONS_REJECTED       = "actions_rejected_total"
COUNTER_ACTIONS_EXECUTED       = "actions_executed_total"
COUNTER_ACTIONS_FAILED         = "actions_failed_total"
COUNTER_ACTIONS_EXPIRED        = "actions_expired_total"
COUNTER_ACTIONS_ROLLED_BACK    = "actions_rolled_back_total"
COUNTER_ACTIONS_ROLLBACK_FAILED = "actions_rollback_failed_total"
COUNTER_ACTIONS_DEAD_LETTERED  = "actions_dead_lettered_total"

# Canonical counter names — Auth
COUNTER_AUTH_SUCCESS           = "auth_success_total"
COUNTER_AUTH_FAILURE           = "auth_failure_total"

# Canonical counter names — Watchdog
COUNTER_WATCHDOG_RUNS          = "watchdog_runs_total"
COUNTER_WATCHDOG_EXPIRED       = "watchdog_expired_actions_total"

# Canonical counter names — Audit
COUNTER_AUDIT_EVENTS_WRITTEN   = "audit_events_written_total"
COUNTER_AUDIT_EVENTS_READ      = "audit_events_read_total"
COUNTER_AUDIT_RETRY            = "audit_retry_total"
COUNTER_AUDIT_RETRY_SUCCESS    = "audit_retry_success_total"
COUNTER_AUDIT_RETRY_FAILURE    = "audit_retry_failure_total"
COUNTER_AUDIT_OUTBOX_FLUSH_OK  = "audit_outbox_flush_success"
COUNTER_AUDIT_OUTBOX_FLUSH_ERR = "audit_outbox_flush_failure"

# Canonical counter names — Worker
COUNTER_WORKER_EXECUTION       = "worker_execution_total"
COUNTER_WORKER_FAILURE         = "worker_failure_total"
COUNTER_WORKER_ROLLBACK        = "worker_rollback_total"

# Sprint 2.13: retry counter (distinct from failure — fires on FAILED→APPROVED re-queue)
COUNTER_ACTIONS_RETRIED        = "actions_retried_total"

# Gauge name (current outbox depth)
GAUGE_AUDIT_OUTBOX_SIZE        = "audit_outbox_size"

# Canonical latency names
LATENCY_EXECUTION  = "execution_latency_ms"
LATENCY_ROLLBACK   = "rollback_latency_ms"
LATENCY_APPROVAL   = "approval_latency_ms"


class MetricsCollector:
    """
    Thread-safe in-process metrics store.

    Counters: monotonically increasing integer counters.
    Latencies: tracked as (sum, count) pairs for summary computation.
    """

    _COUNTER_NAMES: tuple[str, ...] = (
        COUNTER_ACTIONS_CREATED,
        COUNTER_ACTIONS_APPROVED,
        COUNTER_ACTIONS_REJECTED,
        COUNTER_ACTIONS_EXECUTED,
        COUNTER_ACTIONS_FAILED,
        COUNTER_ACTIONS_EXPIRED,
        COUNTER_ACTIONS_ROLLED_BACK,
        COUNTER_ACTIONS_ROLLBACK_FAILED,
        COUNTER_ACTIONS_DEAD_LETTERED,
        COUNTER_ACTIONS_RETRIED,
        COUNTER_AUTH_SUCCESS,
        COUNTER_AUTH_FAILURE,
        COUNTER_WATCHDOG_RUNS,
        COUNTER_WATCHDOG_EXPIRED,
        COUNTER_AUDIT_EVENTS_WRITTEN,
        COUNTER_AUDIT_EVENTS_READ,
        COUNTER_AUDIT_RETRY,
        COUNTER_AUDIT_RETRY_SUCCESS,
        COUNTER_AUDIT_RETRY_FAILURE,
        COUNTER_AUDIT_OUTBOX_FLUSH_OK,
        COUNTER_AUDIT_OUTBOX_FLUSH_ERR,
        COUNTER_WORKER_EXECUTION,
        COUNTER_WORKER_FAILURE,
        COUNTER_WORKER_ROLLBACK,
        GAUGE_AUDIT_OUTBOX_SIZE,
    )

    _LATENCY_NAMES: tuple[str, ...] = (
        LATENCY_EXECUTION,
        LATENCY_ROLLBACK,
        LATENCY_APPROVAL,
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {name: 0 for name in self._COUNTER_NAMES}
        self._latency_sums: dict[str, float] = {name: 0.0 for name in self._LATENCY_NAMES}
        self._latency_counts: dict[str, int] = {name: 0 for name in self._LATENCY_NAMES}

    def increment(self, name: str, by: int = 1) -> None:
        """Increment a counter by `by`. Creates the counter if it doesn't exist."""
        try:
            with self._lock:
                self._counters[name] = self._counters.get(name, 0) + by
        except Exception as exc:
            LOGGER.error("metrics.increment failed name=%s error=%s", name, exc)

    def record_latency(self, name: str, latency_ms: float) -> None:
        """Accumulate latency for summary computation."""
        try:
            with self._lock:
                self._latency_sums[name] = self._latency_sums.get(name, 0.0) + latency_ms
                self._latency_counts[name] = self._latency_counts.get(name, 0) + 1
        except Exception as exc:
            LOGGER.error("metrics.record_latency failed name=%s error=%s", name, exc)

    def get_counter(self, name: str) -> int:
        """Return current value of a counter (0 if unknown)."""
        with self._lock:
            return self._counters.get(name, 0)

    def get_latency_sum(self, name: str) -> float:
        """Return accumulated latency sum in milliseconds."""
        with self._lock:
            return self._latency_sums.get(name, 0.0)

    def get_latency_count(self, name: str) -> int:
        """Return number of latency observations."""
        with self._lock:
            return self._latency_counts.get(name, 0)

    def snapshot(self) -> dict[str, Any]:
        """Return a consistent point-in-time copy of all metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "latency_sums": dict(self._latency_sums),
                "latency_counts": dict(self._latency_counts),
            }
