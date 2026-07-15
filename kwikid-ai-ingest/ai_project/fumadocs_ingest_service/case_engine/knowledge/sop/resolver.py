"""
case_engine/knowledge/sop/resolver.py

Sprint 2.41: SOPDocumentResolver — deterministic SOP resolution.

The resolver wraps a SOPDocumentRepository and guarantees that
resolve() NEVER returns None — it always produces a SOPDocument.

Resolution priority:
  1. Topic + client-specific SOP from repository
  2. Topic + global SOP from repository
  3. Absolute fallback SOP (provided at construction time)

Topic normalisation:
  All topics are upper-cased and hyphens are replaced by underscores.
  "vkyc-session-failure" → "VKYC_SESSION_FAILURE"

This class satisfies the existing SOPResolverProtocol in engine.py:
  resolve(topic, context={}) → SOPDocument | None
  (returns SOPDocument always, satisfying the | None typing at the call site)

Distinct from SOPResolver in provider.py which is the KnowledgeProvider adapter.
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.knowledge.sop.models import SOPDocument
from case_engine.knowledge.sop.repository import SOPDocumentRepository

LOGGER = logging.getLogger(__name__)


class SOPDocumentResolver:
    """
    Deterministic SOP resolver that never returns None.

    Constructor:
        repository:       SOPDocumentRepository to look up SOPs from.
        fallback_sop:     SOPDocument returned when no topic match is found.
                          Must be an ACTIVE SOPDocument (typically MINIMAL_INVESTIGATION).
    """

    def __init__(
        self,
        repository: SOPDocumentRepository,
        fallback_sop: SOPDocument,
    ) -> None:
        self._repo     = repository
        self._fallback = fallback_sop

    # ── Public API ─────────────────────────────────────────────────────────────

    def resolve(
        self,
        topic: str,
        context: dict[str, Any] | None = None,
        client_id: str | None = None,
        slots: dict[str, Any] | None = None,
    ) -> SOPDocument:
        """
        Resolve the best SOPDocument for the given topic.

        Arguments:
            topic:     Topic key (normalised to UPPER_SNAKE before lookup).
            context:   Context dict (not used for repository lookup, kept for
                       protocol compatibility with existing SOPResolverProtocol).
            client_id: Client ID for client-specific SOP preference.
            slots:     Slot values (reserved for future contextual resolution).

        Returns:
            A SOPDocument — never None.
        """
        topic_norm = self._normalise(topic)
        try:
            sop = self._repo.resolve(topic_norm, client_id)
            if sop is not None:
                LOGGER.debug(
                    "sop_document_resolver.resolved topic=%s client=%s sop_id=%s",
                    topic_norm, client_id, sop.sop_id,
                )
                return sop
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "sop_document_resolver.repo_error topic=%s error=%s — using fallback",
                topic_norm, exc,
            )

        LOGGER.debug(
            "sop_document_resolver.fallback topic=%s client=%s → %s",
            topic_norm, client_id, self._fallback.sop_id,
        )
        return self._fallback

    def can_resolve(self, topic: str, client_id: str | None = None) -> bool:
        """
        Return True if the repository has an ACTIVE SOP for the given topic.

        Returns False if resolution would fall back to the minimal fallback.
        """
        topic_norm = self._normalise(topic)
        try:
            sop = self._repo.resolve(topic_norm, client_id)
            return sop is not None
        except Exception:
            return False

    def list_topics(self) -> list[str]:
        """Return sorted list of topics with at least one ACTIVE SOP."""
        return self._repo.list_topics()

    def resolution_summary(
        self,
        topic: str,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a dict describing how the topic would resolve."""
        topic_norm = self._normalise(topic)
        sop = self.resolve(topic_norm, client_id=client_id)
        is_fallback = sop.sop_id == self._fallback.sop_id and not self.can_resolve(topic)
        return {
            "topic_requested":  topic,
            "topic_normalised": topic_norm,
            "client_id":        client_id,
            "resolved_sop_id":  sop.sop_id,
            "resolved_topic":   sop.topic,
            "resolved_version": sop.version,
            "is_fallback":      is_fallback,
            "is_client_specific": not sop.is_global(),
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(topic: str) -> str:
        return topic.strip().upper().replace("-", "_").replace(" ", "_")
