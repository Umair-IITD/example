"""
case_engine/workflows/repository.py

Sprint 2.38: WorkflowRepository abstract interface and WorkflowResolver.

WorkflowRepository provides the abstract storage layer for WorkflowDefinitions.
WorkflowResolver routes a topic + tenant context to the most appropriate playbook.

The existing PlaybookRegistry (playbook_registry.py) already implements the
WorkflowRepository interface — its `get()`, `get_by_id()`, and `list_all()` methods
satisfy the abstract method requirements without modification.

WorkflowResolver adds typed resolution logic on top, including tenant
workflow_overrides support.

Dependency direction:
  repository.py → case_engine/workflows/models.py (WorkflowDefinition)
  PlaybookRegistry satisfies WorkflowRepository (structural duck typing via ABC).
  Nothing here imports from case_engine/investigation or case_engine/knowledge.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from case_engine.workflows.models import WorkflowDefinition

LOGGER = logging.getLogger(__name__)


class WorkflowRepository(ABC):
    """
    Abstract storage layer for WorkflowDefinition (playbook) objects.

    The existing PlaybookRegistry satisfies this interface:
      - PlaybookRegistry.get(topic)          → WorkflowDefinition | None
      - PlaybookRegistry.get_by_id(id)       → WorkflowDefinition | None
      - PlaybookRegistry.list_all()          → list[WorkflowDefinition]

    Concrete method `get_by_topic()` delegates to `get()` so both naming
    conventions work. Concrete `count()` delegates to `list_all()`.

    Future: database-backed registry for dynamic playbook updates (Sprint 2.X).
    """

    @abstractmethod
    def get(self, topic: str) -> WorkflowDefinition | None:
        """Return the active WorkflowDefinition for the given topic, or None."""
        ...

    @abstractmethod
    def get_by_id(self, workflow_id: str) -> WorkflowDefinition | None:
        """Return the WorkflowDefinition with the given workflow_id, or None."""
        ...

    @abstractmethod
    def list_all(self) -> list[WorkflowDefinition]:
        """Return all registered WorkflowDefinitions."""
        ...

    def get_by_topic(self, topic: str) -> WorkflowDefinition | None:
        """Alias for get() — both naming conventions are supported."""
        return self.get(topic)

    def count(self) -> int:
        """Total number of registered workflows."""
        return len(self.list_all())

    def available_topics(self) -> list[str]:
        """Return the list of topics covered by registered workflows."""
        return [w.topic for w in self.list_all()]


class WorkflowResolver:
    """
    Routes a topic + tenant context to the correct WorkflowDefinition.

    Resolution strategy:
    1. If tenant workflow_overrides contains a "workflow_id", use that playbook.
    2. Otherwise, resolve by topic.
    3. Return None if no matching workflow exists (caller must escalate).

    WorkflowResolver wraps a WorkflowRepository and is the single entry point
    for workflow selection in the pipeline. The WorkflowEngine receives a
    WorkflowDefinition from the resolver; it never calls the repository directly.

    Never raises. Returns None if no workflow found.
    """

    def __init__(self, repository: WorkflowRepository) -> None:
        self._repo = repository

    def resolve(
        self,
        topic: str,
        workflow_overrides: dict[str, Any] | None = None,
    ) -> WorkflowDefinition | None:
        """
        Resolve the best WorkflowDefinition for a topic.

        Args:
            topic:              Classification topic (e.g., "VKYC_SESSION_FAILURE")
            workflow_overrides: Optional tenant-specific override dict.
                                If it contains "workflow_id", that playbook is used
                                instead of the default topic lookup.

        Returns:
            WorkflowDefinition | None — None means no automation path is available.
        """
        try:
            return self._resolve(topic, workflow_overrides or {})
        except Exception as exc:
            LOGGER.exception(
                "workflow_resolver.resolve failed topic=%s error=%s", topic, exc
            )
            return None

    def _resolve(
        self,
        topic: str,
        workflow_overrides: dict[str, Any],
    ) -> WorkflowDefinition | None:
        override_id = workflow_overrides.get("workflow_id")
        if override_id:
            workflow = self._repo.get_by_id(str(override_id))
            if workflow is not None:
                LOGGER.debug(
                    "workflow_resolver.override_applied topic=%s override_id=%s",
                    topic, override_id,
                )
                return workflow
            LOGGER.warning(
                "workflow_resolver: override workflow_id=%s not found, "
                "falling back to topic=%s",
                override_id, topic,
            )

        workflow = self._repo.get(topic)
        if workflow is not None:
            LOGGER.debug(
                "workflow_resolver.resolved topic=%s workflow_id=%s version=%s",
                topic, workflow.workflow_id, workflow.version,
            )
        else:
            LOGGER.warning(
                "workflow_resolver: no workflow found for topic=%s "
                "(available: %s)",
                topic, self._repo.available_topics(),
            )
        return workflow

    def available_topics(self) -> list[str]:
        """Return the list of topics for which a workflow is registered."""
        return self._repo.available_topics()
