"""
case_engine/retry/models.py

Sprint 2.26: Retry Queue Framework — data models.

RetryStatus: lifecycle state machine for a retry job.
RetryJob:    immutable record of a queued retry operation.

Design:
  - RetryJob is frozen (immutable after creation). All mutations via with_update()
    return a new instance. dict action_params reference is frozen; contents are
    caller-owned and should not be mutated after creation.
  - No external infrastructure. In-memory only. Future-ready for Redis/SQS by
    swapping RetryRepository backend without changing this module.
  - Fully JSON-serializable via to_dict() / from_dict().
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RetryStatus(str, Enum):
    PENDING       = "PENDING"        # queued, waiting for next_retry_at
    RUNNING       = "RUNNING"        # currently being executed
    SUCCEEDED     = "SUCCEEDED"      # executed successfully — terminal
    FAILED        = "FAILED"         # this attempt failed; rescheduled for retry
    DEAD_LETTERED = "DEAD_LETTERED"  # max_attempts exceeded or manually cancelled — terminal


TERMINAL_RETRY_STATES: frozenset[RetryStatus] = frozenset({
    RetryStatus.SUCCEEDED,
    RetryStatus.DEAD_LETTERED,
})


@dataclass(frozen=True)
class RetryJob:
    """
    Immutable record of one queued retry operation.

    Produced by RetryScheduler.schedule() and consumed by RetryWorker.run_once().
    All state transitions produce new RetryJob instances via with_update().
    """
    job_id:        str
    action_id:     str
    case_id:       str
    action_type:   str
    action_params: dict[str, Any]   # frozen reference; dict contents immutable by convention
    attempt_count: int               # number of times executed (0 = never tried)
    max_attempts:  int               # move to DLQ when attempt_count >= max_attempts
    next_retry_at: datetime          # earliest time this job may next be executed
    created_at:    datetime
    status:        RetryStatus
    last_error:    str | None = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RETRY_STATES

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempt_count)

    @property
    def is_due(self) -> bool:
        return (
            self.status == RetryStatus.PENDING
            and self.next_retry_at <= datetime.now(tz=timezone.utc)
        )

    # ── Frozen mutation pattern ───────────────────────────────────────────────

    def with_update(self, **kwargs: Any) -> "RetryJob":
        """Return a new RetryJob with the given fields replaced."""
        return replace(self, **kwargs)

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id":        self.job_id,
            "action_id":     self.action_id,
            "case_id":       self.case_id,
            "action_type":   self.action_type,
            "action_params": dict(self.action_params),
            "attempt_count": self.attempt_count,
            "max_attempts":  self.max_attempts,
            "next_retry_at": self.next_retry_at.isoformat(),
            "created_at":    self.created_at.isoformat(),
            "status":        self.status.value,
            "last_error":    self.last_error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RetryJob":
        return cls(
            job_id=d["job_id"],
            action_id=d["action_id"],
            case_id=d["case_id"],
            action_type=d["action_type"],
            action_params=dict(d.get("action_params", {})),
            attempt_count=int(d.get("attempt_count", 0)),
            max_attempts=int(d.get("max_attempts", 3)),
            next_retry_at=datetime.fromisoformat(d["next_retry_at"]),
            created_at=datetime.fromisoformat(d["created_at"]),
            status=RetryStatus(d.get("status", RetryStatus.PENDING.value)),
            last_error=d.get("last_error"),
        )

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        action_id:     str,
        case_id:       str,
        action_type:   str,
        action_params: dict[str, Any],
        next_retry_at: datetime,
        max_attempts:  int = 3,
    ) -> "RetryJob":
        """Create a new PENDING RetryJob with a fresh UUID."""
        now = datetime.now(tz=timezone.utc)
        return cls(
            job_id=str(uuid.uuid4()),
            action_id=action_id,
            case_id=case_id,
            action_type=action_type,
            action_params=dict(action_params),
            attempt_count=0,
            max_attempts=max_attempts,
            next_retry_at=next_retry_at,
            created_at=now,
            status=RetryStatus.PENDING,
            last_error=None,
        )
