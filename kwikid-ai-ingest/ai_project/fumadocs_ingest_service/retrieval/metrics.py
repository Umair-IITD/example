"""
retrieval/metrics.py
---------------------
Retrieval quality metrics for local evaluation.

Metrics:
  - Hit Rate @ K : Was any expected doc in the top-K results?
  - Recall @ K   : What fraction of expected docs appeared in top-K?
  - MRR          : Mean Reciprocal Rank of first relevant result
  - Latency stats: mean, p50, p95 across a batch of queries

All metrics work on lists of result dicts — no external dependencies.
"""
from __future__ import annotations

from typing import Any


# ── Core Metrics ────────────────────────────────────────────────────────────

def hit_rate_at_k(results: list[dict[str, Any]], k: int = 5) -> float:
    """
    Fraction of queries where at least one expected doc is in top-K.

    Each result dict:
        {"actual": ["id1", "id2", ...], "expected": ["idA", "idB", ...]}
    """
    if not results:
        return 0.0
    hits = sum(
        1 for r in results
        if any(doc_id in set(r.get("expected", [])) for doc_id in r.get("actual", [])[:k])
    )
    return hits / len(results)


def recall_at_k(results: list[dict[str, Any]], k: int = 5) -> float:
    """
    Average fraction of expected docs that appeared in top-K results.
    """
    if not results:
        return 0.0
    recalls = []
    for r in results:
        expected = set(r.get("expected", []))
        actual_top_k = set(r.get("actual", [])[:k])
        if not expected:
            continue
        recalls.append(len(expected & actual_top_k) / len(expected))
    return sum(recalls) / len(recalls) if recalls else 0.0


def mean_reciprocal_rank(results: list[dict[str, Any]]) -> float:
    """
    Mean Reciprocal Rank (MRR) — average of 1/rank of first relevant result.
    """
    if not results:
        return 0.0
    rr_scores: list[float] = []
    for r in results:
        expected = set(r.get("expected", []))
        actual = r.get("actual", [])
        rr = 0.0
        for i, doc_id in enumerate(actual, start=1):
            if doc_id in expected:
                rr = 1.0 / i
                break
        rr_scores.append(rr)
    return sum(rr_scores) / len(rr_scores)


# ── Latency Stats ───────────────────────────────────────────────────────────

def latency_stats(latencies_ms: list[float]) -> dict[str, float]:
    """
    Return basic statistics for a list of latency measurements (in ms).
    """
    if not latencies_ms:
        return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    sorted_lat = sorted(latencies_ms)
    n = len(sorted_lat)
    p50_idx = int(n * 0.50)
    p95_idx = int(n * 0.95)
    return {
        "count": n,
        "mean_ms": sum(sorted_lat) / n,
        "p50_ms": sorted_lat[min(p50_idx, n - 1)],
        "p95_ms": sorted_lat[min(p95_idx, n - 1)],
        "max_ms": sorted_lat[-1],
    }


# ── Batch Evaluation ────────────────────────────────────────────────────────

def evaluate_batch(results: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """
    Run all metrics over a batch of retrieval results.

    Args:
        results: List of {"query": str, "actual": [id,...], "expected": [id,...]}
        k: Top-K cutoff for Hit Rate and Recall

    Returns:
        Dict with all metric values.
    """
    return {
        f"hit_rate_at_{k}": hit_rate_at_k(results, k),
        f"recall_at_{k}": recall_at_k(results, k),
        "mrr": mean_reciprocal_rank(results),
        "query_count": len(results),
    }
