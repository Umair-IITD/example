"""
case_engine/knowledge/matcher.py

Sprint 2.20: SOP Matching Engine.

Per blueprint Section 31 (Knowledge Retrieval Flow):
  Root Cause → Knowledge Search → Relevant SOP

SOPMatcher orchestrates:
  1. Build KnowledgeSearchQuery from root cause data
  2. Call HybridRetriever to score and rank entries
  3. Return (best_match, full_search_result)

Design:
  - Stateless — safe to share across threads
  - Never raises — delegates to HybridRetriever which itself never raises
  - Extracts keywords from root cause category, topic, and recommended action
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from case_engine.knowledge.models import (
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    SOPMatch,
)
from case_engine.knowledge.repository import KnowledgeRepository
from case_engine.knowledge.retriever import HybridRetriever

LOGGER = logging.getLogger(__name__)

# Minimum relevance score to consider a match "found"
_MATCH_THRESHOLD = 0.30


class SOPMatcher:
    """
    Matches investigation root cause to SOPs/knowledge entries.

    Blueprint flow: ROOT_CAUSE → HYBRIDRAG → REASONING
    Blueprint Section 31: Root Cause → Knowledge Search → Relevant SOP

    Usage:
        matcher = SOPMatcher(retriever, repository)
        sop_match, search_result = matcher.match(topic, root_cause_category, recommended_action)
    """

    def __init__(
        self,
        retriever: HybridRetriever,
        repository: KnowledgeRepository,
    ) -> None:
        self._retriever   = retriever
        self._repository  = repository

    def match(
        self,
        topic: str,
        root_cause_category: str | None,
        recommended_action: str | None = None,
        keywords: tuple[str, ...] = (),
    ) -> tuple[SOPMatch | None, KnowledgeSearchResult]:
        """
        Find the best SOP match for the given root cause.

        Returns:
            (SOPMatch | None, KnowledgeSearchResult)
            SOPMatch is None if no entry scores above _MATCH_THRESHOLD.
        """
        query = self._build_query(topic, root_cause_category, recommended_action, keywords)
        result = self._retriever.retrieve(query, self._repository)

        best = result.top_match
        if best is not None and best.relevance_score < _MATCH_THRESHOLD:
            best = None

        LOGGER.info(
            "sop_matcher.match topic=%s root_cause=%s found=%s top_score=%.2f",
            topic,
            root_cause_category,
            best is not None,
            result.top_match.relevance_score if result.top_match else 0.0,
        )
        return best, result

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_query(
        self,
        topic: str,
        root_cause_category: str | None,
        recommended_action: str | None,
        extra_keywords: tuple[str, ...],
    ) -> KnowledgeSearchQuery:
        """Build a KnowledgeSearchQuery from root cause components."""
        keywords = self._extract_keywords(
            topic, root_cause_category, recommended_action
        ) + list(extra_keywords)
        keywords = list(dict.fromkeys(keywords))  # deduplicate, preserve order

        return KnowledgeSearchQuery(
            query_id=str(uuid.uuid4()),
            topic=topic,
            root_cause_category=root_cause_category,
            recommended_action=recommended_action,
            keywords=tuple(keywords[:20]),  # cap at 20 keywords
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def _extract_keywords(
        self,
        topic: str,
        root_cause_category: str | None,
        recommended_action: str | None,
    ) -> list[str]:
        """
        Extract keyword tokens from topic, root cause, and recommended action.

        Splits on underscores to produce individual searchable tokens.
        """
        parts: list[str] = []
        for raw in (topic, root_cause_category, recommended_action):
            if raw:
                parts.extend(raw.lower().split("_"))
        return [p for p in parts if len(p) > 2]  # filter out trivial tokens
