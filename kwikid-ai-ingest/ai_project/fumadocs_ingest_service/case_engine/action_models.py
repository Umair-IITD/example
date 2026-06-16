"""
case_engine/action_models.py

Data models for the Sprint 2.1 Action Gateway.

All models are plain dataclasses — no Pydantic, no FastAPI dependency.
They must be JSON-serializable and DB-round-trippable against the
action_gateway and action_gateway_transitions tables (S2_001_action_gateway.sql).

Canonical type policy (Sprint 1.2):
  action_id, case_id, transition_id → UUID strings (str(uuid.uuid4()))
  ticket_id, client, actor          → plain TEXT
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from case_engine.action_state import ActionRiskLevel, ActionState, DEFAULT_MAX_ATTEMPTS


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Approval deadline defaults ─────────────────────────────────────────────────
# Configurable in Sprint 2.2 via settings. Hard-coded here for Sprint 2.1.

APPROVAL_DEADLINE_REVERSIBLE   = timedelta(hours=4)
APPROVAL_DEADLINE_IRREVERSIBLE = timedelta(hours=24)
EXECUTION_TIMEOUT_DEFAULT      = timedelta(minutes=15)


# ── Idempotency key ────────────────────────────────────────────────────────────

def compute_idempotency_key(
    case_id: str,
    action_type: str,
    action_namespace: str,
    action_params: dict[str, Any],
) -> str:
    """
    Compute a deterministic SHA-256 idempotency key for an action proposal.

    The key is computed from the action's intent, not its identity, so that
    duplicate proposals for the same logical action are rejected by the
    UNIQUE constraint on action_gateway.idempotency_key.

    Canonical JSON (sorted keys, no whitespace) ensures stability across
    Python versions and dict orderings.

    NOTE: The parameter name `action_params` is intentionally kept for backward
    compatibility with all callers. Internally the JSON key is "params" — this
    is part of the hash algorithm and must not change.
    """
    payload = {
        "case_id":          case_id,
        "action_type":      action_type,
        "action_namespace": action_namespace,
        "params":           action_params,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── ActionRequest ──────────────────────────────────────────────────────────────

@dataclass
class ActionRequest:
    """In-memory representation of an action_gateway row."""

    # Identity
    action_id:           str          = field(default_factory=_new_id)
    case_id:             str          = ""
    ticket_id:           str          = ""
    client:              str          = ""

    # Classification
    action_type:         str          = ""
    action_namespace:    str          = ""
    risk_level:          ActionRiskLevel = ActionRiskLevel.SAFE

    # State machine
    current_state:       ActionState  = ActionState.PROPOSED

    # Proposal
    proposed_by:         str          = "system"
    proposed_at:         datetime     = field(default_factory=_now)

    # Payload (sanitized — no PII). DB column: action_payload.
    action_payload:      dict[str, Any] = field(default_factory=dict)

    # Rollback spec (REVERSIBLE only — stored at proposal time)
    rollback_action_type: str | None  = None
    rollback_params:      dict[str, Any] | None = None

    # Approval
    approval_required:   bool         = True
    # Unified SLA deadline: approval window (AWAITING_APPROVAL) or executor
    # pickup window (APPROVED). NULL for SAFE (auto-approved).
    expires_at:          datetime | None = None
    approver:            str | None   = None   # agent_id or 'auto_approval'
    approved_at:         datetime | None = None
    rejected_at:         datetime | None = None
    approval_notes:      str | None   = None

    # Execution tracking
    executor_id:         str | None   = None
    execution_started_at:    datetime | None = None
    execution_completed_at:  datetime | None = None
    execution_failed_at:     datetime | None = None  # set on FAILED / TIMED_OUT
    execution_attempt:   int          = 0
    max_attempts:        int          = 3

    # Result
    execution_result:    dict[str, Any] | None = None
    failure_code:        str | None   = None
    failure_reason:      str | None   = None

    # Idempotency
    idempotency_key:     str          = ""

    # Rollback linkage
    is_rolled_back:      bool         = False
    rollback_action_id:  str | None   = None
    rollback_completed_at: datetime | None = None  # set when compensation EXECUTED

    # Dead letter — set when all retries exhausted
    dead_lettered_at:    datetime | None = None

    # Cancellation — set when operator cancels before execution
    cancelled_at:        datetime | None = None

    # Timestamps
    created_at:          datetime     = field(default_factory=_now)
    updated_at:          datetime     = field(default_factory=_now)

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        from case_engine.action_state import TERMINAL_ACTION_STATES
        return self.current_state in TERMINAL_ACTION_STATES

    @property
    def can_retry(self) -> bool:
        return (
            self.current_state in (ActionState.FAILED, ActionState.TIMED_OUT)
            and self.execution_attempt < self.max_attempts
        )

    @property
    def is_approval_overdue(self) -> bool:
        return (
            self.current_state == ActionState.AWAITING_APPROVAL
            and self.expires_at is not None
            and _now() > self.expires_at
        )

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_db_row(self) -> dict[str, Any]:
        def _iso(dt: datetime | None) -> str | None:
            return dt.isoformat() if dt else None

        return {
            "action_id":              self.action_id,
            "case_id":                self.case_id,
            "ticket_id":              self.ticket_id,
            "client":                 self.client,
            "action_type":            self.action_type,
            "action_namespace":       self.action_namespace,
            "risk_level":             self.risk_level.value,
            "current_state":          self.current_state.value,
            "proposed_by":            self.proposed_by,
            "proposed_at":            _iso(self.proposed_at),
            "action_payload":         self.action_payload,
            "rollback_action_type":   self.rollback_action_type,
            "rollback_params":        self.rollback_params,
            "approval_required":      self.approval_required,
            "expires_at":             _iso(self.expires_at),
            "approver":               self.approver,
            "approved_at":            _iso(self.approved_at),
            "rejected_at":            _iso(self.rejected_at),
            "approval_notes":         self.approval_notes,
            "executor_id":            self.executor_id,
            "execution_started_at":   _iso(self.execution_started_at),
            "execution_completed_at": _iso(self.execution_completed_at),
            "execution_failed_at":    _iso(self.execution_failed_at),
            "execution_attempt":      self.execution_attempt,
            "max_attempts":           self.max_attempts,
            "execution_result":       self.execution_result,
            "failure_code":           self.failure_code,
            "failure_reason":         self.failure_reason,
            "idempotency_key":        self.idempotency_key,
            "is_rolled_back":         self.is_rolled_back,
            "rollback_action_id":     self.rollback_action_id,
            "rollback_completed_at":  _iso(self.rollback_completed_at),
            "dead_lettered_at":       _iso(self.dead_lettered_at),
            "cancelled_at":           _iso(self.cancelled_at),
            "created_at":             _iso(self.created_at),
            "updated_at":             _iso(self.updated_at),
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ActionRequest":
        def _dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            action_id=row["action_id"],
            case_id=row["case_id"],
            ticket_id=row["ticket_id"],
            client=row["client"],
            action_type=row["action_type"],
            action_namespace=row["action_namespace"],
            risk_level=ActionRiskLevel(row["risk_level"]),
            current_state=ActionState(row["current_state"]),
            proposed_by=row.get("proposed_by", "system"),
            proposed_at=_dt(row.get("proposed_at")) or _now(),
            action_payload=row.get("action_payload") or {},
            rollback_action_type=row.get("rollback_action_type"),
            rollback_params=row.get("rollback_params"),
            approval_required=row.get("approval_required", True),
            expires_at=_dt(row.get("expires_at")),
            approver=row.get("approver"),
            approved_at=_dt(row.get("approved_at")),
            rejected_at=_dt(row.get("rejected_at")),
            approval_notes=row.get("approval_notes"),
            executor_id=row.get("executor_id"),
            execution_started_at=_dt(row.get("execution_started_at")),
            execution_completed_at=_dt(row.get("execution_completed_at")),
            execution_failed_at=_dt(row.get("execution_failed_at")),
            execution_attempt=row.get("execution_attempt", 0),
            max_attempts=row.get("max_attempts", 3),
            execution_result=row.get("execution_result"),
            failure_code=row.get("failure_code"),
            failure_reason=row.get("failure_reason"),
            idempotency_key=row.get("idempotency_key", ""),
            is_rolled_back=row.get("is_rolled_back", False),
            rollback_action_id=row.get("rollback_action_id"),
            rollback_completed_at=_dt(row.get("rollback_completed_at")),
            dead_lettered_at=_dt(row.get("dead_lettered_at")),
            cancelled_at=_dt(row.get("cancelled_at")),
            created_at=_dt(row.get("created_at")) or _now(),
            updated_at=_dt(row.get("updated_at")) or _now(),
        )


# ── ActionTransitionRecord ─────────────────────────────────────────────────────

@dataclass
class ActionTransitionRecord:
    """In-memory representation of an action_gateway_transitions row."""

    transition_id: str          = field(default_factory=_new_id)
    action_id:     str          = ""
    case_id:       str          = ""
    ticket_id:     str          = ""
    client:        str          = ""
    from_state:    ActionState  = ActionState.PROPOSED
    to_state:      ActionState  = ActionState.PROPOSED
    actor:         str          = "system"
    reason:        str | None   = None
    detail:        dict[str, Any] = field(default_factory=dict)
    created_at:    datetime     = field(default_factory=_now)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "action_id":     self.action_id,
            "case_id":       self.case_id,
            "ticket_id":     self.ticket_id,
            "client":        self.client,
            "from_state":    self.from_state.value,
            "to_state":      self.to_state.value,
            "actor":         self.actor,
            "reason":        self.reason,
            "detail":        self.detail,
            "created_at":    self.created_at.isoformat(),
        }


# ── ActionProposal ─────────────────────────────────────────────────────────────

@dataclass
class ActionProposal:
    """
    Input DTO for ActionGateway.propose().

    Separates the external API contract from the internal ActionRequest model.
    The gateway converts this into a fully-populated ActionRequest.

    NOTE: action_params is the DTO field name (Python-facing). The DB column
    is action_payload. The gateway maps proposal.action_params → action.action_payload.
    """
    action_type:          str
    action_namespace:     str
    risk_level:           ActionRiskLevel
    action_params:        dict[str, Any] = field(default_factory=dict)
    rollback_action_type: str | None = None
    rollback_params:      dict[str, Any] | None = None
    proposed_by:          str = "system"

    def validate(self) -> None:
        """Raise ValueError if the proposal is self-inconsistent."""
        if not self.action_type.strip():
            raise ValueError("action_type must not be empty")
        if not self.action_namespace.strip():
            raise ValueError("action_namespace must not be empty")
        if self.risk_level == ActionRiskLevel.REVERSIBLE:
            if not self.rollback_action_type:
                raise ValueError(
                    "REVERSIBLE actions must specify rollback_action_type so that "
                    "compensation can proceed without re-deriving the logic."
                )
        if self.risk_level == ActionRiskLevel.IRREVERSIBLE:
            if self.rollback_action_type is not None:
                raise ValueError(
                    "IRREVERSIBLE actions must not specify rollback_action_type — "
                    "there is no safe compensation path for irreversible operations."
                )
