"""
case_engine/state_machine.py

Deterministic case state machine.

Rules enforced here:
- Only allowed transitions (per ALLOWED_TRANSITIONS table) execute.
- Terminal states accept no further transitions.
- Every transition fires the on_transition callback for audit logging.
- No LLM involvement. No async. No side effects beyond the callback.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from case_engine.case_state import ALLOWED_TRANSITIONS, TERMINAL_STATES, CaseState
from case_engine.models import Case, CaseTransition

LOGGER = logging.getLogger(__name__)


class TransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""


class CaseStateMachine:
    """
    Validates and applies state transitions on Case objects.

    The machine is a pure function — it does not persist anything.
    Persistence and audit logging are handled by callers via the
    on_transition callback.
    """

    def __init__(
        self,
        on_transition: Callable[[Case, CaseTransition], None] | None = None,
    ) -> None:
        self._on_transition = on_transition

    def can_transition(self, from_state: CaseState, to_state: CaseState) -> bool:
        """Return True if the transition is allowed by the rules table."""
        if from_state in TERMINAL_STATES:
            return False
        return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())

    def is_terminal(self, state: CaseState) -> bool:
        return state in TERMINAL_STATES

    def transition(
        self,
        case: Case,
        to_state: CaseState,
        *,
        reason: str = "",
        actor: str = "system",
    ) -> CaseTransition:
        """
        Apply a transition to the case.

        Mutates case.current_state and case.updated_at in-place.
        Fires on_transition callback (for persistence + audit).
        Returns the CaseTransition record.

        Raises TransitionError if the transition is not allowed.
        """
        from_state = case.current_state

        if not self.can_transition(from_state, to_state):
            raise TransitionError(
                f"Illegal transition {from_state.value} → {to_state.value} "
                f"for case {case.case_id} (ticket {case.ticket_id}). "
                f"Allowed from {from_state.value}: "
                f"{[s.value for s in ALLOWED_TRANSITIONS.get(from_state, frozenset())]}"
            )

        transition = CaseTransition(
            case_id=case.case_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            actor=actor,
        )

        # Mutate case
        case.current_state = to_state
        case.updated_at = datetime.now(tz=timezone.utc)
        if to_state in TERMINAL_STATES:
            case.closed_at = case.updated_at

        LOGGER.info(
            "case_transition case_id=%s ticket=%s %s→%s reason=%r actor=%s",
            case.case_id, case.ticket_id,
            from_state.value, to_state.value,
            reason, actor,
        )

        if self._on_transition is not None:
            try:
                self._on_transition(case, transition)
            except Exception:
                LOGGER.exception(
                    "case_transition: on_transition callback failed "
                    "case_id=%s — audit entry may be missing",
                    case.case_id,
                )

        return transition

    def safe_transition(
        self,
        case: Case,
        to_state: CaseState,
        *,
        reason: str = "",
        actor: str = "system",
    ) -> CaseTransition | None:
        """
        Like transition() but swallows TransitionError and returns None on failure.

        Use this when a transition failure should not crash the calling flow.
        The error is always logged.
        """
        try:
            return self.transition(case, to_state, reason=reason, actor=actor)
        except TransitionError as exc:
            LOGGER.error("case_transition: blocked — %s", exc)
            return None
