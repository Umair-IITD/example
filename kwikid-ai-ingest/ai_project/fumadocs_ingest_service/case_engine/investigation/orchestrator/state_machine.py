"""
case_engine/investigation/orchestrator/state_machine.py

Sprint 2.46: Deterministic execution lifecycle for the Investigation Orchestrator.

States:
  CREATED     — Session created, not yet started.
  PLANNING    — InvestigationPlanner is running.
  COLLECTING  — EvidenceCollector is running.
  KNOWLEDGE   — Knowledge enrichment stage is running.
  ROOT_CAUSE  — RootCauseEngine is running.
  OBSERVATION — ObservationGenerator is running.
  COMPLETED   — All stages completed successfully.
  FAILED      — A stage failed unrecoverably.
  CANCELLED   — Investigation was cancelled or timed out.

Terminal states: COMPLETED, FAILED, CANCELLED.

Valid transitions (deterministic — no invalid transition is ever silently applied):
  CREATED     → PLANNING    | CANCELLED
  PLANNING    → COLLECTING  | FAILED | CANCELLED
  COLLECTING  → KNOWLEDGE   | FAILED | CANCELLED
  KNOWLEDGE   → ROOT_CAUSE  | FAILED | CANCELLED
  ROOT_CAUSE  → OBSERVATION | FAILED | CANCELLED
  OBSERVATION → COMPLETED   | FAILED | CANCELLED
  COMPLETED   → (terminal — no outgoing)
  FAILED      → (terminal — no outgoing)
  CANCELLED   → (terminal — no outgoing)

Dependency direction: stdlib only.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, FrozenSet

LOGGER = logging.getLogger(__name__)


class OrchestratorLifecycleState(str, Enum):
    CREATED     = "CREATED"
    PLANNING    = "PLANNING"
    COLLECTING  = "COLLECTING"
    KNOWLEDGE   = "KNOWLEDGE"
    ROOT_CAUSE  = "ROOT_CAUSE"
    OBSERVATION = "OBSERVATION"
    COMPLETED   = "COMPLETED"
    FAILED      = "FAILED"
    CANCELLED   = "CANCELLED"


_TERMINAL: FrozenSet[OrchestratorLifecycleState] = frozenset({
    OrchestratorLifecycleState.COMPLETED,
    OrchestratorLifecycleState.FAILED,
    OrchestratorLifecycleState.CANCELLED,
})

_VALID_TRANSITIONS: dict[OrchestratorLifecycleState, FrozenSet[OrchestratorLifecycleState]] = {
    OrchestratorLifecycleState.CREATED: frozenset({
        OrchestratorLifecycleState.PLANNING,
        OrchestratorLifecycleState.CANCELLED,
    }),
    OrchestratorLifecycleState.PLANNING: frozenset({
        OrchestratorLifecycleState.COLLECTING,
        OrchestratorLifecycleState.FAILED,
        OrchestratorLifecycleState.CANCELLED,
    }),
    OrchestratorLifecycleState.COLLECTING: frozenset({
        OrchestratorLifecycleState.KNOWLEDGE,
        OrchestratorLifecycleState.FAILED,
        OrchestratorLifecycleState.CANCELLED,
    }),
    OrchestratorLifecycleState.KNOWLEDGE: frozenset({
        OrchestratorLifecycleState.ROOT_CAUSE,
        OrchestratorLifecycleState.FAILED,
        OrchestratorLifecycleState.CANCELLED,
    }),
    OrchestratorLifecycleState.ROOT_CAUSE: frozenset({
        OrchestratorLifecycleState.OBSERVATION,
        OrchestratorLifecycleState.FAILED,
        OrchestratorLifecycleState.CANCELLED,
    }),
    OrchestratorLifecycleState.OBSERVATION: frozenset({
        OrchestratorLifecycleState.COMPLETED,
        OrchestratorLifecycleState.FAILED,
        OrchestratorLifecycleState.CANCELLED,
    }),
    # Terminal — no outgoing transitions
    OrchestratorLifecycleState.COMPLETED:  frozenset(),
    OrchestratorLifecycleState.FAILED:     frozenset(),
    OrchestratorLifecycleState.CANCELLED:  frozenset(),
}


class OrchestratorStateMachine:
    """
    Deterministic lifecycle state machine for one InvestigationSession.

    Thread-safety note: the pipeline executor is single-threaded per session.
    This class does not add its own lock — callers are responsible.
    """

    def __init__(self) -> None:
        self._state = OrchestratorLifecycleState.CREATED
        self._history: list[tuple[OrchestratorLifecycleState, OrchestratorLifecycleState]] = []

    @property
    def state(self) -> OrchestratorLifecycleState:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in _TERMINAL

    def can_transition(self, to: OrchestratorLifecycleState) -> bool:
        return to in _VALID_TRANSITIONS.get(self._state, frozenset())

    def transition(self, to: OrchestratorLifecycleState) -> bool:
        """
        Attempt to transition to `to`.
        Returns True if applied, False if invalid (invalid transition is a no-op).
        """
        if not self.can_transition(to):
            LOGGER.warning(
                "orchestrator_state_machine.invalid_transition %s → %s (ignored)",
                self._state.value, to.value,
            )
            return False
        old = self._state
        self._state = to
        self._history.append((old, to))
        LOGGER.debug(
            "orchestrator_state_machine.transition %s → %s",
            old.value, to.value,
        )
        return True

    def force_terminal(self, state: OrchestratorLifecycleState) -> None:
        """
        Force a terminal state unconditionally.
        Used by error/cancel paths when normal transitions are impossible.
        Raises ValueError if `state` is not a terminal state.
        """
        if state not in _TERMINAL:
            raise ValueError(f"force_terminal: {state.value!r} is not a terminal state")
        old = self._state
        self._state = state
        self._history.append((old, state))
        LOGGER.debug(
            "orchestrator_state_machine.force_terminal %s → %s",
            old.value, state.value,
        )

    def transition_history(self) -> list[tuple[str, str]]:
        """Return a list of (from_state, to_state) string pairs."""
        return [(f.value, t.value) for f, t in self._history]

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_state": self._state.value,
            "is_terminal":   self.is_terminal,
            "history":       self.transition_history(),
        }
