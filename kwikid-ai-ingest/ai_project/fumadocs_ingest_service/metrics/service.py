"""
metrics/service.py

Sprint 2.10: MetricsService — application-level metrics facade.

All metric recording goes through this service. Callers never touch
MetricsCollector directly — the service provides a domain-aware API.

Fire-and-forget: record_* methods never raise. Failures are swallowed
so that metrics infrastructure cannot impact business operations.

Prometheus text output:
  prometheus_text() returns the full metrics output in the Prometheus
  exposition format (text/plain; version=0.0.4). Suitable for scraping
  by Prometheus or any compatible collector.
"""
from __future__ import annotations

import logging
from typing import Any

from metrics.collector import (
    MetricsCollector,
    COUNTER_ACTIONS_APPROVED,
    COUNTER_ACTIONS_CREATED,
    COUNTER_ACTIONS_DEAD_LETTERED,
    COUNTER_ACTIONS_EXECUTED,
    COUNTER_ACTIONS_EXPIRED,
    COUNTER_ACTIONS_FAILED,
    COUNTER_ACTIONS_REJECTED,
    COUNTER_ACTIONS_ROLLED_BACK,
    COUNTER_ACTIONS_ROLLBACK_FAILED,
    COUNTER_AUTH_FAILURE,
    COUNTER_AUTH_SUCCESS,
    COUNTER_AUDIT_EVENTS_READ,
    COUNTER_AUDIT_EVENTS_WRITTEN,
    COUNTER_WATCHDOG_EXPIRED,
    COUNTER_WATCHDOG_RUNS,
    COUNTER_WORKER_EXECUTION,
    COUNTER_WORKER_FAILURE,
    COUNTER_WORKER_ROLLBACK,
    LATENCY_APPROVAL,
    LATENCY_EXECUTION,
    LATENCY_ROLLBACK,
)

LOGGER = logging.getLogger(__name__)

_COUNTER_HELP: dict[str, str] = {
    COUNTER_ACTIONS_CREATED:         "Total actions proposed by the AI system",
    COUNTER_ACTIONS_APPROVED:        "Total actions approved by human reviewers",
    COUNTER_ACTIONS_REJECTED:        "Total actions rejected by human reviewers",
    COUNTER_ACTIONS_EXECUTED:        "Total actions successfully executed",
    COUNTER_ACTIONS_FAILED:          "Total actions that failed execution (all severities)",
    COUNTER_ACTIONS_EXPIRED:         "Total actions expired by the SLA watchdog",
    COUNTER_ACTIONS_ROLLED_BACK:     "Total actions successfully rolled back",
    COUNTER_ACTIONS_ROLLBACK_FAILED: "Total rollback attempts that failed",
    COUNTER_ACTIONS_DEAD_LETTERED:   "Total actions sent to dead-letter (all retries exhausted)",
    COUNTER_AUTH_SUCCESS:            "Total successful API key authentications",
    COUNTER_AUTH_FAILURE:            "Total failed API key authentication attempts",
    COUNTER_WATCHDOG_RUNS:           "Total SLA watchdog run invocations",
    COUNTER_WATCHDOG_EXPIRED:        "Total actions expired by the SLA watchdog per run",
    COUNTER_AUDIT_EVENTS_WRITTEN:    "Total audit events successfully persisted",
    COUNTER_AUDIT_EVENTS_READ:       "Total audit events read via API",
    COUNTER_WORKER_EXECUTION:        "Total worker execution attempts",
    COUNTER_WORKER_FAILURE:          "Total worker execution failures",
    COUNTER_WORKER_ROLLBACK:         "Total worker rollback attempts",
}

_LATENCY_HELP: dict[str, str] = {
    LATENCY_EXECUTION: "Execution latency from executor.execute() in milliseconds",
    LATENCY_ROLLBACK:  "Rollback latency from executor.rollback() in milliseconds",
    LATENCY_APPROVAL:  "Approval latency (proposed_at → approved_at) in milliseconds",
}


class MetricsService:
    """
    Application-level metrics facade.

    All methods are safe to call without checking whether metrics are enabled —
    if the collector is None, all calls are no-ops. The caller (runtime/gateway)
    uses the same fire-and-forget pattern as the audit service.
    """

    def __init__(self, collector: MetricsCollector) -> None:
        self._col = collector

    # ── Counter recordings ─────────────────────────────────────────────────────

    def record_action_created(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_CREATED)

    def record_action_approved(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_APPROVED)

    def record_action_rejected(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_REJECTED)

    def record_action_expired(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_EXPIRED)

    def record_action_executed(self, latency_ms: float = 0.0) -> None:
        self._safe_increment(COUNTER_ACTIONS_EXECUTED)
        if latency_ms > 0:
            self._safe_latency(LATENCY_EXECUTION, latency_ms)

    def record_action_failed(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_FAILED)

    def record_action_rolled_back(self, latency_ms: float = 0.0) -> None:
        self._safe_increment(COUNTER_ACTIONS_ROLLED_BACK)
        if latency_ms > 0:
            self._safe_latency(LATENCY_ROLLBACK, latency_ms)

    def record_action_rollback_failed(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_ROLLBACK_FAILED)

    def record_action_dead_lettered(self) -> None:
        self._safe_increment(COUNTER_ACTIONS_DEAD_LETTERED)

    # ── Auth counters ──────────────────────────────────────────────────────────

    def record_auth_success(self) -> None:
        self._safe_increment(COUNTER_AUTH_SUCCESS)

    def record_auth_failure(self) -> None:
        self._safe_increment(COUNTER_AUTH_FAILURE)

    # ── Watchdog counters ──────────────────────────────────────────────────────

    def record_watchdog_run(self) -> None:
        self._safe_increment(COUNTER_WATCHDOG_RUNS)

    def record_watchdog_expired(self) -> None:
        self._safe_increment(COUNTER_WATCHDOG_EXPIRED)

    # ── Audit counters ─────────────────────────────────────────────────────────

    def record_audit_event_written(self) -> None:
        self._safe_increment(COUNTER_AUDIT_EVENTS_WRITTEN)

    def record_audit_event_read(self) -> None:
        self._safe_increment(COUNTER_AUDIT_EVENTS_READ)

    # ── Worker counters ────────────────────────────────────────────────────────

    def record_worker_execution(self) -> None:
        self._safe_increment(COUNTER_WORKER_EXECUTION)

    def record_worker_failure(self) -> None:
        self._safe_increment(COUNTER_WORKER_FAILURE)

    def record_worker_rollback(self) -> None:
        self._safe_increment(COUNTER_WORKER_ROLLBACK)

    # ── Output ─────────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a raw snapshot from the underlying collector."""
        return self._col.snapshot()

    def prometheus_text(self) -> str:
        """
        Return all metrics in Prometheus text exposition format.

        Format: text/plain; version=0.0.4
        One metric family per counter or latency summary.
        """
        snap = self._col.snapshot()
        lines: list[str] = []

        for name, help_text in _COUNTER_HELP.items():
            value = snap["counters"].get(name, 0)
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        for name, help_text in _LATENCY_HELP.items():
            total = snap["latency_sums"].get(name, 0.0)
            count = snap["latency_counts"].get(name, 0)
            lines.append(f"# HELP {name}_sum {help_text} (sum)")
            lines.append(f"# TYPE {name}_sum gauge")
            lines.append(f"{name}_sum {total:.3f}")
            lines.append(f"# HELP {name}_count {help_text} (count)")
            lines.append(f"# TYPE {name}_count counter")
            lines.append(f"{name}_count {count}")

        return "\n".join(lines) + "\n"

    # ── Internal ───────────────────────────────────────────────────────────────

    def _safe_increment(self, name: str) -> None:
        try:
            self._col.increment(name)
        except Exception as exc:
            LOGGER.error("metrics.service: increment failed name=%s error=%s", name, exc)

    def _safe_latency(self, name: str, latency_ms: float) -> None:
        try:
            self._col.record_latency(name, latency_ms)
        except Exception as exc:
            LOGGER.error("metrics.service: latency failed name=%s error=%s", name, exc)
