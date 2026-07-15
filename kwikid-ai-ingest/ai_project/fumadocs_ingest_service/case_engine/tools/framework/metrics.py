"""
case_engine/tools/framework/metrics.py

Sprint 2.45: Thread-safe aggregate metrics for the Tool Framework.

Tracks: executions, failures, timeouts, retry count, latency,
availability, success rate. Internal only — not Prometheus integration.

Dependency direction:
  metrics.py → stdlib only
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolFrameworkMetrics:
    """
    Thread-safe aggregate metrics for the entire Tool Framework session.

    One instance is shared by ProductionToolExecutor across all executions.
    All mutation methods acquire self._lock before modifying state.
    """

    def __init__(self) -> None:
        self._lock:                threading.Lock = threading.Lock()
        self._total_executions:    int   = 0
        self._total_successes:     int   = 0
        self._total_failures:      int   = 0
        self._total_timeouts:      int   = 0
        self._total_retries:       int   = 0
        self._total_duration_ms:   int   = 0
        self._per_tool:            dict[str, dict[str, int]] = {}
        self._capability_hits:     dict[str, int] = {}  # evidence_kind → count

    # ── Recording ──────────────────────────────────────────────────────────────

    def record_execution(
        self,
        tool_name:   str,
        duration_ms: int,
        *,
        success:     bool,
        timed_out:   bool = False,
        retries:     int  = 0,
        evidence_kind: str | None = None,
    ) -> None:
        """Record one completed tool execution."""
        with self._lock:
            self._total_executions  += 1
            self._total_duration_ms += duration_ms
            self._total_retries     += retries
            if success:
                self._total_successes += 1
            else:
                self._total_failures += 1
            if timed_out:
                self._total_timeouts += 1

            t = self._per_tool.setdefault(tool_name, {
                "executions": 0, "successes": 0, "failures": 0,
                "timeouts": 0, "retries": 0, "total_ms": 0,
            })
            t["executions"] += 1
            t["total_ms"]   += duration_ms
            t["retries"]    += retries
            if success:
                t["successes"] += 1
            else:
                t["failures"] += 1
            if timed_out:
                t["timeouts"] += 1

            if evidence_kind:
                self._capability_hits[evidence_kind] = (
                    self._capability_hits.get(evidence_kind, 0) + 1
                )

    # ── Read ───────────────────────────────────────────────────────────────────

    @property
    def total_executions(self) -> int:
        with self._lock:
            return self._total_executions

    @property
    def total_successes(self) -> int:
        with self._lock:
            return self._total_successes

    @property
    def total_failures(self) -> int:
        with self._lock:
            return self._total_failures

    @property
    def total_timeouts(self) -> int:
        with self._lock:
            return self._total_timeouts

    @property
    def total_retries(self) -> int:
        with self._lock:
            return self._total_retries

    @property
    def average_duration_ms(self) -> int:
        with self._lock:
            if self._total_executions == 0:
                return 0
            return self._total_duration_ms // self._total_executions

    @property
    def success_rate(self) -> float:
        with self._lock:
            if self._total_executions == 0:
                return 0.0
            return self._total_successes / self._total_executions

    def per_tool_stats(self, tool_name: str) -> dict[str, int]:
        """Return execution stats for a specific tool (copy)."""
        with self._lock:
            return dict(self._per_tool.get(tool_name, {}))

    def capability_hit_counts(self) -> dict[str, int]:
        """Return evidence_kind → execution count map."""
        with self._lock:
            return dict(self._capability_hits)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_executions":  self._total_executions,
                "total_successes":   self._total_successes,
                "total_failures":    self._total_failures,
                "total_timeouts":    self._total_timeouts,
                "total_retries":     self._total_retries,
                "average_duration_ms": (
                    self._total_duration_ms // self._total_executions
                    if self._total_executions else 0
                ),
                "success_rate":      (
                    self._total_successes / self._total_executions
                    if self._total_executions else 0.0
                ),
                "per_tool":          dict(self._per_tool),
                "capability_hits":   dict(self._capability_hits),
            }

    def reset(self) -> None:
        """Clear all metrics. Intended for test isolation only."""
        with self._lock:
            self._total_executions  = 0
            self._total_successes   = 0
            self._total_failures    = 0
            self._total_timeouts    = 0
            self._total_retries     = 0
            self._total_duration_ms = 0
            self._per_tool.clear()
            self._capability_hits.clear()
