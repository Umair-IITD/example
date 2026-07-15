"""
case_engine/investigation/collector/execution.py

Sprint 2.42: Execution record types for the Evidence Collector Engine.

Defines:
  StepStatus           — lifecycle status of a single step execution
  StepExecutionRecord  — complete record of one step execution (all attempts)
  PlanExecutionSummary — aggregate of all step records for one plan run

Dependency direction:
  execution.py → collector/contracts.py (CollectionResult)
  execution.py → stdlib only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from case_engine.investigation.collector.contracts import CollectionResult


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class StepStatus(str, Enum):
    """Lifecycle status of a single PlanningStep execution."""
    PENDING = "PENDING"  # Not yet started
    RUNNING = "RUNNING"  # Currently executing
    SUCCESS = "SUCCESS"  # Completed successfully
    FAILED  = "FAILED"   # All attempts failed
    SKIPPED = "SKIPPED"  # Precondition not met or step was optional and bypassed
    TIMEOUT = "TIMEOUT"  # Step exceeded its time budget


@dataclass
class StepExecutionRecord:
    """
    Complete record of one PlanningStep execution, including all retry attempts.

    Fields:
        step_id:        PlanningStep.step_id
        status:         Final lifecycle status
        attempts:       Total number of execution attempts made
        results:        CollectionResult from each attempt (in order)
        final_result:   The result from the last attempt (success or last failure)
        started_at:     ISO timestamp when first attempt began
        completed_at:   ISO timestamp when execution finished (any outcome)
        duration_ms:    Total wall-clock time including all retries
        skipped_reason: Human-readable reason if status=SKIPPED
        step_order:     step.order value (for deterministic sorting in summaries)
    """
    step_id:        str
    status:         StepStatus
    attempts:       int
    results:        list[CollectionResult] = field(default_factory=list)
    final_result:   CollectionResult | None = None
    started_at:     str = field(default_factory=_now_iso)
    completed_at:   str = field(default_factory=_now_iso)
    duration_ms:    int = 0
    skipped_reason: str | None = None
    step_order:     int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == StepStatus.SUCCESS

    @property
    def failed(self) -> bool:
        return self.status == StepStatus.FAILED

    @property
    def skipped(self) -> bool:
        return self.status == StepStatus.SKIPPED

    @property
    def timed_out(self) -> bool:
        return self.status == StepStatus.TIMEOUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":        self.step_id,
            "status":         self.status.value,
            "attempts":       self.attempts,
            "duration_ms":    self.duration_ms,
            "started_at":     self.started_at,
            "completed_at":   self.completed_at,
            "skipped_reason": self.skipped_reason,
            "final_success":  self.succeeded,
            "result_count":   len(self.results),
        }


@dataclass
class PlanExecutionSummary:
    """
    Aggregate outcome of executing all steps in one InvestigationPlan.

    Fields:
        plan_id:           InvestigationPlan.plan_id
        case_id:           Case identifier
        total_steps:       Total steps executed (or skipped)
        successful_steps:  Steps that completed with SUCCESS
        failed_steps:      Steps that ended with FAILED
        skipped_steps:     Steps that were SKIPPED
        timeout_steps:     Steps that timed out
        total_duration_ms: Sum of all step durations
        records:           All StepExecutionRecords, sorted by step_order
        completed_at:      ISO timestamp when the plan finished
    """
    plan_id:           str
    case_id:           str
    total_steps:       int
    successful_steps:  int
    failed_steps:      int
    skipped_steps:     int
    timeout_steps:     int
    total_duration_ms: int
    records:           list[StepExecutionRecord] = field(default_factory=list)
    completed_at:      str = field(default_factory=_now_iso)

    @property
    def is_fully_successful(self) -> bool:
        return self.failed_steps == 0 and self.timeout_steps == 0

    @property
    def any_succeeded(self) -> bool:
        return self.successful_steps > 0

    @property
    def completion_ratio(self) -> float:
        if self.total_steps == 0:
            return 1.0
        return self.successful_steps / self.total_steps

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id":             self.plan_id,
            "case_id":             self.case_id,
            "total_steps":         self.total_steps,
            "successful_steps":    self.successful_steps,
            "failed_steps":        self.failed_steps,
            "skipped_steps":       self.skipped_steps,
            "timeout_steps":       self.timeout_steps,
            "total_duration_ms":   self.total_duration_ms,
            "completion_ratio":    self.completion_ratio,
            "is_fully_successful": self.is_fully_successful,
            "completed_at":        self.completed_at,
            "records":             [r.to_dict() for r in self.records],
        }
