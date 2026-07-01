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


# ── Case priority ──────────────────────────────────────────────────────────────

class CasePriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


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

    # Sprint 2.15: Slot filling state (JSONB). Maps slot_name → {status, value, attempt_count}.
    slot_state: dict[str, Any] = field(default_factory=dict)

    # Sprint 2.16: Workflow orchestration state
    workflow_id:      str | None     = None    # playbook ID (e.g., "vkyc_session_failure_v1")
    workflow_state:   str | None     = None    # WorkflowState value
    workflow_step_index: int | None  = None    # current step index (informational)
    workflow_context: dict[str, Any] = field(default_factory=dict)  # WorkflowExecutionResult JSONB

    # Sprint 2.27.9: Tenant context (in-process only — not persisted in DB)
    # Carries client_id, enabled_tools, credentials_ref, environment, etc.
    tenant_context: Any = None

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
            "slot_state":         self.slot_state if self.slot_state else None,
            "workflow_id":        self.workflow_id,
            "workflow_state":     self.workflow_state,
            "workflow_step_index": self.workflow_step_index,
            "workflow_context":   self.workflow_context if self.workflow_context else None,
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
            slot_state=row.get("slot_state") or {},
            workflow_id=row.get("workflow_id"),
            workflow_state=row.get("workflow_state"),
            workflow_step_index=row.get("workflow_step_index"),
            workflow_context=row.get("workflow_context") or {},
        )


# ── Case context ──────────────────────────────────────────────────────────────

@dataclass
class CaseContext:
    """
    Active slot-filling context for a case.

    Built from Case.slot_state at query time. Tracks clarification progress
    (turn count, last slot asked). Not independently persisted — it is a
    structured view of Case.slot_state metadata fields.
    """
    case_id:             str
    topic:               str | None = None
    clarification_turns: int = 0
    last_asked_slot:     str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id":             self.case_id,
            "topic":               self.topic,
            "clarification_turns": self.clarification_turns,
            "last_asked_slot":     self.last_asked_slot,
        }


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
    STATE_TRANSITION        = "STATE_TRANSITION"
    ACTION_PROPOSED         = "ACTION_PROPOSED"
    ACTION_EXECUTED         = "ACTION_EXECUTED"
    ACTION_REJECTED         = "ACTION_REJECTED"
    ESCALATION_TRIGGERED    = "ESCALATION_TRIGGERED"
    NOTE_POSTED             = "NOTE_POSTED"
    RAG_CALLED              = "RAG_CALLED"
    SLOT_FILLED             = "SLOT_FILLED"
    CLASSIFICATION          = "CLASSIFICATION"
    ERROR                   = "ERROR"
    # Sprint 2.16: Workflow orchestration events
    WORKFLOW_STARTED        = "WORKFLOW_STARTED"
    WORKFLOW_STEP_COMPLETED = "WORKFLOW_STEP_COMPLETED"
    WORKFLOW_ESCALATED      = "WORKFLOW_ESCALATED"
    WORKFLOW_RESOLVED       = "WORKFLOW_RESOLVED"
    # Sprint 2.17: Additional workflow lifecycle events
    WORKFLOW_RESUMED        = "WORKFLOW_RESUMED"
    WORKFLOW_COMPLETED      = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED         = "WORKFLOW_FAILED"
    # Sprint 2.17: Tool execution events
    TOOL_EXECUTED           = "TOOL_EXECUTED"
    TOOL_FAILED             = "TOOL_FAILED"
    # Sprint 2.18: Investigation layer events
    INVESTIGATION_STARTED   = "INVESTIGATION_STARTED"
    INVESTIGATION_COMPLETED = "INVESTIGATION_COMPLETED"
    # Sprint 2.19: Workflow-level investigation step events
    WORKFLOW_INVESTIGATION_STARTED   = "WORKFLOW_INVESTIGATION_STARTED"
    WORKFLOW_INVESTIGATION_COMPLETED = "WORKFLOW_INVESTIGATION_COMPLETED"
    # Sprint 2.20: Knowledge Layer events
    KNOWLEDGE_SEARCH_STARTED   = "KNOWLEDGE_SEARCH_STARTED"
    KNOWLEDGE_SEARCH_COMPLETED = "KNOWLEDGE_SEARCH_COMPLETED"
    SOP_MATCH_FOUND            = "SOP_MATCH_FOUND"
    SOP_MATCH_NOT_FOUND        = "SOP_MATCH_NOT_FOUND"
    # Sprint 2.21: Action Proposal Engine events
    ACTION_PROPOSAL_STARTED    = "ACTION_PROPOSAL_STARTED"
    ACTION_PROPOSAL_COMPLETED  = "ACTION_PROPOSAL_COMPLETED"
    ACTION_PROPOSAL_BLOCKED    = "ACTION_PROPOSAL_BLOCKED"
    RISK_ASSESSMENT_COMPLETED  = "RISK_ASSESSMENT_COMPLETED"
    # Sprint 2.22: Action Gateway + Approval events
    ACTION_GATEWAY_STARTED     = "ACTION_GATEWAY_STARTED"
    ACTION_GATEWAY_COMPLETED   = "ACTION_GATEWAY_COMPLETED"
    APPROVAL_REQUESTED         = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED           = "APPROVAL_GRANTED"
    APPROVAL_REJECTED          = "APPROVAL_REJECTED"
    # Sprint 2.23: Execution layer events
    EXECUTION_STARTED          = "EXECUTION_STARTED"
    EXECUTION_COMPLETED        = "EXECUTION_COMPLETED"
    VERIFICATION_STARTED       = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED     = "VERIFICATION_COMPLETED"
    RECOVERY_STARTED           = "RECOVERY_STARTED"
    RECOVERY_COMPLETED         = "RECOVERY_COMPLETED"
    RESOLUTION_STARTED         = "RESOLUTION_STARTED"
    RESOLUTION_COMPLETED       = "RESOLUTION_COMPLETED"
    # Sprint 2.24: Investigation Reasoning Engine
    REASONING_STARTED          = "REASONING_STARTED"
    REASONING_COMPLETED        = "REASONING_COMPLETED"
    WORKFLOW_REASONING_STARTED  = "WORKFLOW_REASONING_STARTED"
    WORKFLOW_REASONING_COMPLETED = "WORKFLOW_REASONING_COMPLETED"
    # Sprint 2.25: Clarification Layer
    CLARIFICATION_STARTED           = "CLARIFICATION_STARTED"
    CLARIFICATION_COMPLETED         = "CLARIFICATION_COMPLETED"
    WORKFLOW_CLARIFICATION_STARTED  = "WORKFLOW_CLARIFICATION_STARTED"
    WORKFLOW_CLARIFICATION_COMPLETED  = "WORKFLOW_CLARIFICATION_COMPLETED"
    # Sprint 2.26: Clarification Resume + Attempt Tracking
    WORKFLOW_CLARIFICATION_RESUMED    = "WORKFLOW_CLARIFICATION_RESUMED"
    CLARIFICATION_ATTEMPT_INCREMENTED = "CLARIFICATION_ATTEMPT_INCREMENTED"
    # Sprint 2.27: Adapter Framework events
    ADAPTER_REQUEST_STARTED    = "ADAPTER_REQUEST_STARTED"
    ADAPTER_REQUEST_COMPLETED  = "ADAPTER_REQUEST_COMPLETED"
    ADAPTER_HEALTH_CHECK       = "ADAPTER_HEALTH_CHECK"
    ADAPTER_ROUTING_FAILED     = "ADAPTER_ROUTING_FAILED"
    # Sprint 2.27.5: Architecture convergence events
    KNOWLEDGE_ORCHESTRATION_COMPLETED = "KNOWLEDGE_ORCHESTRATION_COMPLETED"
    RESPONSE_GENERATED                = "RESPONSE_GENERATED"
    ENGINEERING_ESCALATION_CREATED    = "ENGINEERING_ESCALATION_CREATED"
    ENGINEERING_ESCALATION_RESOLVED   = "ENGINEERING_ESCALATION_RESOLVED"
    AGENT_RUN_STARTED                 = "AGENT_RUN_STARTED"
    AGENT_RUN_COMPLETED               = "AGENT_RUN_COMPLETED"
    TICKET_PROCESSED                  = "TICKET_PROCESSED"
    # Sprint 2.27.8: Dry-run mode and startup validation events
    DRY_RUN_MODE_ACTIVE               = "DRY_RUN_MODE_ACTIVE"
    PRODUCTION_MODE_ACTIVE            = "PRODUCTION_MODE_ACTIVE"
    DRY_RUN_EXECUTION                 = "DRY_RUN_EXECUTION"
    DRY_RUN_ROUTE                     = "DRY_RUN_ROUTE"
    DRY_RUN_ACTION                    = "DRY_RUN_ACTION"
    STARTUP_VALIDATION_PASSED         = "STARTUP_VALIDATION_PASSED"
    STARTUP_VALIDATION_FAILED         = "STARTUP_VALIDATION_FAILED"
    STARTUP_VALIDATION_WARNING        = "STARTUP_VALIDATION_WARNING"
    INVARIANT_VIOLATION               = "INVARIANT_VIOLATION"
    # Sprint 2.27.9: Multi-Tenant Client Resolution events
    CLIENT_RESOLVED                   = "CLIENT_RESOLVED"
    CLIENT_RESOLUTION_FAILED          = "CLIENT_RESOLUTION_FAILED"
    TENANT_CONTEXT_ATTACHED           = "TENANT_CONTEXT_ATTACHED"
    UNKNOWN_CLIENT_ESCALATED          = "UNKNOWN_CLIENT_ESCALATED"
    TENANT_REGISTRY_VALIDATED         = "TENANT_REGISTRY_VALIDATED"
    TENANT_REGISTRY_VALIDATION_FAILED = "TENANT_REGISTRY_VALIDATION_FAILED"
    # Sprint 2.28.1: Freshdesk Foundation Layer events
    WEBHOOK_RECEIVED                  = "WEBHOOK_RECEIVED"
    WEBHOOK_REJECTED                  = "WEBHOOK_REJECTED"
    WEBHOOK_DUPLICATE                 = "WEBHOOK_DUPLICATE"
    TICKET_INGESTED                   = "TICKET_INGESTED"
    TICKET_UPDATED                    = "TICKET_UPDATED"
    TICKET_SKIPPED                    = "TICKET_SKIPPED"
    CUSTOMER_REPLY_RECEIVED           = "CUSTOMER_REPLY_RECEIVED"
    AGENT_NOTE_RECEIVED               = "AGENT_NOTE_RECEIVED"
    PRIVATE_NOTE_ADDED                = "PRIVATE_NOTE_ADDED"
    PUBLIC_REPLY_SENT                 = "PUBLIC_REPLY_SENT"
    CONVERSATION_STATE_UPDATED        = "CONVERSATION_STATE_UPDATED"
    CLARIFICATION_REPLY_RECEIVED      = "CLARIFICATION_REPLY_RECEIVED"
    FRESHDESK_API_ERROR               = "FRESHDESK_API_ERROR"
    SIGNATURE_FAILURE                 = "SIGNATURE_FAILURE"
    CLARIFICATION_PENDING             = "CLARIFICATION_PENDING"


@dataclass
class AuditEntry:
    audit_id:        str            = field(default_factory=_new_id)
    case_id:         str | None     = None
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
