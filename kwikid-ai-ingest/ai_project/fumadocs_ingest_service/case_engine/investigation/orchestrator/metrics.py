"""
case_engine/investigation/orchestrator/metrics.py

Sprint 2.46: Orchestration metrics.

OrchestratorMetrics      — per-session metrics (stage durations, failures)
OrchestratorGlobalMetrics — process-lifetime counters (thread-safe)

Dependency direction: stdlib only.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrchestratorMetrics:
    """
    Per-session metrics for one investigation pipeline run.

    Populated by PipelineOrchestrator as stages complete.
    """
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    stage_failures:     dict[str, int] = field(default_factory=dict)
    pipeline_duration_ms: int          = 0
    stages_completed:     int          = 0
    stages_failed:        int          = 0

    def record_stage(
        self,
        stage: str,
        duration_ms: int,
        *,
        success: bool,
    ) -> None:
        """Record a stage execution outcome."""
        self.stage_durations_ms[stage] = duration_ms
        if success:
            self.stages_completed += 1
        else:
            self.stages_failed += 1
            self.stage_failures[stage] = self.stage_failures.get(stage, 0) + 1

    def set_pipeline_duration(self, ms: int) -> None:
        self.pipeline_duration_ms = ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_durations_ms":   self.stage_durations_ms,
            "stage_failures":       self.stage_failures,
            "pipeline_duration_ms": self.pipeline_duration_ms,
            "stages_completed":     self.stages_completed,
            "stages_failed":        self.stages_failed,
        }


class OrchestratorGlobalMetrics:
    """
    Thread-safe process-lifetime counters for the InvestigationOrchestrator.

    Shared across all orchestrator instances. Reset only for testing.

    Tracks:
      - completed_investigations
      - failed_investigations
      - cancelled_investigations
      - stage_failure_totals (per-stage counts)
    """

    def __init__(self) -> None:
        self._lock:         threading.Lock         = threading.Lock()
        self._completed:    int                    = 0
        self._failed:       int                    = 0
        self._cancelled:    int                    = 0
        self._stage_totals: dict[str, int]         = {}

    def record_completed(self) -> None:
        with self._lock:
            self._completed += 1

    def record_failed(self) -> None:
        with self._lock:
            self._failed += 1

    def record_cancelled(self) -> None:
        with self._lock:
            self._cancelled += 1

    def record_stage_failure(self, stage: str) -> None:
        with self._lock:
            self._stage_totals[stage] = self._stage_totals.get(stage, 0) + 1

    @property
    def completed(self) -> int:
        with self._lock:
            return self._completed

    @property
    def failed(self) -> int:
        with self._lock:
            return self._failed

    @property
    def cancelled(self) -> int:
        with self._lock:
            return self._cancelled

    @property
    def stage_failure_totals(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stage_totals)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "completed_investigations":  self._completed,
                "failed_investigations":     self._failed,
                "cancelled_investigations":  self._cancelled,
                "stage_failure_totals":      dict(self._stage_totals),
            }

    def reset(self) -> None:
        """Reset all counters. For test use only."""
        with self._lock:
            self._completed    = 0
            self._failed       = 0
            self._cancelled    = 0
            self._stage_totals = {}
