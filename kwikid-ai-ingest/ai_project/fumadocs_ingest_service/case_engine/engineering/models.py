"""
case_engine/engineering/models.py

Sprint 2.27.5: Engineering Escalation domain models.

Per blueprint flow_diagram.mermaid — L2 workflow:
  L2CHECK -->|Yes| ASANACREATE
  ASANACREATE --> ASANA
  ASANA <--> DEV
  ASANA --> FIXED --> FDUPDATE

Design:
  - Frozen dataclasses: immutable, JSON-serializable
  - EngineeringEscalationService uses a mock adapter in Sprint 2.27.5
  - Sprint 2.28: inject real Asana client via AsanaAdapter
  - Supports full lifecycle: create → in_progress → resolved / closed
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


# ── Enums ─────────────────────────────────────────────────────────────────────

class EngineeringPriority(str, Enum):
    """Engineering ticket priority — mirrors Asana task urgency levels."""
    CRITICAL = "CRITICAL"   # P0: service-impacting, needs immediate response
    HIGH     = "HIGH"       # P1: major feature broken, same-day response
    MEDIUM   = "MEDIUM"     # P2: significant issue, next-sprint response
    LOW      = "LOW"        # P3: minor / cosmetic issue


class EngineeringStatus(str, Enum):
    """Lifecycle status of an engineering escalation ticket."""
    PENDING     = "PENDING"       # ticket created, not yet picked up
    IN_PROGRESS = "IN_PROGRESS"   # engineering team is working on it
    RESOLVED    = "RESOLVED"      # fix deployed, awaiting Freshdesk update
    CLOSED      = "CLOSED"        # Freshdesk ticket closed
    FAILED      = "FAILED"        # creation / sync failed


# ── EngineeringTicket ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EngineeringTicket:
    """
    An engineering escalation ticket (Asana task in production).

    In Sprint 2.27.5 this is created by a mock adapter.
    In Sprint 2.28, the AsanaAdapter wires the real Asana API.

    Fields:
        ticket_id        — internal UUID (not the Asana task GID)
        external_id      — Asana task GID (None in mock mode)
        case_id          — the case that triggered this escalation
        freshdesk_ticket_id — the originating Freshdesk ticket
        title            — short engineering task title
        description      — full technical context (root cause, evidence, SOP steps)
        priority         — EngineeringPriority
        status           — EngineeringStatus lifecycle state
        assignee         — engineering team / person (optional)
        asana_project_id — target Asana project (None in mock mode)
        created_at       — ISO timestamp
        updated_at       — ISO timestamp
        resolved_at      — ISO timestamp (set when status → RESOLVED)
        metadata         — extensible dict for future fields
    """
    ticket_id:            str
    external_id:          str | None
    case_id:              str
    freshdesk_ticket_id:  str
    title:                str
    description:          str
    priority:             EngineeringPriority
    status:               EngineeringStatus
    assignee:             str | None
    asana_project_id:     str | None
    created_at:           str
    updated_at:           str
    resolved_at:          str | None = None
    metadata:             dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id":            self.ticket_id,
            "external_id":          self.external_id,
            "case_id":              self.case_id,
            "freshdesk_ticket_id":  self.freshdesk_ticket_id,
            "title":                self.title,
            "description":          self.description,
            "priority":             self.priority.value,
            "status":               self.status.value,
            "assignee":             self.assignee,
            "asana_project_id":     self.asana_project_id,
            "created_at":           self.created_at,
            "updated_at":           self.updated_at,
            "resolved_at":          self.resolved_at,
            "metadata":             dict(self.metadata),
        }

    def with_status(self, status: EngineeringStatus, resolved_at: str | None = None) -> "EngineeringTicket":
        """Return a new ticket with updated status (immutable update pattern)."""
        return EngineeringTicket(
            ticket_id=self.ticket_id,
            external_id=self.external_id,
            case_id=self.case_id,
            freshdesk_ticket_id=self.freshdesk_ticket_id,
            title=self.title,
            description=self.description,
            priority=self.priority,
            status=status,
            assignee=self.assignee,
            asana_project_id=self.asana_project_id,
            created_at=self.created_at,
            updated_at=_now_iso(),
            resolved_at=resolved_at or self.resolved_at,
            metadata=dict(self.metadata),
        )


# ── EngineeringEscalationResult ───────────────────────────────────────────────

@dataclass(frozen=True)
class EngineeringEscalationResult:
    """
    Output of EngineeringEscalationService.create_ticket().

    Returned by every public method on the service regardless of success/failure.
    The caller inspects `success` before using `ticket`.

    Fields:
        result_id   — UUID for this specific result
        success     — True iff the operation completed without error
        ticket      — EngineeringTicket (populated even on failure with FAILED status)
        operation   — which operation was performed ("create", "update", "resolve", "sync")
        error_code  — error identifier if not successful, else None
        error_msg   — human-readable error message if not successful, else None
        executed_at — ISO timestamp
        duration_ms — wall-clock duration of the operation
    """
    result_id:   str
    success:     bool
    ticket:      EngineeringTicket
    operation:   str
    error_code:  str | None
    error_msg:   str | None
    executed_at: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":   self.result_id,
            "success":     self.success,
            "ticket":      self.ticket.to_dict(),
            "operation":   self.operation,
            "error_code":  self.error_code,
            "error_msg":   self.error_msg,
            "executed_at": self.executed_at,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def failure(
        cls,
        case_id:             str,
        freshdesk_ticket_id: str,
        operation:           str,
        error_code:          str,
        error_msg:           str,
        duration_ms:         int = 0,
    ) -> "EngineeringEscalationResult":
        """Build a failure result with a minimal FAILED-status ticket."""
        now = _now_iso()
        ticket = EngineeringTicket(
            ticket_id=_new_id(),
            external_id=None,
            case_id=case_id,
            freshdesk_ticket_id=freshdesk_ticket_id,
            title="[FAILED] Engineering escalation",
            description=error_msg,
            priority=EngineeringPriority.HIGH,
            status=EngineeringStatus.FAILED,
            assignee=None,
            asana_project_id=None,
            created_at=now,
            updated_at=now,
        )
        return cls(
            result_id=_new_id(),
            success=False,
            ticket=ticket,
            operation=operation,
            error_code=error_code,
            error_msg=error_msg,
            executed_at=now,
            duration_ms=duration_ms,
        )
