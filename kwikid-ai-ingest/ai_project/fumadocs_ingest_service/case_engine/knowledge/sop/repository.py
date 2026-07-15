"""
case_engine/knowledge/sop/repository.py

Sprint 2.38 (original) + Sprint 2.41 (expanded): SOP storage layer.

Sprint 2.38: SOPRepository (ABC) + SOPRegistry (simple in-memory dict).
Sprint 2.41: SOPDocumentRepository — production-grade versioned repository.

SOPDocumentRepository adds:
  - (sop_id, version) keyed storage to support multiple versions of a SOP.
  - Priority-sorted resolution: client-specific > global > first active.
  - DuplicateSOPError on conflicting (sop_id, version) registrations.
  - replace() for intentional overwrites.
  - statistics() returning a RepositorySOPStats dataclass.
  - __len__ and __contains__ for Pythonic interface.

Dependency direction:
  repository.py → models.py, exceptions.py, validators.py (same package)
  Nothing in case_engine/investigation or case_engine/workflows imports from here.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from case_engine.knowledge.sop.exceptions import DuplicateSOPError
from case_engine.knowledge.sop.models import SOPDocument, SOPStatus
from case_engine.knowledge.sop.validators import SOPValidator

LOGGER = logging.getLogger(__name__)


# ── Sprint 2.38: Abstract + Simple Registry ────────────────────────────────────

class SOPRepository(ABC):
    """
    Abstract repository for SOP documents.

    Implementations may store SOPs in memory, a database, S3, or a document
    store. All methods are synchronous — async wrappers are the caller's
    responsibility (asyncio.to_thread for blocking I/O).
    """

    @abstractmethod
    def get_by_id(self, sop_id: str) -> SOPDocument | None:
        """Return the SOP with the given ID, or None if not found."""
        ...

    @abstractmethod
    def get_by_topic(self, topic: str) -> list[SOPDocument]:
        """Return all ACTIVE SOPs that cover the given topic."""
        ...

    @abstractmethod
    def get_all_active(self) -> list[SOPDocument]:
        """Return all SOPs in ACTIVE status."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Total number of registered SOPs (any status)."""
        ...


class SOPRegistry(SOPRepository):
    """
    In-memory SOPRepository (Sprint 2.38).

    SOPs are registered at startup (typically by ProductionRuntime assembly).
    This is the production implementation until a persistent store is required.

    Thread-safe for concurrent reads. Not designed for concurrent writes —
    register all SOPs before serving requests.
    """

    def __init__(self) -> None:
        self._sops: dict[str, SOPDocument] = {}

    def register(self, sop: SOPDocument) -> None:
        """
        Register a SOP document.

        Overwrites any existing SOP with the same sop_id. Use this to update
        a SOP to a new version.
        """
        self._sops[sop.sop_id] = sop
        LOGGER.debug(
            "sop_registry.registered sop_id=%s topic=%s version=%s status=%s",
            sop.sop_id, sop.topic, sop.version, sop.status.value,
        )

    def register_many(self, sops: list[SOPDocument]) -> None:
        """Register multiple SOPs atomically."""
        for sop in sops:
            self.register(sop)

    def get_by_id(self, sop_id: str) -> SOPDocument | None:
        return self._sops.get(sop_id)

    def get_by_topic(self, topic: str) -> list[SOPDocument]:
        """Return all ACTIVE SOPs for the given topic, sorted by sop_id."""
        return sorted(
            [s for s in self._sops.values() if s.topic == topic and s.is_active()],
            key=lambda s: s.sop_id,
        )

    def get_all_active(self) -> list[SOPDocument]:
        """Return all ACTIVE SOPs, sorted by topic then sop_id."""
        return sorted(
            [s for s in self._sops.values() if s.is_active()],
            key=lambda s: (s.topic, s.sop_id),
        )

    def count(self) -> int:
        return len(self._sops)

    def all_topics(self) -> set[str]:
        """Return the set of topics covered by at least one ACTIVE SOP."""
        return {s.topic for s in self._sops.values() if s.is_active()}

    def summary(self) -> dict[str, int]:
        """Return count of SOPs grouped by status."""
        counts: dict[str, int] = {status.value: 0 for status in SOPStatus}
        for sop in self._sops.values():
            counts[sop.status.value] += 1
        return counts


# ── Sprint 2.41: Production Repository ────────────────────────────────────────

@dataclass(frozen=True)
class RepositorySOPStats:
    """Statistics snapshot from a SOPDocumentRepository."""
    total_sops:        int
    active_sops:       int
    deprecated_sops:   int
    archived_sops:     int
    draft_sops:        int
    topic_count:       int
    client_count:      int
    version_count:     int
    global_sops:       int
    client_scoped_sops: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_sops":         self.total_sops,
            "active_sops":        self.active_sops,
            "deprecated_sops":    self.deprecated_sops,
            "archived_sops":      self.archived_sops,
            "draft_sops":         self.draft_sops,
            "topic_count":        self.topic_count,
            "client_count":       self.client_count,
            "version_count":      self.version_count,
            "global_sops":        self.global_sops,
            "client_scoped_sops": self.client_scoped_sops,
        }


class SOPDocumentRepository:
    """
    Production-grade, versioned SOP repository.

    Store key: (sop_id, version_str) → SOPDocument.
    Two secondary indexes maintained for fast lookup:
      _by_id:    sop_id → list[SOPDocument]  (sorted newest-first by version str)
      _by_topic: topic  → list[SOPDocument]  (sorted: client-specific first)

    Priority resolution in find() and resolve():
      1. Client-specific SOPs (applicable_to or client_scope includes client_id)
      2. Global SOPs (applicable_to and client_scope both empty)
      3. Version ordering: higher version string preferred within same scope

    Raises DuplicateSOPError if (sop_id, version) already exists and
    replace=False (which is the default for register()).
    """

    def __init__(self, validator: SOPValidator | None = None) -> None:
        self._store: dict[tuple[str, str], SOPDocument] = {}
        self._validator = validator or SOPValidator()

    # ── Registration ───────────────────────────────────────────────────────────

    def register(self, sop: SOPDocument) -> None:
        """
        Register a SOPDocument.

        Raises:
            DuplicateSOPError if (sop_id, version) already exists.
        """
        key = (sop.sop_id, sop.version)
        if key in self._store:
            raise DuplicateSOPError(
                f"SOP {sop.sop_id!r} version {sop.version!r} already registered. "
                f"Use replace() to overwrite."
            )
        self._store[key] = sop
        LOGGER.debug(
            "sop_document_repository.registered sop_id=%s version=%s topic=%s",
            sop.sop_id, sop.version, sop.topic,
        )

    def replace(self, sop: SOPDocument) -> None:
        """
        Register or overwrite a SOPDocument without raising DuplicateSOPError.
        """
        self._store[(sop.sop_id, sop.version)] = sop
        LOGGER.debug(
            "sop_document_repository.replaced sop_id=%s version=%s topic=%s",
            sop.sop_id, sop.version, sop.topic,
        )

    def remove(self, sop_id: str, version: str | None = None) -> bool:
        """
        Remove a SOPDocument.

        If version is None, removes ALL versions of sop_id.
        Returns True if at least one record was removed, False otherwise.
        """
        if version is not None:
            key = (sop_id, version)
            if key in self._store:
                del self._store[key]
                return True
            return False

        keys_to_remove = [k for k in self._store if k[0] == sop_id]
        if not keys_to_remove:
            return False
        for key in keys_to_remove:
            del self._store[key]
        return True

    # ── Retrieval ──────────────────────────────────────────────────────────────

    def get(self, sop_id: str, version: str | None = None) -> SOPDocument | None:
        """
        Return the SOPDocument with the given sop_id.

        If version is given, return that specific version.
        If version is None, return the most recently registered version
        (highest version string, lexicographically — caller should use
        SOPSemanticVersion.parse for semantic ordering if needed).
        """
        if version is not None:
            return self._store.get((sop_id, version))

        matching = sorted(
            [v for (sid, ver), v in self._store.items() if sid == sop_id],
            key=lambda s: s.version,
            reverse=True,
        )
        return matching[0] if matching else None

    def find(
        self,
        topic: str,
        client_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[SOPDocument]:
        """
        Return all SOPs matching topic (and optionally client_id).

        Sort order: client-specific first, then by version (newest first).
        Inactive SOPs are excluded by default (include_inactive=False).
        """
        topic_norm = topic.strip().upper().replace("-", "_")
        candidates = [
            sop for sop in self._store.values()
            if sop.topic.upper().replace("-", "_") == topic_norm
        ]

        if not include_inactive:
            candidates = [s for s in candidates if s.is_active()]

        if client_id is not None:
            candidates = [s for s in candidates if s.applies_to_client(client_id)]

        candidates.sort(
            key=lambda s: (
                0 if not s.is_global() else 1,
                s.version,
            ),
            reverse=False,
        )
        candidates.sort(key=lambda s: s.version, reverse=True)
        candidates.sort(key=lambda s: 0 if not s.is_global() else 1)

        return candidates

    def resolve(
        self,
        topic: str,
        client_id: str | None = None,
    ) -> SOPDocument | None:
        """
        Return the highest-priority ACTIVE SOP for the given topic.

        Priority: client-specific > global.
        Returns None if no matching SOP found.
        """
        candidates = self.find(topic, client_id, include_inactive=False)
        return candidates[0] if candidates else None

    def list_topics(self) -> list[str]:
        """Return sorted list of topics covered by at least one ACTIVE SOP."""
        return sorted({
            sop.topic for sop in self._store.values() if sop.is_active()
        })

    def list_clients(self) -> list[str]:
        """Return sorted list of unique client IDs that have client-specific SOPs."""
        clients: set[str] = set()
        for sop in self._store.values():
            clients.update(sop.applicable_to)
            clients.update(sop.client_scope)
        return sorted(clients)

    def statistics(self) -> RepositorySOPStats:
        """Return a statistics snapshot of the current repository state."""
        all_sops = list(self._store.values())
        status_counts = {s: 0 for s in SOPStatus}
        for sop in all_sops:
            status_counts[sop.status] += 1

        return RepositorySOPStats(
            total_sops=len(all_sops),
            active_sops=status_counts[SOPStatus.ACTIVE],
            deprecated_sops=status_counts[SOPStatus.DEPRECATED],
            archived_sops=status_counts[SOPStatus.ARCHIVED],
            draft_sops=status_counts[SOPStatus.DRAFT],
            topic_count=len({s.topic for s in all_sops if s.is_active()}),
            client_count=len(self.list_clients()),
            version_count=len(self._store),
            global_sops=sum(1 for s in all_sops if s.is_global()),
            client_scoped_sops=sum(1 for s in all_sops if not s.is_global()),
        )

    # ── Pythonic interface ─────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, sop_id: str) -> bool:
        return any(k[0] == sop_id for k in self._store)
