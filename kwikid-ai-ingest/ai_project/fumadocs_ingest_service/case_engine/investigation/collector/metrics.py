"""
case_engine/investigation/collector/metrics.py

Sprint 2.42: Collection metrics for the Evidence Collector Engine.

Defines:
  StepMetric        — per-step metric snapshot (immutable)
  CollectionMetrics — aggregate metrics for one plan execution

CollectionMetrics is written to InvestigationContext after every
collect() call so downstream components can observe collection performance.

Dependency direction:
  metrics.py → collector/execution.py (StepExecutionRecord, PlanExecutionSummary, StepStatus)
  metrics.py → stdlib only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from case_engine.investigation.collector.execution import (
    PlanExecutionSummary,
    StepExecutionRecord,
    StepStatus,
)


@dataclass(frozen=True)
class StepMetric:
    """
    Metric snapshot for a single PlanningStep execution.

    Immutable — produced once per step and never modified.
    """
    step_id:       str
    status:        str   # StepStatus.value
    attempts:      int
    duration_ms:   int
    provider_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":       self.step_id,
            "status":        self.status,
            "attempts":      self.attempts,
            "duration_ms":   self.duration_ms,
            "provider_name": self.provider_name,
        }


@dataclass
class CollectionMetrics:
    """
    Aggregate metrics for one Evidence Collector plan execution.

    Produced by the EvidenceCollector after collect() completes and
    written to InvestigationContext.collection_metrics.

    Fields:
        plan_id:           InvestigationPlan.plan_id
        case_id:           Case identifier
        total_duration_ms: Sum of all step durations
        successful_steps:  Count of SUCCESS steps
        failed_steps:      Count of FAILED steps
        skipped_steps:     Count of SKIPPED steps
        timeout_steps:     Count of TIMEOUT steps
        total_attempts:    Sum of all attempts across all steps
        total_retries:     total_attempts minus total non-skipped steps
        step_metrics:      Per-step metric detail
    """
    plan_id:           str
    case_id:           str
    total_duration_ms: int
    successful_steps:  int
    failed_steps:      int
    skipped_steps:     int
    timeout_steps:     int
    total_attempts:    int
    total_retries:     int
    step_metrics:      list[StepMetric] = field(default_factory=list)

    @classmethod
    def from_summary(cls, summary: PlanExecutionSummary) -> "CollectionMetrics":
        """Build CollectionMetrics from a PlanExecutionSummary."""
        step_metrics = [_record_to_metric(r) for r in summary.records]
        total_attempts = sum(r.attempts for r in summary.records)
        non_skipped = sum(1 for r in summary.records if r.status != StepStatus.SKIPPED)
        return cls(
            plan_id=summary.plan_id,
            case_id=summary.case_id,
            total_duration_ms=summary.total_duration_ms,
            successful_steps=summary.successful_steps,
            failed_steps=summary.failed_steps,
            skipped_steps=summary.skipped_steps,
            timeout_steps=summary.timeout_steps,
            total_attempts=total_attempts,
            total_retries=max(0, total_attempts - non_skipped),
            step_metrics=step_metrics,
        )

    @property
    def total_steps(self) -> int:
        return len(self.step_metrics)

    @property
    def completion_ratio(self) -> float:
        if self.total_steps == 0:
            return 1.0
        return self.successful_steps / self.total_steps

    @property
    def retry_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.total_retries / self.total_attempts

    def slowest_step(self) -> StepMetric | None:
        if not self.step_metrics:
            return None
        return max(self.step_metrics, key=lambda m: m.duration_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id":           self.plan_id,
            "case_id":           self.case_id,
            "total_duration_ms": self.total_duration_ms,
            "successful_steps":  self.successful_steps,
            "failed_steps":      self.failed_steps,
            "skipped_steps":     self.skipped_steps,
            "timeout_steps":     self.timeout_steps,
            "total_attempts":    self.total_attempts,
            "total_retries":     self.total_retries,
            "total_steps":       self.total_steps,
            "completion_ratio":  self.completion_ratio,
            "retry_rate":        self.retry_rate,
            "step_metrics":      [m.to_dict() for m in self.step_metrics],
        }


def _record_to_metric(record: StepExecutionRecord) -> StepMetric:
    provider_name = ""
    if record.final_result is not None:
        provider_name = record.final_result.provider_name
    return StepMetric(
        step_id=record.step_id,
        status=record.status.value,
        attempts=record.attempts,
        duration_ms=record.duration_ms,
        provider_name=provider_name,
    )
