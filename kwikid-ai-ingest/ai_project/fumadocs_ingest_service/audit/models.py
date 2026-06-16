"""
audit/models.py

Sprint 2.8: Immutable audit event models.

AuditEvent is the canonical audit record. It is write-once — once emitted,
it must not be modified. AuditLogger stores and retrieves these records.

Event type coverage:
  ACTION_APPROVED          — AWAITING_APPROVAL → APPROVED (human decision)
  ACTION_REJECTED          — AWAITING_APPROVAL → REJECTED (human decision)
  ACTION_EXPIRED           — AWAITING_APPROVAL → EXPIRED  (SLA watchdog)
  ACTION_EXECUTION_STARTED — APPROVED → EXECUTING
  ACTION_EXECUTED          — EXECUTING → EXECUTED
  ACTION_FAILED            — EXECUTING → FAILED
  ACTION_ROLLED_BACK       — ROLLING_BACK → ROLLED_BACK
  ACTION_ROLLBACK_FAILED   — ROLLING_BACK → ROLLBACK_FAILED

Future extension:
  Add persistence by injecting a storage backend into AuditLogger.
  The model layer (this file) remains unchanged.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AuditEventType(str, Enum):
    """Canonical audit event types for the action lifecycle."""
    ACTION_APPROVED          = "ACTION_APPROVED"
    ACTION_REJECTED          = "ACTION_REJECTED"
    ACTION_EXPIRED           = "ACTION_EXPIRED"
    ACTION_EXECUTION_STARTED = "ACTION_EXECUTION_STARTED"
    ACTION_EXECUTED          = "ACTION_EXECUTED"
    ACTION_FAILED            = "ACTION_FAILED"
    ACTION_ROLLED_BACK       = "ACTION_ROLLED_BACK"
    ACTION_ROLLBACK_FAILED   = "ACTION_ROLLBACK_FAILED"
    # Sprint 2.11: Admin and compliance operations
    ACTION_DEAD_LETTERED     = "ACTION_DEAD_LETTERED"
    ACTION_AUDIT_READ        = "ACTION_AUDIT_READ"
    # Sprint 2.14: Human recovery and operational control
    ACTION_CANCELLED                  = "ACTION_CANCELLED"
    ACTION_RECOVERED_FROM_DEAD_LETTER = "ACTION_RECOVERED_FROM_DEAD_LETTER"
    ACTION_MANUALLY_EXPIRED           = "ACTION_MANUALLY_EXPIRED"
    ACTION_ROLLBACK_TRIGGERED         = "ACTION_ROLLBACK_TRIGGERED"


@dataclass(frozen=True)
class AuditEvent:
    """
    Immutable audit record for a single lifecycle event.

    Fields:
        event_id:   Unique identifier for this audit record.
        action_id:  The action that triggered this event.
        event_type: What happened (see AuditEventType).
        actor:      Who or what caused the event ("human:alice", "watchdog:sla", etc.)
        timestamp:  When the event occurred.
        metadata:   Additional event-specific data (action_type, client, notes, etc.)
    """
    action_id:  str
    event_type: AuditEventType
    actor:      str
    case_id:    str            = ""
    client:     str            = ""
    event_id:   str            = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:  datetime       = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    metadata:   dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":   self.event_id,
            "action_id":  self.action_id,
            "case_id":    self.case_id,
            "client":     self.client,
            "event_type": self.event_type.value,
            "actor":      self.actor,
            "timestamp":  self.timestamp.isoformat(),
            "metadata":   self.metadata,
        }
