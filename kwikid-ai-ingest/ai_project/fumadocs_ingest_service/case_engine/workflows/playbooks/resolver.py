"""
case_engine/workflows/playbooks/resolver.py

Sprint 2.40: Workflow Playbook System — priority-based resolver.

WorkflowPlaybookResolver is the authoritative entry point for resolving
the correct WorkflowPlaybook for a given investigation context.

Resolution priority (blueprint Layer 4 — Workflow Selection):
  1. Client override  — topic + client_id exact match (client_scope contains client_id)
  2. Topic override   — topic match, global playbook (empty client_scope)
  3. Default topic    — first active enabled playbook for any variant of topic
  4. Global default   — minimal investigation playbook (always present)

The resolver NEVER returns None. The global default (minimal investigation)
is the guaranteed fallback. This satisfies the blueprint requirement:
  "Output: Single WorkflowPlaybook — Always deterministic."
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.workflows.playbooks.exceptions import PlaybookNotFoundError
from case_engine.workflows.playbooks.models import WorkflowPlaybook
from case_engine.workflows.playbooks.repository import WorkflowPlaybookRepository

LOGGER = logging.getLogger(__name__)

_MINIMAL_TOPIC = "MINIMAL_INVESTIGATION"


class WorkflowPlaybookResolver:
    """
    Priority-based playbook resolver.

    Constructor:
        repository:        The source of registered playbooks.
        minimal_playbook:  The absolute fallback — returned when no other
                           playbook matches. Must be an enabled, active playbook.

    Usage:
        resolver = WorkflowPlaybookResolver(repo, minimal)
        playbook = resolver.resolve("VKYC_SESSION_FAILURE", client_id="unity_bank")
        # → always returns a WorkflowPlaybook, never raises PlaybookNotFoundError
    """

    def __init__(
        self,
        repository: WorkflowPlaybookRepository,
        minimal_playbook: WorkflowPlaybook,
    ) -> None:
        self._repository      = repository
        self._minimal_playbook = minimal_playbook

    # ── Public API ─────────────────────────────────────────────────────────────

    def resolve(
        self,
        topic: str,
        client_id: str | None = None,
        slots: dict[str, Any] | None = None,
    ) -> WorkflowPlaybook:
        """
        Resolve the best matching playbook. Never returns None.

        Priority:
          1. Client-specific (client_scope contains client_id) + topic match
          2. Global (empty client_scope) + topic match
          3. Minimal investigation playbook (guaranteed fallback)

        Args:
            topic:     Topic string (case-insensitive, hyphen-tolerant)
            client_id: Optional client identifier for client-specific overrides
            slots:     Optional slot values (reserved for future condition evaluation)

        Returns:
            WorkflowPlaybook — the best match. Never None.
        """
        normalised = topic.upper().replace("-", "_")

        # Priority 1 + 2: repository lookup (returns client-specific before global)
        playbook = self._repository.resolve(normalised, client_id)
        if playbook is not None:
            LOGGER.info(
                "playbook_resolver.resolved topic=%s client=%s playbook=%s version=%s",
                normalised, client_id, playbook.playbook_id, playbook.version,
            )
            return playbook

        # Priority 3: check repository for the minimal topic directly
        minimal = self._repository.resolve(_MINIMAL_TOPIC)
        if minimal is not None:
            LOGGER.warning(
                "playbook_resolver.fallback_minimal topic=%s client=%s",
                normalised, client_id,
            )
            return minimal

        # Priority 4: absolute fallback — the pre-loaded minimal playbook
        LOGGER.warning(
            "playbook_resolver.absolute_fallback topic=%s client=%s "
            "— using constructor-provided minimal playbook",
            normalised, client_id,
        )
        return self._minimal_playbook

    def can_resolve(self, topic: str, client_id: str | None = None) -> bool:
        """Return True if the topic has a non-minimal active playbook."""
        normalised = topic.upper().replace("-", "_")
        if normalised == _MINIMAL_TOPIC:
            return False
        return self._repository.resolve(normalised, client_id) is not None

    def list_topics(self) -> list[str]:
        """Return sorted list of all topics with active playbooks."""
        return self._repository.list_topics()

    def resolution_summary(self, topic: str, client_id: str | None = None) -> dict[str, Any]:
        """Return a debug summary of how a topic would be resolved."""
        normalised = topic.upper().replace("-", "_")
        candidates = self._repository.find(normalised, client_id)
        playbook = self.resolve(topic, client_id)
        return {
            "topic":       normalised,
            "client_id":   client_id,
            "candidates":  len(candidates),
            "resolved":    playbook.playbook_id,
            "version":     str(playbook.version),
            "is_minimal":  playbook.playbook_id == self._minimal_playbook.playbook_id,
            "is_global":   playbook.is_global,
        }
