"""
case_engine/investigation/orchestrator/exceptions.py

Sprint 2.46: Orchestrator-specific exceptions.

Dependency direction: stdlib only.
"""
from __future__ import annotations


class OrchestratorError(Exception):
    """Base exception for the Investigation Orchestrator."""


class StageError(OrchestratorError):
    """A pipeline stage encountered an unrecoverable error."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(f"Stage {stage!r} failed: {cause}")
        self.stage = stage
        self.cause = cause


class CancellationError(OrchestratorError):
    """Investigation was cancelled."""

    def __init__(self, reason: str = "") -> None:
        msg = f"Investigation cancelled: {reason}" if reason else "Investigation cancelled"
        super().__init__(msg)
        self.reason = reason


class SessionAlreadyStartedError(OrchestratorError):
    """An attempt was made to start an already-started session."""


class InvalidContextError(OrchestratorError):
    """InvestigationContext is missing required fields."""
