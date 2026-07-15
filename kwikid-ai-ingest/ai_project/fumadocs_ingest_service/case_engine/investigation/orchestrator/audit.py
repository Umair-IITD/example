"""
case_engine/investigation/orchestrator/audit.py

Sprint 2.46: Deterministic audit timeline for the Investigation Orchestrator.

Every pipeline stage produces an AuditStageRecord.

PII contract:
  - input_summary NEVER stores actual slot values, ticket content, or user data.
  - output_summary stores structural info only (IDs, counts, status).
  - error_message is truncated to 500 chars.

Dependency direction: stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class AuditStageRecord:
    """
    Mutable audit record for one pipeline stage.

    Created by OrchestratorAudit.start_stage().
    Mutated in-place as the stage progresses (mark_complete, mark_failed, etc.).
    """
    stage:         str
    started_at:    str
    completed_at:  str | None             = None
    failed_at:     str | None             = None
    duration_ms:   int                    = 0
    outcome:       str                    = "PENDING"
    input_summary: dict[str, Any]         = field(default_factory=dict)
    output_summary: dict[str, Any]        = field(default_factory=dict)
    error_code:    str | None             = None
    error_message: str | None             = None

    def mark_complete(
        self,
        completed_at: str,
        duration_ms: int,
        output_summary: dict[str, Any],
    ) -> None:
        self.completed_at   = completed_at
        self.duration_ms    = duration_ms
        self.outcome        = "SUCCESS"
        self.output_summary = output_summary

    def mark_failed(
        self,
        failed_at: str,
        duration_ms: int,
        error_code: str,
        error_message: str,
    ) -> None:
        self.failed_at     = failed_at
        self.duration_ms   = duration_ms
        self.outcome       = "FAILURE"
        self.error_code    = error_code
        self.error_message = error_message[:500]

    def mark_cancelled(self, cancelled_at: str, duration_ms: int) -> None:
        self.completed_at = cancelled_at
        self.duration_ms  = duration_ms
        self.outcome      = "CANCELLED"

    def mark_skipped(self) -> None:
        self.completed_at = _now_iso()
        self.outcome      = "SKIPPED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage":          self.stage,
            "started_at":     self.started_at,
            "completed_at":   self.completed_at,
            "failed_at":      self.failed_at,
            "duration_ms":    self.duration_ms,
            "outcome":        self.outcome,
            "input_summary":  self.input_summary,
            "output_summary": self.output_summary,
            "error_code":     self.error_code,
            "error_message":  self.error_message,
        }


@dataclass
class OrchestratorAudit:
    """
    Deterministic audit timeline for one investigation pipeline run.

    PII contract: stage records store key names and structural summaries only.
    All input_summary dicts are caller-constructed with no raw values.
    """
    session_id: str
    case_id:    str
    started_at: str
    timeline:   list[AuditStageRecord] = field(default_factory=list)

    def start_stage(
        self,
        stage: str,
        input_summary: dict[str, Any] | None = None,
    ) -> AuditStageRecord:
        """
        Create, append, and return a new stage record.

        The returned record must be mutated in-place to record the outcome.
        """
        record = AuditStageRecord(
            stage=stage,
            started_at=_now_iso(),
            input_summary=input_summary or {},
        )
        self.timeline.append(record)
        return record

    def stage_count(self) -> int:
        return len(self.timeline)

    def successful_stages(self) -> list[str]:
        return [r.stage for r in self.timeline if r.outcome == "SUCCESS"]

    def failed_stages(self) -> list[str]:
        return [r.stage for r in self.timeline if r.outcome == "FAILURE"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id":       self.session_id,
            "case_id":          self.case_id,
            "started_at":       self.started_at,
            "stages":           [r.to_dict() for r in self.timeline],
            "stage_count":      len(self.timeline),
            "successful_stages": self.successful_stages(),
            "failed_stages":    self.failed_stages(),
        }
