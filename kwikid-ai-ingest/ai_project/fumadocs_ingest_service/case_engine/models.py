"""
case_engine/models.py

Data models for the case engine.

All models are plain dataclasses or Pydantic-free dataclasses to keep the
case engine independent of FastAPI. They must be JSON-serializable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from case_engine.case_state import CaseState


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Classification ─────────────────────────────────────────────────────────────

class TopicKey(str, Enum):
    OTP_DELIVERY_FAILURE  = "OTP_Delivery_Failure"
    VKYC_SESSION_FAILURE  = "VKYC_Session_Failure"
    DOCUMENT_OCR_FAILURE  = "Document_OCR_Failure"
    AGENT_PORTAL_ISSUE    = "Agent_Portal_Issue"
    API_CALLBACK_FAILURE  = "API_Callback_Failure"
    UNKNOWN               = "UNKNOWN"


@dataclass
class ClassificationResult:
    topic: TopicKey
    confidence: float          # 0.0 – 1.0
    tier_used: int             # 1 = regex, 2 = semantic, 0 = none
    raw_text_excerpt: str = ""

    @property
    def meets_threshold(self) -> bool:
        return self.confidence >= 0.85 and self.topic != TopicKey.UNKNOWN


# ── Retrieval match ────────────────────────────────────────────────────────────

class MatchType(str, Enum):
    EXACT_MATCH   = "exact_match"
    RELATED_MATCH = "related_match"
    WEAK_MATCH    = "weak_match"
    NO_MATCH      = "no_match"


# ── Case ──────────────────────────────────────────────────────────────────────

@dataclass
class Case:
    """In-memory representation of a case record."""
    case_id:       str       = field(default_factory=_new_id)
    ticket_id:     str       = ""
    client:        str       = ""
    topic:         str | None = None
    confidence:    float | None = None
    current_state: CaseState = CaseState.NEW
    created_at:    datetime  = field(default_factory=_now)
    updated_at:    datetime  = field(default_factory=_now)
    closed_at:     datetime | None = None

    # In-process enrichment (not persisted in cases table; used for Transfer Context)
    attempted_remediations: list[dict[str, Any]] = field(default_factory=list)
    failure_code:           str | None = None
    escalation_reason:      str | None = None
    retrieval_match_type:   str | None = None

    # Sprint 1.1: SLA deadline — set by Level 2 SLA watchdog (Sprint 2)
    sla_breach_at: datetime | None = None

    def to_db_row(self) -> dict[str, Any]:
        return {
            "case_id":       self.case_id,
            "ticket_id":     self.ticket_id,
            "client":        self.client,
            "topic":         self.topic,
            "confidence":    self.confidence,
            "current_state": self.current_state.value,
            "created_at":    self.created_at.isoformat(),
            "updated_at":    self.updated_at.isoformat(),
            "closed_at":     self.closed_at.isoformat() if self.closed_at else None,
            "sla_breach_at": self.sla_breach_at.isoformat() if self.sla_breach_at else None,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "Case":
        return cls(
            case_id=row["case_id"],
            ticket_id=row["ticket_id"],
            client=row["client"],
            topic=row.get("topic"),
            confidence=row.get("confidence"),
            current_state=CaseState(row["current_state"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else _now(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else _now(),
            closed_at=datetime.fromisoformat(row["closed_at"]) if row.get("closed_at") else None,
            sla_breach_at=datetime.fromisoformat(row["sla_breach_at"]) if row.get("sla_breach_at") else None,
        )


# ── Transition ────────────────────────────────────────────────────────────────

@dataclass
class CaseTransition:
    transition_id: str       = field(default_factory=_new_id)
    case_id:       str       = ""
    from_state:    CaseState = CaseState.NEW
    to_state:      CaseState = CaseState.NEW
    reason:        str       = ""
    actor:         str       = "system"
    created_at:    datetime  = field(default_factory=_now)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "case_id":       self.case_id,
            "from_state":    self.from_state.value,
            "to_state":      self.to_state.value,
            "reason":        self.reason,
            "actor":         self.actor,
            "created_at":    self.created_at.isoformat(),
        }


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditEventType(str, Enum):
    STATE_TRANSITION     = "STATE_TRANSITION"
    ACTION_PROPOSED      = "ACTION_PROPOSED"
    ACTION_EXECUTED      = "ACTION_EXECUTED"
    ACTION_REJECTED      = "ACTION_REJECTED"
    ESCALATION_TRIGGERED = "ESCALATION_TRIGGERED"
    NOTE_POSTED          = "NOTE_POSTED"
    RAG_CALLED           = "RAG_CALLED"
    SLOT_FILLED          = "SLOT_FILLED"
    CLASSIFICATION       = "CLASSIFICATION"
    ERROR                = "ERROR"


@dataclass
class AuditEntry:
    audit_id:        str            = field(default_factory=_new_id)
    case_id:         str            = ""
    ticket_id:       str            = ""
    client:          str            = ""
    event_timestamp: datetime       = field(default_factory=_now)
    actor:           str            = "system"
    action_type:     AuditEventType = AuditEventType.STATE_TRANSITION
    action_detail:   dict[str, Any] = field(default_factory=dict)
    outcome:         str | None     = None
    error_code:      str | None     = None
    idempotency_key: str | None     = None

    def to_db_row(self) -> dict[str, Any]:
        return {
            "audit_id":        self.audit_id,
            "case_id":         self.case_id,
            "ticket_id":       self.ticket_id,
            "client":          self.client,
            "event_timestamp": self.event_timestamp.isoformat(),
            "actor":           self.actor,
            "action_type":     self.action_type.value,
            "action_detail":   self.action_detail,
            "outcome":         self.outcome,
            "error_code":      self.error_code,
            "idempotency_key": self.idempotency_key,
        }
