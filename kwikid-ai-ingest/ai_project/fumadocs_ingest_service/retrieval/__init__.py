"""
retrieval/
----------
Modular hybrid retrieval pipeline for KwikID AI Ingest.

Public API:
    from retrieval import RetrievalConfig, HybridResult
    from retrieval.hybrid_search import run_hybrid_search   # requires supabase-py
"""
# Pure-stdlib modules — always importable, no heavy deps
from retrieval.config import RetrievalConfig, DEFAULT_CONFIG
from retrieval.models import HybridResult, RetrievalTrace, FusedCandidate
from retrieval.filters import build_metadata_filter_set
from retrieval.metrics import hit_rate_at_k, recall_at_k, mean_reciprocal_rank, evaluate_batch

__all__ = [
    "RetrievalConfig",
    "DEFAULT_CONFIG",
    "HybridResult",
    "RetrievalTrace",
    "FusedCandidate",
    "build_metadata_filter_set",
    "hit_rate_at_k",
    "recall_at_k",
    "mean_reciprocal_rank",
    "evaluate_batch",
]
