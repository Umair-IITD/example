"""
case_engine/knowledge/repository.py

Sprint 2.20: Knowledge repository abstraction.

Per blueprint flow_diagram.mermaid (Knowledge Layer):
  STACK → INGEST → INDEX → HYBRIDRAG

This module provides:
  KnowledgeRepository  — Protocol defining the repository interface
  InMemoryKnowledgeRepository — Phase-1 implementation (no external DB)

Architecture note:
  The Protocol interface is defined so future implementations can plug in
  vector databases (Pinecone, pgvector, Chroma) without changing callers.
  Phase 1 uses an in-memory dict-based store seeded at startup.

Design:
  - Thread-safe reads (no write contention in Phase 1 — knowledge is loaded once)
  - O(1) lookup by entry_id
  - O(n) filter queries (acceptable for Phase 1 repository size ~500 entries)
  - Never raises
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from case_engine.knowledge.models import KnowledgeEntry, KnowledgeEntryStatus

LOGGER = logging.getLogger(__name__)


# ── Repository Protocol ───────────────────────────────────────────────────────

@runtime_checkable
class KnowledgeRepository(Protocol):
    """
    Abstract repository interface for knowledge entries.

    Implementations must be safe to call from any thread once populated.
    All methods return copies — callers must not mutate returned objects.
    """

    def store(self, entry: KnowledgeEntry) -> None:
        """Persist one entry. Overwrites if entry_id already exists."""
        ...

    def bulk_store(self, entries: list[KnowledgeEntry]) -> int:
        """Persist multiple entries. Returns count stored."""
        ...

    def get_by_id(self, entry_id: str) -> KnowledgeEntry | None:
        """Return entry by ID, or None."""
        ...

    def get_all(self) -> list[KnowledgeEntry]:
        """Return all ACTIVE entries."""
        ...

    def get_by_topic(self, topic: str) -> list[KnowledgeEntry]:
        """Return ACTIVE entries whose topic_keys contain the given topic."""
        ...

    def get_by_root_cause(self, category: str) -> list[KnowledgeEntry]:
        """Return ACTIVE entries whose root_cause_categories contain the category."""
        ...

    def get_by_recommended_action(self, action: str) -> list[KnowledgeEntry]:
        """Return ACTIVE entries whose recommended_actions contain the action."""
        ...

    def count(self) -> int:
        """Return total number of stored entries (all statuses)."""
        ...

    def count_active(self) -> int:
        """Return number of ACTIVE entries."""
        ...

    def clear(self) -> None:
        """Remove all entries (useful for testing)."""
        ...


# ── InMemoryKnowledgeRepository ───────────────────────────────────────────────

class InMemoryKnowledgeRepository:
    """
    Phase-1 in-memory knowledge repository.

    Backed by a plain dict keyed by entry_id. All filter methods iterate
    the dict and apply predicate filtering — acceptable for Phase 1 sizes
    (< 1000 entries).

    Thread safety: reads are safe without locks. Writes (store/bulk_store/clear)
    should be performed at startup before request handling begins.
    """

    def __init__(self) -> None:
        self._store: dict[str, KnowledgeEntry] = {}

    def store(self, entry: KnowledgeEntry) -> None:
        self._store[entry.entry_id] = entry

    def bulk_store(self, entries: list[KnowledgeEntry]) -> int:
        before = len(self._store)
        for entry in entries:
            self._store[entry.entry_id] = entry
        stored = len(self._store) - before
        LOGGER.info(
            "knowledge_repository.bulk_store added=%d total=%d",
            stored, len(self._store),
        )
        return len(entries)

    def get_by_id(self, entry_id: str) -> KnowledgeEntry | None:
        return self._store.get(entry_id)

    def get_all(self) -> list[KnowledgeEntry]:
        return [e for e in self._store.values() if e.status == KnowledgeEntryStatus.ACTIVE]

    def get_by_topic(self, topic: str) -> list[KnowledgeEntry]:
        return [
            e for e in self._store.values()
            if e.status == KnowledgeEntryStatus.ACTIVE and topic in e.topic_keys
        ]

    def get_by_root_cause(self, category: str) -> list[KnowledgeEntry]:
        return [
            e for e in self._store.values()
            if e.status == KnowledgeEntryStatus.ACTIVE and category in e.root_cause_categories
        ]

    def get_by_recommended_action(self, action: str) -> list[KnowledgeEntry]:
        return [
            e for e in self._store.values()
            if e.status == KnowledgeEntryStatus.ACTIVE and action in e.recommended_actions
        ]

    def count(self) -> int:
        return len(self._store)

    def count_active(self) -> int:
        return sum(1 for e in self._store.values() if e.status == KnowledgeEntryStatus.ACTIVE)

    def clear(self) -> None:
        self._store.clear()
