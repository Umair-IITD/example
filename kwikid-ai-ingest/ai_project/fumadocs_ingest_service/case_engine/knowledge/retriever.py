"""
case_engine/knowledge/retriever.py

Sprint 2.20: Hybrid Retrieval Engine.

Per blueprint flow_diagram.mermaid: HYBRIDRAG node.
Per blueprint Section 31: Knowledge Retrieval Flow.

Phase 1: Deterministic keyword + category matching.
Architecture is prepared for future BM25 + vector similarity upgrade.

Scoring model (deterministic, no LLM):
  root_cause_category exact match  : +0.50
  topic_key match                  : +0.30
  recommended_action match         : +0.20
  keyword overlap (per keyword)    : +0.05 (max +0.20 total)
  accepted_answer bonus            : +0.05
  vote score bonus                 : min(score / 200, 0.05)

Max possible score: ~1.30 (clamped to 1.0)

Design:
  - Stateless — no mutable state; safe to share across threads
  - Never raises — returns empty result on exception
  - top_k is applied after scoring, returning highest-scored entries
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    SOPMatch,
)
from case_engine.knowledge.repository import InMemoryKnowledgeRepository, KnowledgeRepository

LOGGER = logging.getLogger(__name__)

_DEFAULT_TOP_K = 5


class HybridRetriever:
    """
    Hybrid retrieval engine (blueprint HYBRIDRAG node).

    Phase 1 implementation: deterministic multi-signal scoring.
    Future phases will add BM25 full-text and vector similarity components.

    Score components:
      1. Category match   — does the entry address the same root cause?
      2. Topic match      — does the entry cover the same ticket topic?
      3. Action match     — does the entry recommend the same remediation?
      4. Keyword overlap  — do query keywords appear in title/body/tags?
      5. Answer quality   — bonus for accepted answers and high-vote entries
    """

    def retrieve(
        self,
        query: KnowledgeSearchQuery,
        repository: KnowledgeRepository,
        top_k: int = _DEFAULT_TOP_K,
    ) -> KnowledgeSearchResult:
        """
        Retrieve the top_k most relevant entries for the given query.

        Never raises — returns an empty KnowledgeSearchResult on any error.
        """
        try:
            return self._retrieve(query, repository, top_k)
        except Exception:
            LOGGER.exception(
                "hybrid_retriever.error topic=%s root_cause=%s",
                query.topic, query.root_cause_category,
            )
            return self._empty_result(query)

    # ── Private ───────────────────────────────────────────────────────────────

    def _retrieve(
        self,
        query: KnowledgeSearchQuery,
        repository: KnowledgeRepository,
        top_k: int,
    ) -> KnowledgeSearchResult:
        candidates = repository.get_all()
        total_found = len(candidates)

        if not candidates:
            return self._empty_result(query)

        scored: list[tuple[float, KnowledgeEntry, list[str]]] = []
        for entry in candidates:
            score, reasons = self._score_entry(entry, query)
            if score > 0.0:
                scored.append((score, entry, reasons))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        matches = tuple(
            SOPMatch(
                match_id=str(uuid.uuid4()),
                entry=entry,
                relevance_score=min(score, 1.0),
                match_reason="; ".join(reasons),
                matched_on=tuple(reasons),
            )
            for score, entry, reasons in top
        )

        top_match = matches[0] if matches else None

        return KnowledgeSearchResult(
            result_id=str(uuid.uuid4()),
            query=query,
            matches=matches,
            top_match=top_match,
            total_found=total_found,
            searched_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def _score_entry(
        self,
        entry: KnowledgeEntry,
        query: KnowledgeSearchQuery,
    ) -> tuple[float, list[str]]:
        """
        Compute relevance score for one entry against the query.

        Returns (score, list_of_match_reasons).
        """
        score = 0.0
        reasons: list[str] = []

        # 1. Root cause category match (+0.50)
        if (
            query.root_cause_category
            and query.root_cause_category in entry.root_cause_categories
        ):
            score += 0.50
            reasons.append(f"root_cause_category:{query.root_cause_category}")

        # 2. Topic key match (+0.30)
        if query.topic and query.topic in entry.topic_keys:
            score += 0.30
            reasons.append(f"topic_key:{query.topic}")

        # 3. Recommended action match (+0.20)
        if (
            query.recommended_action
            and query.recommended_action in entry.recommended_actions
        ):
            score += 0.20
            reasons.append(f"recommended_action:{query.recommended_action}")

        # 4. Keyword overlap (+0.05 per keyword, max +0.20)
        keyword_score = 0.0
        matched_keywords: list[str] = []
        searchable = (entry.title + " " + entry.body + " " + " ".join(entry.tags)).lower()
        for kw in query.keywords:
            if kw.lower() in searchable:
                keyword_score = min(keyword_score + 0.05, 0.20)
                matched_keywords.append(kw)
        if keyword_score > 0:
            score += keyword_score
            reasons.append(f"keywords:{','.join(matched_keywords[:5])}")

        # 5. Quality bonuses
        if entry.accepted_answer:
            score += 0.05
            reasons.append("accepted_answer")
        vote_bonus = min(entry.score / 200.0, 0.05)
        if vote_bonus > 0:
            score += vote_bonus

        return score, reasons

    def _empty_result(self, query: KnowledgeSearchQuery) -> KnowledgeSearchResult:
        return KnowledgeSearchResult(
            result_id=str(uuid.uuid4()),
            query=query,
            matches=(),
            top_match=None,
            total_found=0,
            searched_at=datetime.now(tz=timezone.utc).isoformat(),
        )
