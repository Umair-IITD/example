"""
case_engine/workflows/playbooks/repository.py

Sprint 2.40: Workflow Playbook System — in-memory repository.

WorkflowPlaybookRepository manages all registered WorkflowPlaybook instances.
It is the authoritative store for playbook lookup, resolution, and statistics.

Design:
  - Pure in-memory; stateless across restarts (populated at startup via loader.py)
  - All mutations validate before storing
  - Thread-safety: single-threaded assumption (FastAPI runs handlers sequentially
    per request; background tasks use separate instances)
  - Future: replace with DB-backed repository in Sprint 2.41+

Key invariants:
  - A (playbook_id, version) pair is unique within the repository
  - resolve() ALWAYS returns a playbook (never raises PlaybookNotFoundError)
  - Only ACTIVE + enabled playbooks are returned by resolve()
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.workflows.playbooks.exceptions import (
    DuplicatePlaybookError,
    PlaybookNotFoundError,
)
from case_engine.workflows.playbooks.models import (
    PlaybookStatus,
    RepositoryStats,
    WorkflowPlaybook,
)
from case_engine.workflows.playbooks.validators import PlaybookValidator
from case_engine.workflows.playbooks.versioning import WorkflowVersion

LOGGER = logging.getLogger(__name__)


class WorkflowPlaybookRepository:
    """
    In-memory repository for WorkflowPlaybook instances.

    All playbooks are validated before storage. The repository supports
    multiple versions of the same playbook_id.

    Resolution priority (used by WorkflowPlaybookResolver):
      1. Client-specific + topic (client_scope contains client_id)
      2. Topic-specific, global (empty client_scope)
      3. First active enabled playbook for topic
      4. (Caller handles fallback if none found)
    """

    def __init__(self, validator: PlaybookValidator | None = None) -> None:
        self._store: dict[str, list[WorkflowPlaybook]] = {}
        self._validator = validator or PlaybookValidator()

    # ── Mutation operations ────────────────────────────────────────────────────

    def register(self, playbook: WorkflowPlaybook) -> None:
        """
        Register a new playbook.

        Raises:
            PlaybookValidationError: if playbook fails validation
            DuplicatePlaybookError: if (playbook_id, version) already registered
        """
        self._validator.validate(playbook)

        pid = playbook.playbook_id
        existing = self._store.get(pid, [])
        for pb in existing:
            if pb.version == playbook.version:
                raise DuplicatePlaybookError(pid, str(playbook.version))

        if pid not in self._store:
            self._store[pid] = []
        self._store[pid].append(playbook)
        self._store[pid].sort(key=lambda p: p.version, reverse=True)

        LOGGER.info(
            "playbook_repository.registered playbook_id=%s version=%s topic=%s",
            pid, playbook.version, playbook.topic,
        )

    def replace(self, playbook: WorkflowPlaybook) -> None:
        """
        Register or replace an existing version.

        If the (playbook_id, version) already exists, replaces it.
        If not, registers it. Validates before storing.
        """
        self._validator.validate(playbook)

        pid = playbook.playbook_id
        existing = self._store.get(pid, [])
        new_list = [p for p in existing if p.version != playbook.version]
        new_list.append(playbook)
        new_list.sort(key=lambda p: p.version, reverse=True)
        self._store[pid] = new_list

        LOGGER.info(
            "playbook_repository.replaced playbook_id=%s version=%s",
            pid, playbook.version,
        )

    def remove(self, playbook_id: str, version: WorkflowVersion | None = None) -> bool:
        """
        Remove a playbook.

        If version is None, removes ALL versions of playbook_id.
        Returns True if anything was removed, False otherwise.
        """
        if playbook_id not in self._store:
            return False

        if version is None:
            del self._store[playbook_id]
            return True

        before = len(self._store[playbook_id])
        self._store[playbook_id] = [
            p for p in self._store[playbook_id] if p.version != version
        ]
        after = len(self._store[playbook_id])
        if not self._store[playbook_id]:
            del self._store[playbook_id]
        return before > after

    # ── Read operations ────────────────────────────────────────────────────────

    def get(
        self,
        playbook_id: str,
        version: WorkflowVersion | None = None,
    ) -> WorkflowPlaybook | None:
        """
        Retrieve a playbook by ID and optional version.

        If version is None, returns the latest (highest) version.
        Returns None if not found.
        """
        versions = self._store.get(playbook_id, [])
        if not versions:
            return None
        if version is None:
            return versions[0]  # already sorted descending by version
        for pb in versions:
            if pb.version == version:
                return pb
        return None

    def find(
        self,
        topic: str,
        client_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[WorkflowPlaybook]:
        """
        Find all playbooks matching topic and optional client constraint.

        By default only returns ACTIVE + enabled playbooks.
        Set include_inactive=True to include DRAFT/DEPRECATED/ARCHIVED.

        Results are sorted: client-scoped first, then global; within each group
        sorted by version descending.
        """
        normalised_topic = topic.upper().replace("-", "_")
        results: list[WorkflowPlaybook] = []

        for pb_list in self._store.values():
            for pb in pb_list:
                if pb.topic.upper().replace("-", "_") != normalised_topic:
                    continue
                if not include_inactive and not pb.is_active:
                    continue
                if client_id is not None and not pb.applies_to_client(client_id):
                    continue
                results.append(pb)

        # Sort: client-specific before global, then by version descending
        results.sort(
            key=lambda p: (0 if not p.is_global else 1, p.version),
            reverse=False,
        )
        # version sort descending within each group
        client_specific = sorted(
            [p for p in results if not p.is_global],
            key=lambda p: p.version, reverse=True,
        )
        global_pb = sorted(
            [p for p in results if p.is_global],
            key=lambda p: p.version, reverse=True,
        )
        return client_specific + global_pb

    def list(self, include_inactive: bool = False) -> list[WorkflowPlaybook]:
        """Return all registered playbooks, latest version first per playbook_id."""
        result: list[WorkflowPlaybook] = []
        for pb_list in self._store.values():
            for pb in pb_list:
                if not include_inactive and not pb.is_active:
                    continue
                result.append(pb)
        result.sort(key=lambda p: (p.topic, p.version), reverse=False)
        return result

    def resolve(
        self,
        topic: str,
        client_id: str | None = None,
    ) -> WorkflowPlaybook | None:
        """
        Resolve the best matching playbook for a topic and optional client.

        Resolution priority:
          1. Client-specific active playbook for this topic
          2. Global active playbook for this topic
          3. None (caller must handle fallback)

        Returns None if no active playbook found.
        Never raises PlaybookNotFoundError — that is the resolver's responsibility.
        """
        candidates = self.find(topic, client_id)
        if candidates:
            return candidates[0]
        return None

    def list_topics(self) -> list[str]:
        """Return sorted list of all topics covered by active playbooks."""
        topics: set[str] = set()
        for pb_list in self._store.values():
            for pb in pb_list:
                if pb.is_active:
                    topics.add(pb.topic)
        return sorted(topics)

    def statistics(self) -> RepositoryStats:
        """Return summary statistics for this repository."""
        all_playbooks: list[WorkflowPlaybook] = []
        for pb_list in self._store.values():
            all_playbooks.extend(pb_list)

        topics: set[str] = set()
        active = deprecated = draft = archived = client_scoped = 0

        for pb in all_playbooks:
            topics.add(pb.topic)
            if pb.status == PlaybookStatus.ACTIVE:
                active += 1
            elif pb.status == PlaybookStatus.DEPRECATED:
                deprecated += 1
            elif pb.status == PlaybookStatus.DRAFT:
                draft += 1
            elif pb.status == PlaybookStatus.ARCHIVED:
                archived += 1
            if pb.client_scope:
                client_scoped += 1

        return RepositoryStats(
            total_playbooks=len(all_playbooks),
            active_playbooks=active,
            deprecated_playbooks=deprecated,
            draft_playbooks=draft,
            archived_playbooks=archived,
            topics_covered=tuple(sorted(topics)),
            client_scoped=client_scoped,
        )

    def __len__(self) -> int:
        return sum(len(v) for v in self._store.values())

    def __contains__(self, playbook_id: str) -> bool:
        return playbook_id in self._store
