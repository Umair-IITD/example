"""
case_engine/knowledge/sop/provider.py

Sprint 2.38: SOPProvider abstract interface and SOPResolver concrete implementation.

SOPProvider answers KnowledgeQueries from the SOP Repository.
SOPResolver routes a query + context to the most relevant ACTIVE SOPs.

Per blueprint Section 31 — Knowledge Retrieval Flow:
  Root Cause → Knowledge Search → Relevant SOP → Recommended Action

Dependency direction:
  provider.py → repository.py → models.py
  provider.py → case_engine/knowledge/base.py (KnowledgeProvider interface)
  Nothing here imports from case_engine/investigation or case_engine/action_gateway.
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any

from case_engine.knowledge.base import (
    KnowledgeEntry,
    KnowledgeProvider,
    KnowledgeProviderResult,
    KnowledgeQuery,
)
from case_engine.knowledge.sop.models import SOPDocument
from case_engine.knowledge.sop.repository import SOPRepository

LOGGER = logging.getLogger(__name__)

PROVIDER_NAME = "sop_repository"


class SOPProvider(KnowledgeProvider):
    """
    Abstract provider that answers queries from the SOP repository.

    Extends KnowledgeProvider with SOP-specific retrieval that returns
    full SOPDocument objects (not just generic KnowledgeEntry wrappers).
    """

    @abstractmethod
    def find_matching_sops(
        self,
        topic: str,
        context: dict[str, Any],
        client_id: str | None = None,
    ) -> list[SOPDocument]:
        """
        Return all ACTIVE SOPs that match the given topic and context.

        Sorted by specificity: client-specific SOPs before global ones.
        """
        ...


class SOPResolver(SOPProvider):
    """
    Concrete SOPProvider that resolves queries against a SOPRepository.

    Resolution strategy (in order):
    1. Filter by topic — only SOPs for the active topic are candidates.
    2. Filter by client_id — SOPs with applicable_to set must include the client.
    3. Evaluate trigger_conditions against the context dict.
    4. Sort: client-specific first, then by step count descending (more specific SOPs first).

    Never raises. Returns empty result if no matching SOP found.
    """

    def __init__(self, repository: SOPRepository) -> None:
        self._repo = repository

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeProviderResult:
        """Convert matching SOPDocuments to generic KnowledgeEntry results."""
        try:
            sops = self.find_matching_sops(
                topic=query.topic,
                context=query.context,
                client_id=query.client_id,
            )
            entries = [
                self._sop_to_entry(sop, rank)
                for rank, sop in enumerate(sops)
            ]
            limited = entries[: query.max_results]
            return KnowledgeProviderResult(
                provider_name=self.provider_name,
                query_id=query.query_id,
                entries=limited,
                total_found=len(sops),
            )
        except Exception as exc:
            LOGGER.exception(
                "sop_resolver.retrieve failed topic=%s error=%s", query.topic, exc
            )
            return KnowledgeProviderResult.empty(self.provider_name, query.query_id)

    def find_matching_sops(
        self,
        topic: str,
        context: dict[str, Any],
        client_id: str | None = None,
    ) -> list[SOPDocument]:
        """
        Return matching SOPs for topic + context + client.

        Sorted: client-specific first, then by step count descending.
        """
        candidates = self._repo.get_by_topic(topic)

        if client_id:
            candidates = [s for s in candidates if s.applies_to_client(client_id)]

        matching = [s for s in candidates if s.matches_context(context)]

        matching.sort(
            key=lambda s: (len(s.applicable_to) == 0, -len(s.steps))
        )
        return matching

    def is_available(self) -> bool:
        """Always available — may return 0 results if no SOPs are registered."""
        return True

    @staticmethod
    def _sop_to_entry(sop: SOPDocument, rank: int) -> KnowledgeEntry:
        """Convert a SOPDocument to a generic KnowledgeEntry for the base interface."""
        content_parts = []
        if sop.description:
            content_parts.append(sop.description)
        for step in sop.steps:
            content_parts.append(
                f"Step {step.step_number}: {step.title}. {step.instruction}"
            )
        return KnowledgeEntry(
            entry_id=sop.sop_id,
            source=PROVIDER_NAME,
            title=sop.title,
            content="\n".join(content_parts),
            topic=sop.topic,
            relevance=max(0.0, 1.0 - rank * 0.1),
            metadata={
                "version":       sop.version,
                "tags":          list(sop.tags),
                "applicable_to": list(sop.applicable_to),
                "step_count":    len(sop.steps),
            },
        )
