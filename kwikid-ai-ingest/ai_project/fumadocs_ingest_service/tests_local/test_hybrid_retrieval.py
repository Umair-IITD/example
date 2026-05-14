"""
tests_local/test_hybrid_retrieval.py
--------------------------------------
Unit tests for the hybrid retrieval pipeline (pure-stdlib, no network calls).

Run:
    python -m unittest discover -s tests_local -p "test_hybrid_retrieval.py" -v
"""
from __future__ import annotations

import sys
import os
import unittest

# Project root on path so 'retrieval.*' imports resolve
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from retrieval.config import RetrievalConfig
from retrieval.filters import (
    build_metadata_filter_set,
    matches_metadata_filters,
    apply_metadata_filters,
)
from retrieval.fusion import run_rrf_fusion, fused_to_row
from retrieval.metrics import (
    hit_rate_at_k,
    recall_at_k,
    mean_reciprocal_rank,
    evaluate_batch,
    latency_stats,
)
from retrieval.models import SemanticCandidate, KeywordCandidate, FusedCandidate
from retrieval.reranker import LexicalReranker, get_reranker


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_semantic(n: int = 3) -> list[SemanticCandidate]:
    return [
        SemanticCandidate(
            doc_id=f"sem-{i}",
            content=f"PAN mismatch error during VKYC session {i}",
            metadata={"source_type": "freshdesk"},
            similarity=round(0.95 - i * 0.05, 2),
            semantic_rank=i + 1,
        )
        for i in range(n)
    ]


def _make_keyword(n: int = 3, overlap_id: str = "sem-1") -> list[KeywordCandidate]:
    return [
        KeywordCandidate(
            doc_id=overlap_id if i == 0 else f"kw-{i}",
            content=f"PAN card mismatch in KYC flow {i}",
            metadata={"source_type": "md"},
            ts_rank=round(0.8 - i * 0.1, 2),
            keyword_rank=i + 1,
            matched_terms=["pan", "mismatch"],
        )
        for i in range(n)
    ]


def _make_config(**kw) -> RetrievalConfig:
    """Build a RetrievalConfig without triggering env reads."""
    c = object.__new__(RetrievalConfig)
    defaults = dict(
        semantic_top_k=10, semantic_threshold=0.0,
        keyword_top_k=10, keyword_enabled=True, keyword_min_ts_rank=0.0,
        rrf_k=60, fusion_top_k=10,
        rerank_enabled=True, rerank_top_k=5,
        final_top_k=5, min_similarity=0.2, min_rerank_score=0.05,
        filter_by_index_version=True, filter_by_tenant=True,
        trace_enabled=False,
    )
    defaults.update(kw)
    for k, v in defaults.items():
        object.__setattr__(c, k, v)
    return c


# ── Tests: Metadata Filters ──────────────────────────────────────────────────

class TestMetadataFilters(unittest.TestCase):

    def test_no_filters_pass_all(self):
        filters = build_metadata_filter_set()
        self.assertTrue(matches_metadata_filters({"source_type": "md"}, filters))

    def test_source_type_match(self):
        filters = build_metadata_filter_set(source_types=["md", "json"])
        self.assertTrue(matches_metadata_filters({"source_type": "md"}, filters))

    def test_source_type_reject(self):
        filters = build_metadata_filter_set(source_types=["md"])
        self.assertFalse(matches_metadata_filters({"source_type": "freshdesk"}, filters))

    def test_tenant_filter(self):
        filters = build_metadata_filter_set(tenant="kwikid")
        self.assertTrue(matches_metadata_filters({"tenant": "kwikid"}, filters))
        self.assertFalse(matches_metadata_filters({"tenant": "other"}, filters))

    def test_index_version_filter(self):
        filters = build_metadata_filter_set(index_version="v1")
        self.assertTrue(matches_metadata_filters({"index_version": "v1"}, filters))
        self.assertFalse(matches_metadata_filters({"index_version": "v2"}, filters))

    def test_apply_filters_list(self):
        rows = [
            {"metadata": {"source_type": "md"}},
            {"metadata": {"source_type": "freshdesk"}},
        ]
        filters = build_metadata_filter_set(source_types=["md"])
        result = apply_metadata_filters(rows, filters)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["metadata"]["source_type"], "md")


# ── Tests: RRF Fusion ────────────────────────────────────────────────────────

class TestRRFFusion(unittest.TestCase):

    def test_fusion_returns_results(self):
        fused, latency = run_rrf_fusion(_make_semantic(3), _make_keyword(3), _make_config())
        self.assertGreater(len(fused), 0)
        self.assertGreaterEqual(latency, 0.0)

    def test_overlap_doc_scores_higher(self):
        """sem-1 is in both sets and should beat a semantic-only doc."""
        sem = _make_semantic(3)
        kw  = _make_keyword(3, overlap_id="sem-1")
        fused, _ = run_rrf_fusion(sem, kw, _make_config())
        overlap   = next(c for c in fused if c.doc_id == "sem-1")
        sem_only  = next((c for c in fused if c.doc_id == "sem-2"), None)
        if sem_only:
            self.assertGreaterEqual(overlap.rrf_score, sem_only.rrf_score)

    def test_rrf_formula(self):
        k = 60
        sem = [SemanticCandidate("d1", "x", {}, 0.9, semantic_rank=1)]
        kw  = [KeywordCandidate("d1", "x", {}, 0.8, keyword_rank=1)]
        fused, _ = run_rrf_fusion(sem, kw, _make_config(rrf_k=k, fusion_top_k=10))
        expected = 1 / (k + 1) + 1 / (k + 1)
        self.assertAlmostEqual(fused[0].rrf_score, expected, places=9)

    def test_fusion_top_k_cap(self):
        sem = _make_semantic(5)
        kw  = _make_keyword(5, overlap_id="kw-only")
        fused, _ = run_rrf_fusion(sem, kw, _make_config(fusion_top_k=4))
        self.assertLessEqual(len(fused), 4)

    def test_fused_to_row_backward_compat(self):
        c = FusedCandidate(
            doc_id="d1", content="hello", metadata={},
            semantic_rank=1, keyword_rank=2, rrf_score=0.03,
            semantic_score=0.85, rerank_score=0.6, final_rank=1,
        )
        row = fused_to_row(c)
        self.assertEqual(row["id"], "d1")
        self.assertEqual(row["similarity"], 0.85)
        self.assertEqual(row["rerank_score"], 0.6)
        self.assertEqual(row["rrf_score"], 0.03)
        self.assertIn("metadata", row)


# ── Tests: Reranker ──────────────────────────────────────────────────────────

class TestLexicalReranker(unittest.TestCase):

    def setUp(self):
        self.reranker = LexicalReranker()

    def test_high_overlap_scores_high(self):
        score = self.reranker.score("PAN mismatch VKYC", "PAN mismatch occurred in VKYC session")
        self.assertGreater(score, 0.5)

    def test_no_overlap_scores_low(self):
        score = self.reranker.score("PAN mismatch", "completely unrelated content about weather")
        self.assertLess(score, 0.3)

    def test_empty_query_returns_zero(self):
        self.assertEqual(self.reranker.score("", "some content"), 0.0)

    def test_rerank_sorts_descending(self):
        sem = _make_semantic(5)
        kw  = _make_keyword(5, overlap_id="sem-1")
        fused, _ = run_rrf_fusion(sem, kw, _make_config(fusion_top_k=5))
        reranked, latency = self.reranker.rerank("PAN mismatch VKYC", fused, top_k=3)
        self.assertLessEqual(len(reranked), 3)
        self.assertGreaterEqual(latency, 0.0)
        scores = [c.rerank_score for c in reranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_get_reranker_returns_lexical(self):
        self.assertIsInstance(get_reranker(_make_config()), LexicalReranker)


# ── Tests: Metrics ───────────────────────────────────────────────────────────

class TestRetrievalMetrics(unittest.TestCase):

    def _results(self):
        return [
            {"query": "q1", "actual": ["d1", "d2", "d3"], "expected": ["d1"]},   # hit pos 1
            {"query": "q2", "actual": ["d4", "d5"],        "expected": ["d6"]},   # miss
            {"query": "q3", "actual": ["d7", "d1"],        "expected": ["d1"]},   # hit pos 2
        ]

    def test_hit_rate_at_5(self):
        self.assertAlmostEqual(hit_rate_at_k(self._results(), k=5), 2/3)

    def test_hit_rate_at_1(self):
        self.assertAlmostEqual(hit_rate_at_k(self._results(), k=1), 1/3)

    def test_mrr(self):
        # q1: 1/1, q2: 0, q3: 1/2  → mean = 0.5
        self.assertAlmostEqual(mean_reciprocal_rank(self._results()), (1.0 + 0.0 + 0.5) / 3)

    def test_recall_at_k_positive(self):
        self.assertGreater(recall_at_k(self._results(), k=5), 0.0)

    def test_empty_results(self):
        self.assertEqual(hit_rate_at_k([], k=5), 0.0)
        self.assertEqual(recall_at_k([], k=5), 0.0)
        self.assertEqual(mean_reciprocal_rank([]), 0.0)

    def test_evaluate_batch_keys(self):
        report = evaluate_batch(self._results(), k=5)
        self.assertIn("hit_rate_at_5", report)
        self.assertIn("mrr", report)
        self.assertEqual(report["query_count"], 3)

    def test_latency_stats(self):
        stats = latency_stats([10.0, 20.0, 30.0, 100.0, 200.0])
        self.assertEqual(stats["count"], 5)
        self.assertAlmostEqual(stats["mean_ms"], 72.0)
        self.assertEqual(stats["max_ms"], 200.0)

    def test_latency_stats_empty(self):
        self.assertEqual(latency_stats([])["count"], 0)


# ── Tests: Keyword tsquery builder ───────────────────────────────────────────

class TestKeywordTsquery(unittest.TestCase):

    def test_tokens_present(self):
        from retrieval.keyword_search import _build_tsquery
        q = _build_tsquery("PAN mismatch during VKYC")
        self.assertIn("PAN:*", q)
        self.assertIn("VKYC:*", q)
        self.assertIn("mismatch:*", q)

    def test_empty_input(self):
        from retrieval.keyword_search import _build_tsquery
        self.assertEqual(_build_tsquery(""), "")

    def test_deduplication(self):
        from retrieval.keyword_search import _build_tsquery
        q = _build_tsquery("pan PAN Pan")
        # Should only have one entry for 'pan'
        self.assertLessEqual(q.lower().count("pan:*"), 1)


# ── Tests: RetrievalConfig ────────────────────────────────────────────────────

class TestRetrievalConfig(unittest.TestCase):

    def test_to_dict_has_required_keys(self):
        c = _make_config()
        d = c.to_dict()
        for key in ("semantic_top_k", "rrf_k", "rerank_enabled", "fusion_top_k"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
