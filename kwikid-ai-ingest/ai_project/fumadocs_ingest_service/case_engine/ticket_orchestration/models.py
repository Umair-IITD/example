"""
case_engine/ticket_orchestration/models.py

Sprint 2.27.5: Ticket Orchestration domain models.

Per blueprint flow_diagram.mermaid:
  FD → TICKET → CASE → CLASSIFIER → ...

The TicketOrchestrator is the abstracted ticket lifecycle layer.
It owns the full lifetime of a ticket from ingestion to closure.

Design:
  - Frozen dataclasses: immutable, JSON-serializable
  - TicketContext carries everything needed for a ticket processing run
  - TicketOrchestrationResult is the output for every orchestrator method call
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


class TicketLifecycleState(str, Enum):
    """
    Lifecycle state for a ticket within the orchestration layer.

    Maps to the blueprint node sequence:
      FD → TICKET (RECEIVED) → CASE (OPEN) → processing → CLOSE (CLOSED)
    """
    RECEIVED   = "RECEIVED"    # ticket received from Freshdesk
    OPEN       = "OPEN"        # case opened, processing begins
    PROCESSING = "PROCESSING"  # agent is actively processing
    WAITING    = "WAITING"     # awaiting customer reply or human approval
    ESCALATED  = "ESCALATED"   # escalated to L2 / engineering
    CLOSED     = "CLOSED"      # resolution confirmed
    FAILED     = "FAILED"      # unrecoverable error


@dataclass(frozen=True)
class TicketContext:
    """
    Full context for a Freshdesk ticket entering the orchestration layer.

    Populated at ingestion and passed to the SupportAgentRuntime.

    Fields:
        ticket_id         — Freshdesk ticket ID
        client            — client/tenant identifier
        subject           — ticket subject line
        description       — ticket body / customer message text
        requester_email   — customer email (for response routing)
        freshdesk_url     — Freshdesk ticket URL (for audit links)
        attachments       — list of attachment metadata dicts
        metadata          — extensible dict (custom fields, tags, etc.)
        received_at       — ISO timestamp when ticket was received
    """
    ticket_id:       str
    client:          str
    subject:         str
    description:     str
    requester_email: str         = ""
    freshdesk_url:   str         = ""
    attachments:     tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metadata:        dict[str, Any] = field(default_factory=dict)
    received_at:     str            = field(default_factory=_now_iso)

    def message_text(self) -> str:
        """Build the full message text for the agent (subject + description)."""
        parts = []
        if self.subject:
            parts.append(f"Subject: {self.subject}")
        if self.description:
            parts.append(self.description)
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id":       self.ticket_id,
            "client":          self.client,
            "subject":         self.subject,
            "description":     self.description,
            "requester_email": self.requester_email,
            "freshdesk_url":   self.freshdesk_url,
            "attachments":     list(self.attachments),
            "metadata":        dict(self.metadata),
            "received_at":     self.received_at,
        }


@dataclass(frozen=True)
class TicketOrchestrationResult:
    """
    Output of every TicketOrchestrator method.

    Carries the full outcome: lifecycle state, agent result, and any errors.

    Fields:
        orchestration_id   — UUID for this specific orchestration run
        ticket_id          — Freshdesk ticket ID
        case_id            — Case ID (if a case was opened, else None)
        lifecycle_state    — TicketLifecycleState current state
        agent_result       — AgentExecutionResult.to_dict() if agent ran, else None
        operation          — which operation produced this result
        success            — True iff operation completed without error
        error_code         — error identifier if not successful, else None
        error_msg          — human-readable error if not successful, else None
        executed_at        — ISO timestamp
        duration_ms        — wall-clock duration
        metadata           — extensible dict
    """
    orchestration_id: str
    ticket_id:        str
    case_id:          str | None
    lifecycle_state:  TicketLifecycleState
    agent_result:     dict[str, Any] | None
    operation:        str
    success:          bool
    error_code:       str | None
    error_msg:        str | None
    executed_at:      str
    duration_ms:      int
    metadata:         dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "orchestration_id": self.orchestration_id,
            "ticket_id":        self.ticket_id,
            "case_id":          self.case_id,
            "lifecycle_state":  self.lifecycle_state.value,
            "agent_result":     self.agent_result,
            "operation":        self.operation,
            "success":          self.success,
            "error_code":       self.error_code,
            "error_msg":        self.error_msg,
            "executed_at":      self.executed_at,
            "duration_ms":      self.duration_ms,
            "metadata":         dict(self.metadata),
        }

    @classmethod
    def failure(
        cls,
        ticket_id:   str,
        operation:   str,
        error_code:  str,
        error_msg:   str,
        case_id:     str | None = None,
        duration_ms: int = 0,
    ) -> "TicketOrchestrationResult":
        """Build a failure result."""
        return cls(
            orchestration_id=_new_id(),
            ticket_id=ticket_id,
            case_id=case_id,
            lifecycle_state=TicketLifecycleState.FAILED,
            agent_result=None,
            operation=operation,
            success=False,
            error_code=error_code,
            error_msg=error_msg,
            executed_at=_now_iso(),
            duration_ms=duration_ms,
        )
