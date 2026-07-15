"""
case_engine/workflows/playbooks/exceptions.py

Sprint 2.40: Workflow Playbook System — exception hierarchy.

All exceptions are subclasses of PlaybookError for easy catching.
"""
from __future__ import annotations


class PlaybookError(Exception):
    """Base exception for all Workflow Playbook errors."""


class PlaybookNotFoundError(PlaybookError):
    """Raised when no playbook matches the resolution criteria."""

    def __init__(self, topic: str, client_id: str | None = None) -> None:
        self.topic     = topic
        self.client_id = client_id
        detail = f"topic={topic!r}"
        if client_id:
            detail += f", client_id={client_id!r}"
        super().__init__(f"No playbook found: {detail}")


class PlaybookValidationError(PlaybookError):
    """Raised when a WorkflowPlaybook fails structural or logical validation."""

    def __init__(self, playbook_id: str, reason: str) -> None:
        self.playbook_id = playbook_id
        self.reason      = reason
        super().__init__(f"Playbook {playbook_id!r} validation failed: {reason}")


class PlaybookGraphCycleError(PlaybookValidationError):
    """Raised when the PlaybookGraph contains a dependency cycle."""

    def __init__(self, playbook_id: str, cycle_path: list[str]) -> None:
        self.cycle_path = cycle_path
        reason = f"cycle detected: {' → '.join(cycle_path)}"
        super().__init__(playbook_id, reason)


class DuplicatePlaybookError(PlaybookError):
    """Raised when a playbook with the same (id, version) already exists."""

    def __init__(self, playbook_id: str, version: str) -> None:
        self.playbook_id = playbook_id
        self.version     = version
        super().__init__(f"Playbook {playbook_id!r} version {version!r} already registered")


class PlaybookVersionConflictError(PlaybookError):
    """Raised when a version replacement violates ordering constraints."""

    def __init__(self, playbook_id: str, existing: str, incoming: str) -> None:
        self.playbook_id = playbook_id
        self.existing    = existing
        self.incoming    = incoming
        super().__init__(
            f"Playbook {playbook_id!r}: cannot replace version {existing!r} with {incoming!r}"
        )


class PlaybookRegistryError(PlaybookError):
    """Raised when the playbook registry is in an inconsistent state."""
