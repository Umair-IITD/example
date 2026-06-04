"""
case_engine/action_state.py

ActionState enum, risk levels, and deterministic state machine for the
Sprint 2.1 Action Gateway.

Mirrors the structure of case_state.py: all transition rules are explicit,
no LLM involvement, no async, no side effects.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Callable

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from case_engine.action_models import ActionRequest, ActionTransitionRecord


class ActionState(str, Enum):
    """
    Action request lifecycle states.

    Matches the current_state CHECK constraint in S2_001_action_gateway.sql
    (extended by S2_003_dead_letter_and_audit_events.sql with DEAD_LETTER).
    """
    PROPOSED          = "PROPOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED          = "APPROVED"
    REJECTED          = "REJECTED"
    EXPIRED           = "EXPIRED"
    EXECUTING         = "EXECUTING"
    EXECUTED          = "EXECUTED"
    FAILED            = "FAILED"
    TIMED_OUT         = "TIMED_OUT"
    ROLLING_BACK      = "ROLLING_BACK"
    ROLLED_BACK       = "ROLLED_BACK"
    ROLLBACK_FAILED   = "ROLLBACK_FAILED"
    DEAD_LETTER       = "DEAD_LETTER"


class ActionRiskLevel(str, Enum):
    """
    Risk tier for an action request.

    Determines approval requirements, max_attempts defaults, and
    rollback availability.
    """
    SAFE         = "SAFE"
    REVERSIBLE   = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"


# ── Transition table ───────────────────────────────────────────────────────────

ALLOWED_ACTION_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PROPOSED: frozenset({
        ActionState.AWAITING_APPROVAL,  # REVERSIBLE / IRREVERSIBLE — human needed
        ActionState.APPROVED,           # SAFE — auto-approved immediately
    }),
    ActionState.AWAITING_APPROVAL: frozenset({
        ActionState.APPROVED,           # human approved
        ActionState.REJECTED,           # human rejected
        ActionState.EXPIRED,            # approval_deadline elapsed
    }),
    ActionState.APPROVED: frozenset({
        ActionState.EXECUTING,          # executor acquired the action
        ActionState.EXPIRED,            # executor never picked up within window
    }),
    ActionState.EXECUTING: frozenset({
        ActionState.EXECUTED,           # target system confirmed success
        ActionState.FAILED,             # execution error; may retry
        ActionState.TIMED_OUT,          # executor heartbeat lost
    }),
    ActionState.EXECUTED: frozenset({
        ActionState.ROLLING_BACK,       # REVERSIBLE only; service enforces risk check
    }),
    ActionState.FAILED: frozenset({
        ActionState.APPROVED,           # retry: service checks attempt < max_attempts
        ActionState.DEAD_LETTER,        # exhausted all retries — permanent failure
    }),
    ActionState.TIMED_OUT: frozenset({
        ActionState.APPROVED,           # retry after timeout
        ActionState.FAILED,             # give up after max retries
        ActionState.DEAD_LETTER,        # exhausted all retries after timeout
    }),
    ActionState.ROLLING_BACK: frozenset({
        ActionState.ROLLED_BACK,        # compensation succeeded
        ActionState.ROLLBACK_FAILED,    # compensation failed — human escalation required
    }),
    # Terminal states — no outgoing transitions
    ActionState.REJECTED:        frozenset(),
    ActionState.EXPIRED:         frozenset(),
    ActionState.ROLLED_BACK:     frozenset(),
    ActionState.ROLLBACK_FAILED: frozenset(),
    ActionState.DEAD_LETTER:     frozenset(),
}

TERMINAL_ACTION_STATES: frozenset[ActionState] = frozenset({
    ActionState.REJECTED,
    ActionState.EXPIRED,
    ActionState.ROLLED_BACK,
    ActionState.ROLLBACK_FAILED,
    ActionState.DEAD_LETTER,
})

# EXECUTED is pseudo-terminal: REVERSIBLE actions may transition to ROLLING_BACK.
# Service layer validates risk_level before permitting this transition.
PSEUDO_TERMINAL_ACTION_STATES: frozenset[ActionState] = frozenset({
    ActionState.EXECUTED,
})

# Default max_attempts per risk level (enforced by ActionGateway.propose)
DEFAULT_MAX_ATTEMPTS: dict[ActionRiskLevel, int] = {
    ActionRiskLevel.SAFE:         3,
    ActionRiskLevel.REVERSIBLE:   3,
    ActionRiskLevel.IRREVERSIBLE: 1,   # strict: no auto-retry on high-risk
}


# ── State machine ──────────────────────────────────────────────────────────────

class ActionTransitionError(ValueError):
    """Raised when an illegal action state transition is attempted."""


class ActionStateMachine:
    """
    Validates and applies state transitions on ActionRequest objects.

    Pure: does not persist anything. Persistence and audit logging are
    handled by callers via the on_transition callback.

    Mirrors CaseStateMachine from case_engine/state_machine.py.
    """

    def __init__(
        self,
        on_transition: Callable[["ActionRequest", "ActionTransitionRecord"], None] | None = None,
    ) -> None:
        self._on_transition = on_transition

    def can_transition(self, from_state: ActionState, to_state: ActionState) -> bool:
        if from_state in TERMINAL_ACTION_STATES:
            return False
        return to_state in ALLOWED_ACTION_TRANSITIONS.get(from_state, frozenset())

    def is_terminal(self, state: ActionState) -> bool:
        return state in TERMINAL_ACTION_STATES

    def transition(
        self,
        action: "ActionRequest",
        to_state: ActionState,
        *,
        reason: str = "",
        actor: str = "system",
        detail: dict | None = None,
    ) -> "ActionTransitionRecord":
        """
        Apply a transition to the action request.

        Mutates action.current_state and action.updated_at in-place.
        Fires on_transition callback (for persistence + audit).
        Returns the ActionTransitionRecord.

        Raises ActionTransitionError if the transition is not allowed.
        """
        from datetime import datetime, timezone
        from case_engine.action_models import ActionTransitionRecord

        from_state = action.current_state

        if not self.can_transition(from_state, to_state):
            raise ActionTransitionError(
                f"Illegal action transition {from_state.value} → {to_state.value} "
                f"for action {action.action_id} "
                f"(type={action.action_type} risk={action.risk_level.value}). "
                f"Allowed from {from_state.value}: "
                f"{[s.value for s in ALLOWED_ACTION_TRANSITIONS.get(from_state, frozenset())]}"
            )

        record = ActionTransitionRecord(
            action_id=action.action_id,
            case_id=action.case_id,
            ticket_id=action.ticket_id,
            client=action.client,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            reason=reason,
            detail=detail or {},
        )

        action.current_state = to_state
        action.updated_at = datetime.now(tz=timezone.utc)

        LOGGER.info(
            "action_transition action_id=%s %s→%s reason=%r actor=%s",
            action.action_id, from_state.value, to_state.value, reason, actor,
        )

        if self._on_transition is not None:
            try:
                self._on_transition(action, record)
            except Exception:
                LOGGER.exception(
                    "action_transition: on_transition callback failed action_id=%s",
                    action.action_id,
                )

        return record

    def safe_transition(
        self,
        action: "ActionRequest",
        to_state: ActionState,
        *,
        reason: str = "",
        actor: str = "system",
        detail: dict | None = None,
    ) -> "ActionTransitionRecord | None":
        """Like transition() but returns None on ActionTransitionError."""
        try:
            return self.transition(action, to_state, reason=reason, actor=actor, detail=detail)
        except ActionTransitionError as exc:
            LOGGER.error("action_transition: blocked — %s", exc)
            return None
