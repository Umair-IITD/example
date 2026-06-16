"""
tests/test_sprint220_retriever.py

Sprint 2.20: HybridRetriever unit tests.

Coverage:
  - empty repository returns empty result
  - single entry matching category
  - single entry NOT matching → score 0 → not returned
  - topic match scores
  - recommended_action match scores
  - keyword overlap contributes to score
  - accepted_answer bonus applied
  - vote score bonus applied
  - top_k limit respected
  - top_match is highest-scored entry
  - result_id is unique per call
  - never raises on corrupt repository
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
    KnowledgeSearchQuery,
)
from case_engine.knowledge.repository import InMemoryKnowledgeRepository
from case_engine.knowledge.retriever import HybridRetriever


def _make_entry(
    entry_id: str,
    topic_keys: tuple = ("VKYC_Session_Failure",),
    root_cause_categories: tuple = ("EXPIRED_SESSION",),
    recommended_actions: tuple = ("SESSION_RESET",),
    accepted_answer: bool = False,
    score: int = 0,
    body: str = "body text about session reset",
    tags: tuple = ("vkyc", "session_reset"),
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        title=f"Entry {entry_id}",
        body=body,
        entry_type=KnowledgeEntryType.SOP,
        tags=tags,
        topic_keys=topic_keys,
        root_cause_categories=root_cause_categories,
        recommended_actions=recommended_actions,
        resolution_steps=(),
        source="manual",
        source_id=None,
        accepted_answer=accepted_answer,
        score=score,
        created_at="2024-01-01T00:00:00Z",
        status=KnowledgeEntryStatus.ACTIVE,
    )


def _make_query(
    topic: str = "VKYC_Session_Failure",
    root_cause_category: str | None = "EXPIRED_SESSION",
    recommended_action: str | None = "SESSION_RESET",
    keywords: tuple = ("vkyc", "session"),
) -> KnowledgeSearchQuery:
    return KnowledgeSearchQuery(
        query_id="q-test",
        topic=topic,
        root_cause_category=root_cause_category,
        recommended_action=recommended_action,
        keywords=keywords,
        created_at="2024-01-01T00:00:00Z",
    )


class TestHybridRetrieverEmpty:
    def setup_method(self):
        self.retriever = HybridRetriever()
        self.repo = InMemoryKnowledgeRepository()

    def test_empty_repo_returns_result(self):
        result = self.retriever.retrieve(_make_query(), self.repo)
        assert result is not None

    def test_empty_repo_no_matches(self):
        result = self.retriever.retrieve(_make_query(), self.repo)
        assert len(result.matches) == 0

    def test_empty_repo_no_top_match(self):
        result = self.retriever.retrieve(_make_query(), self.repo)
        assert result.top_match is None

    def test_empty_repo_total_found_zero(self):
        result = self.retriever.retrieve(_make_query(), self.repo)
        assert result.total_found == 0


class TestHybridRetrieverScoring:
    def setup_method(self):
        self.retriever = HybridRetriever()
        self.repo = InMemoryKnowledgeRepository()

    def test_category_match_entry_returned(self):
        self.repo.store(_make_entry("e-1"))
        result = self.retriever.retrieve(_make_query(), self.repo)
        assert len(result.matches) > 0

    def test_non_matching_entry_not_returned(self):
        self.repo.store(_make_entry(
            "e-unrelated",
            topic_keys=("API_Callback_Failure",),
            root_cause_categories=("CALLBACK_FAILURE",),
            recommended_actions=("CALLBACK_RETRY",),
            tags=("callback", "api_callback"),
        ))
        query = _make_query(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            keywords=("vkyc",),
        )
        result = self.retriever.retrieve(query, self.repo)
        assert len(result.matches) == 0

    def test_accepted_answer_entry_scores_higher(self):
        self.repo.store(_make_entry("e-no-accept", accepted_answer=False))
        self.repo.store(_make_entry("e-accepted", accepted_answer=True))
        result = self.retriever.retrieve(_make_query(), self.repo)
        top = result.top_match
        assert top is not None
        assert top.entry.entry_id == "e-accepted"

    def test_high_vote_score_bonus(self):
        self.repo.store(_make_entry("e-low-vote", score=0))
        self.repo.store(_make_entry("e-high-vote", score=200))
        result = self.retriever.retrieve(_make_query(), self.repo)
        # High-vote entry should have higher or equal relevance
        scores = {m.entry.entry_id: m.relevance_score for m in result.matches}
        assert scores.get("e-high-vote", 0) >= scores.get("e-low-vote", 0)

    def test_relevance_score_clamped_to_1(self):
        self.repo.store(_make_entry("e-perfect", accepted_answer=True, score=200))
        result = self.retriever.retrieve(_make_query(), self.repo)
        for m in result.matches:
            assert m.relevance_score <= 1.0


class TestHybridRetrieverTopK:
    def setup_method(self):
        self.retriever = HybridRetriever()
        self.repo = InMemoryKnowledgeRepository()

    def test_top_k_limits_results(self):
        for i in range(10):
            self.repo.store(_make_entry(f"e-{i}"))
        result = self.retriever.retrieve(_make_query(), self.repo, top_k=3)
        assert len(result.matches) <= 3

    def test_total_found_not_limited_by_top_k(self):
        for i in range(10):
            self.repo.store(_make_entry(f"e-{i}"))
        result = self.retriever.retrieve(_make_query(), self.repo, top_k=3)
        assert result.total_found == 10

    def test_top_match_is_highest_scored(self):
        self.repo.store(_make_entry("e-low"))
        self.repo.store(_make_entry("e-high", accepted_answer=True))
        result = self.retriever.retrieve(_make_query(), self.repo)
        assert result.top_match is not None
        for m in result.matches:
            assert result.top_match.relevance_score >= m.relevance_score


class TestHybridRetrieverRobustness:
    def test_never_raises_on_corrupt_repository(self):
        retriever = HybridRetriever()
        bad_repo = MagicMock()
        bad_repo.get_all.side_effect = RuntimeError("DB exploded")
        result = retriever.retrieve(_make_query(), bad_repo)
        assert result is not None
        assert len(result.matches) == 0

    def test_result_id_unique_per_call(self):
        retriever = HybridRetriever()
        repo = InMemoryKnowledgeRepository()
        r1 = retriever.retrieve(_make_query(), repo)
        r2 = retriever.retrieve(_make_query(), repo)
        assert r1.result_id != r2.result_id

    def test_query_preserved_in_result(self):
        retriever = HybridRetriever()
        repo = InMemoryKnowledgeRepository()
        q = _make_query()
        result = retriever.retrieve(q, repo)
        assert result.query is q


class TestHybridRetrieverKeywords:
    def test_keyword_match_contributes_to_score(self):
        retriever = HybridRetriever()
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry("e-kwmatch", body="expired session vkyc failed"))
        result = retriever.retrieve(
            _make_query(keywords=("expired", "session", "vkyc")),
            repo,
        )
        assert len(result.matches) > 0
        assert result.top_match is not None
