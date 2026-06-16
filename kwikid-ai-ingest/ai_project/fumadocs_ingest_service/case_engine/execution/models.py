"""
case_engine/execution/models.py

Sprint 2.23: Execution Layer domain models.

Flow (flow_diagram.mermaid):
    ACTION_GATEWAY (Sprint 2.22)
    --> EXECUTE         <- this module
    --> VERIFY
    --> DECISION3 { Outcome Successful? }
      |No  --> RECOVERY (RETRY / ROLLBACK / DEADLETTER)
      |Yes --> RESOLUTION

All models: frozen dataclasses, to_dict/from_dict, JSON-serializable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Execution Status ──────────────────────────────────────────────────────────

class ExecutionStatus(str, Enum):
    """
    Lifecycle state of an execution attempt.

    PENDING            -- created, not yet started
    EXECUTING          -- adapter call in progress
    SUCCESS            -- adapter reported success
    FAILED             -- adapter reported failure
    PARTIAL_SUCCESS    -- some sub-actions succeeded, others failed
    ROLLBACK_COMPLETED -- failed action was rolled back successfully
    RETRY_SCHEDULED    -- recovery engine queued a retry attempt
    """
    PENDING            = "PENDING"
    EXECUTING          = "EXECUTING"
    SUCCESS            = "SUCCESS"
    FAILED             = "FAILED"
    PARTIAL_SUCCESS    = "PARTIAL_SUCCESS"
    ROLLBACK_COMPLETED = "ROLLBACK_COMPLETED"
    RETRY_SCHEDULED    = "RETRY_SCHEDULED"


# ── Verification Status ───────────────────────────────────────────────────────

class VerificationStatus(str, Enum):
    """
    Result of the VERIFY node.

    VERIFIED_SUCCESS -- positive confirmation that the action succeeded
    VERIFIED_FAILURE -- positive confirmation that the action failed
    UNCERTAIN        -- could not confirm success or failure (treated as failure)
    """
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    UNCERTAIN        = "UNCERTAIN"


# ── Recovery Strategy ─────────────────────────────────────────────────────────

class RecoveryStrategy(str, Enum):
    """
    Strategy selected by the RecoveryEngine.

    RETRY       -- attempt the action again
    ROLLBACK    -- reverse the partial changes made by the failed action
    ESCALATE    -- hand off to a human operator for manual resolution
    DEAD_LETTER -- mark as unrecoverable; queue for offline investigation
    """
    RETRY       = "RETRY"
    ROLLBACK    = "ROLLBACK"
    ESCALATE    = "ESCALATE"
    DEAD_LETTER = "DEAD_LETTER"


# ── Recovery Status ───────────────────────────────────────────────────────────

class RecoveryStatus(str, Enum):
    """Outcome state of a recovery operation."""
    RECOVERY_PENDING   = "RECOVERY_PENDING"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    RECOVERY_FAILED    = "RECOVERY_FAILED"
    ESCALATED          = "ESCALATED"


# ── Resolution Status ─────────────────────────────────────────────────────────

class ResolutionStatus(str, Enum):
    """
    Final disposition of the case after execution + verification.

    RESOLVED           -- issue confirmed resolved; ticket can be closed
    PARTIALLY_RESOLVED -- some aspects resolved; follow-up may be needed
    UNRESOLVED         -- action failed or unverified; manual intervention required
    ESCALATED          -- escalated to L2 / engineering; outside L1 scope
    """
    RESOLVED           = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    UNRESOLVED         = "UNRESOLVED"
    ESCALATED          = "ESCALATED"


# ── ExecutionResult ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionResult:
    """Output of a single execution attempt via the ActionExecutor."""
    result_id:      str
    adapter_name:   str
    action_type:    str
    action_params:  dict[str, Any]
    status:         ExecutionStatus
    success:        bool
    response_data:  dict[str, Any]
    error_code:     str | None
    error_message:  str | None
    executed_at:    str
    duration_ms:    int

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":     self.result_id,
            "adapter_name":  self.adapter_name,
            "action_type":   self.action_type,
            "action_params": self.action_params,
            "status":        self.status.value,
            "success":       self.success,
            "response_data": self.response_data,
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "executed_at":   self.executed_at,
            "duration_ms":   self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecutionResult":
        return cls(
            result_id=d.get("result_id", _new_id()),
            adapter_name=d.get("adapter_name", "unknown"),
            action_type=d.get("action_type", ""),
            action_params=d.get("action_params", {}),
            status=ExecutionStatus(d.get("status", ExecutionStatus.FAILED.value)),
            success=d.get("success", False),
            response_data=d.get("response_data", {}),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            executed_at=d.get("executed_at", _now_iso()),
            duration_ms=d.get("duration_ms", 0),
        )


# ── ExecutionAttempt ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionAttempt:
    """Records one attempt at executing an action, including retry attempts."""
    attempt_id:        str
    attempt_number:    int
    execution_result:  ExecutionResult
    recovery_strategy: RecoveryStrategy | None
    attempted_at:      str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id":        self.attempt_id,
            "attempt_number":    self.attempt_number,
            "execution_result":  self.execution_result.to_dict(),
            "recovery_strategy": self.recovery_strategy.value if self.recovery_strategy else None,
            "attempted_at":      self.attempted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecutionAttempt":
        rs_raw = d.get("recovery_strategy")
        return cls(
            attempt_id=d.get("attempt_id", _new_id()),
            attempt_number=d.get("attempt_number", 1),
            execution_result=ExecutionResult.from_dict(d.get("execution_result") or {}),
            recovery_strategy=RecoveryStrategy(rs_raw) if rs_raw else None,
            attempted_at=d.get("attempted_at", _now_iso()),
        )


# ── ExecutionBundle ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionBundle:
    """
    Complete execution package for one workflow EXECUTE step.

    Aggregates all attempts and the final status.
    Stored as JSONB in WorkflowExecutionResult.execution_result.
    """
    bundle_id:        str
    case_id:          str
    action_type:      str
    action_namespace: str
    attempts:         tuple[ExecutionAttempt, ...]
    final_status:     ExecutionStatus
    total_attempts:   int
    created_at:       str
    completed_at:     str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id":        self.bundle_id,
            "case_id":          self.case_id,
            "action_type":      self.action_type,
            "action_namespace": self.action_namespace,
            "attempts":         [a.to_dict() for a in self.attempts],
            "final_status":     self.final_status.value,
            "total_attempts":   self.total_attempts,
            "created_at":       self.created_at,
            "completed_at":     self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExecutionBundle":
        return cls(
            bundle_id=d.get("bundle_id", _new_id()),
            case_id=d.get("case_id", ""),
            action_type=d.get("action_type", ""),
            action_namespace=d.get("action_namespace", ""),
            attempts=tuple(
                ExecutionAttempt.from_dict(a) for a in (d.get("attempts") or [])
            ),
            final_status=ExecutionStatus(d.get("final_status", ExecutionStatus.FAILED.value)),
            total_attempts=d.get("total_attempts", 0),
            created_at=d.get("created_at", _now_iso()),
            completed_at=d.get("completed_at"),
        )


# ── VerificationResult ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VerificationResult:
    """
    Output of the VERIFY node.

    Never assumes success -- if evidence is absent or ambiguous: UNCERTAIN.
    UNCERTAIN is treated identically to VERIFIED_FAILURE in the recovery path.
    """
    verification_id: str
    action_type:     str
    status:          VerificationStatus
    confirmed:       bool
    evidence:        dict[str, Any]
    failure_reason:  str | None
    verified_at:     str

    def is_success(self) -> bool:
        return self.status == VerificationStatus.VERIFIED_SUCCESS

    def requires_recovery(self) -> bool:
        return self.status in (VerificationStatus.VERIFIED_FAILURE, VerificationStatus.UNCERTAIN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "action_type":     self.action_type,
            "status":          self.status.value,
            "confirmed":       self.confirmed,
            "evidence":        self.evidence,
            "failure_reason":  self.failure_reason,
            "verified_at":     self.verified_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VerificationResult":
        return cls(
            verification_id=d.get("verification_id", _new_id()),
            action_type=d.get("action_type", ""),
            status=VerificationStatus(d.get("status", VerificationStatus.UNCERTAIN.value)),
            confirmed=d.get("confirmed", False),
            evidence=d.get("evidence") or {},
            failure_reason=d.get("failure_reason"),
            verified_at=d.get("verified_at", _now_iso()),
        )


# ── RecoveryResult ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecoveryResult:
    """Output of the RECOVERY node."""
    recovery_id:      str
    strategy_applied: RecoveryStrategy
    status:           RecoveryStatus
    max_retries:      int
    attempts_used:    int
    can_retry:        bool
    notes:            str
    recovered_at:     str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id":      self.recovery_id,
            "strategy_applied": self.strategy_applied.value,
            "status":           self.status.value,
            "max_retries":      self.max_retries,
            "attempts_used":    self.attempts_used,
            "can_retry":        self.can_retry,
            "notes":            self.notes,
            "recovered_at":     self.recovered_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RecoveryResult":
        return cls(
            recovery_id=d.get("recovery_id", _new_id()),
            strategy_applied=RecoveryStrategy(
                d.get("strategy_applied", RecoveryStrategy.ESCALATE.value)
            ),
            status=RecoveryStatus(d.get("status", RecoveryStatus.ESCALATED.value)),
            max_retries=d.get("max_retries", 3),
            attempts_used=d.get("attempts_used", 0),
            can_retry=d.get("can_retry", False),
            notes=d.get("notes", ""),
            recovered_at=d.get("recovered_at", _now_iso()),
        )


# ── ResolutionResult ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResolutionResult:
    """Final output of the RESOLUTION node."""
    resolution_id:   str
    action_type:     str
    status:          ResolutionStatus
    resolved:        bool
    resolution_note: str
    evidence_keys:   tuple[str, ...]
    resolved_at:     str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_id":   self.resolution_id,
            "action_type":     self.action_type,
            "status":          self.status.value,
            "resolved":        self.resolved,
            "resolution_note": self.resolution_note,
            "evidence_keys":   list(self.evidence_keys),
            "resolved_at":     self.resolved_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResolutionResult":
        return cls(
            resolution_id=d.get("resolution_id", _new_id()),
            action_type=d.get("action_type", ""),
            status=ResolutionStatus(d.get("status", ResolutionStatus.UNRESOLVED.value)),
            resolved=d.get("resolved", False),
            resolution_note=d.get("resolution_note", ""),
            evidence_keys=tuple(d.get("evidence_keys") or []),
            resolved_at=d.get("resolved_at", _now_iso()),
        )
