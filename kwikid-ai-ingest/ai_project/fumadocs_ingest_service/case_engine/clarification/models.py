"""
case_engine/clarification/models.py

Sprint 2.25: Domain models for the Clarification Layer.

flow_diagram path:
    Missing Slots --> CLARIFICATION_ENGINE --> CLARIFY step --> Wait for Customer Response
    --> Resume Workflow

Design:
  - All models are frozen dataclasses -- immutable after construction.
  - JSON-serializable via to_dict() / from_dict().
  - Never raises.
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


# ── Status enum ───────────────────────────────────────────────────────────────

class ClarificationStatus(str, Enum):
    """
    Result status of a ClarificationEngine run.

    READY               : All required slots are present — workflow can proceed.
    NEEDS_CLARIFICATION : At least one required slot is missing — ask the customer.
    ESCALATE            : Max clarification attempts exceeded — escalate to human.
    """
    READY               = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    ESCALATE            = "ESCALATE"


# ── Missing slot descriptor ───────────────────────────────────────────────────

@dataclass(frozen=True)
class MissingSlotInfo:
    """
    Describes a single required slot that is missing from the workflow context.

    slot_name    : name of the slot (e.g., "session_id")
    prompt_text  : customer-facing question to ask
    attempt_count: how many times this slot has already been asked
    max_attempts : maximum attempts before escalation
    valid_values : optional tuple of valid enum values (shown as options)
    """
    slot_name:    str
    prompt_text:  str
    attempt_count: int = 0
    max_attempts:  int = 2
    valid_values:  tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "slot_name":    self.slot_name,
            "prompt_text":  self.prompt_text,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
        }
        if self.valid_values is not None:
            d["valid_values"] = list(self.valid_values)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MissingSlotInfo":
        vv = d.get("valid_values")
        return cls(
            slot_name=str(d.get("slot_name", "")),
            prompt_text=str(d.get("prompt_text", "")),
            attempt_count=int(d.get("attempt_count", 0)),
            max_attempts=int(d.get("max_attempts", 2)),
            valid_values=tuple(vv) if vv is not None else None,
        )


# ── Clarification result ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClarificationResult:
    """
    Output of one ClarificationEngine.clarify() invocation.

    result_id             : unique ID for this clarification run
    status                : READY | NEEDS_CLARIFICATION | ESCALATE
    missing_slots         : names of still-missing required slots (empty when READY)
    next_question         : structured question for the first missing slot (None if READY/ESCALATE)
    clarification_message : human-readable message for the customer or support log
    ready_to_continue     : True only when status == READY
    created_at            : ISO-8601 timestamp
    """
    result_id:             str
    status:                ClarificationStatus
    missing_slots:         tuple[str, ...]
    next_question:         MissingSlotInfo | None
    clarification_message: str
    ready_to_continue:     bool
    created_at:            str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":             self.result_id,
            "status":                self.status.value,
            "missing_slots":         list(self.missing_slots),
            "next_question":         self.next_question.to_dict() if self.next_question else None,
            "clarification_message": self.clarification_message,
            "ready_to_continue":     self.ready_to_continue,
            "created_at":            self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClarificationResult":
        nq_raw = d.get("next_question")
        return cls(
            result_id=d.get("result_id") or _new_id(),
            status=ClarificationStatus(
                d.get("status", ClarificationStatus.READY.value)
            ),
            missing_slots=tuple(d.get("missing_slots") or []),
            next_question=MissingSlotInfo.from_dict(nq_raw) if nq_raw else None,
            clarification_message=str(d.get("clarification_message") or ""),
            ready_to_continue=bool(d.get("ready_to_continue", False)),
            created_at=d.get("created_at") or _now_iso(),
        )
