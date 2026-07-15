"""
case_engine/knowledge/base.py

Sprint 2.38: Knowledge Layer abstract interfaces.

Blueprint Section 6 defines three distinct knowledge stores:
  - SOP Repository: support procedures and resolution rules
  - Knowledge Base: historical fixes, FAQs, engineering guidance
  - Workflow Playbooks: deterministic automation sequences

This module provides the typed query, result, provider, and repository ABCs
that unify access to all three stores. Future stores (vector DBs, document
stores, S3) plug in by implementing these interfaces — downstream code never
changes its import surface.

Dependency direction:
  This module imports ONLY from the standard library.
  All case_engine modules may import from here.
  This module NEVER imports from case_engine sub-packages.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Query ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KnowledgeQuery:
    """
    Structured query for any knowledge provider.

    topic:       Support topic (e.g., "VKYC_SESSION_FAILURE")
    query_text:  Natural-language description of the information needed
    context:     Structured context dict (root cause, evidence, slots)
    client_id:   Tenant filter — None means global / tenant-agnostic
    max_results: Upper bound on returned items
    query_id:    Stable identifier for this query (audit trail)
    created_at:  ISO UTC timestamp
    """
    topic:       str
    query_text:  str
    context:     dict[str, Any] = field(default_factory=dict)
    client_id:   str | None = None
    max_results: int = 5
    query_id:    str = field(default_factory=_new_id)
    created_at:  str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id":    self.query_id,
            "topic":       self.topic,
            "query_text":  self.query_text,
            "client_id":   self.client_id,
            "max_results": self.max_results,
            "created_at":  self.created_at,
        }


# ── Entry ──────────────────────────────────────────────────────────────────────

@dataclass
class KnowledgeEntry:
    """
    A single item returned from a knowledge provider.

    entry_id:  Stable identifier (e.g., SOP ID, KB article ID)
    source:    Provider name that produced this entry
    title:     Human-readable title
    content:   Full text content
    topic:     Topic this entry covers
    relevance: Relevance score 0.0–1.0
    metadata:  Provider-specific key-value metadata
    """
    entry_id:  str
    source:    str
    title:     str
    content:   str
    topic:     str
    relevance: float = 0.0
    metadata:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id":  self.entry_id,
            "source":    self.source,
            "title":     self.title,
            "content":   self.content,
            "topic":     self.topic,
            "relevance": self.relevance,
            "metadata":  self.metadata,
        }


# ── Provider Result ────────────────────────────────────────────────────────────

@dataclass
class KnowledgeProviderResult:
    """
    Typed result envelope from any knowledge provider.

    provider_name: Which provider produced this result
    query_id:      The original query ID (links to KnowledgeQuery)
    entries:       Retrieved entries, sorted by relevance descending
    total_found:   Total matching entries (may exceed len(entries))
    retrieved_at:  ISO timestamp
    """
    provider_name: str
    query_id:      str
    entries:       list[KnowledgeEntry]
    total_found:   int
    retrieved_at:  str = field(default_factory=_now_iso)

    @classmethod
    def empty(cls, provider_name: str, query_id: str) -> "KnowledgeProviderResult":
        """Return an empty result for a provider."""
        return cls(
            provider_name=provider_name,
            query_id=query_id,
            entries=[],
            total_found=0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "query_id":      self.query_id,
            "entry_count":   len(self.entries),
            "total_found":   self.total_found,
            "retrieved_at":  self.retrieved_at,
            "entries":       [e.to_dict() for e in self.entries],
        }


# ── Provider ABC ───────────────────────────────────────────────────────────────

class KnowledgeProvider(ABC):
    """
    Abstract interface for all knowledge providers.

    A KnowledgeProvider answers queries from one knowledge store
    (SOP repository, RAG knowledge base, workflow playbooks, etc.).

    Contract:
    - retrieve() NEVER raises — return empty result on any failure.
    - provider_name is a stable, unique string identifier.
    - is_available() returns False if the provider is not configured.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable identifier, e.g. 'sop_repository', 'hybrid_rag'."""
        ...

    @abstractmethod
    def retrieve(self, query: KnowledgeQuery) -> KnowledgeProviderResult:
        """
        Retrieve knowledge items matching the query.

        Never raises. Returns empty result if nothing found or on error.
        """
        ...

    def is_available(self) -> bool:
        """Return True if this provider is configured and reachable."""
        return True


# ── Repository ABC ─────────────────────────────────────────────────────────────

class KnowledgeRepository(ABC):
    """
    Abstract repository for loading and persisting knowledge artifacts.

    A KnowledgeRepository is the storage backend behind a KnowledgeProvider.
    It handles load, get, list, and (optionally) upsert operations.
    """

    @abstractmethod
    def get_by_id(self, entry_id: str) -> KnowledgeEntry | None:
        """Retrieve a specific entry by its stable ID."""
        ...

    @abstractmethod
    def list_by_topic(self, topic: str) -> list[KnowledgeEntry]:
        """Return all entries relevant to a topic."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Total number of entries in this repository."""
        ...
