"""
retrieval/config.py
--------------------
Centralized configuration for the hybrid retrieval pipeline.
All values can be overridden via environment variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, str(default)).lower()
    return val in ("true", "1", "yes")


@dataclass
class RetrievalConfig:
    """
    Hybrid retrieval configuration.

    All fields support environment variable overrides prefixed with RETRIEVAL_.
    """

    # ── Semantic search ────────────────────────────────────────────────────
    semantic_top_k: int = field(
        default_factory=lambda: _env_int("RETRIEVAL_SEMANTIC_TOP_K", 20)
    )
    semantic_threshold: float = field(
        default_factory=lambda: _env_float("RETRIEVAL_SEMANTIC_THRESHOLD", 0.0)
    )

    # ── Keyword / BM25 search ──────────────────────────────────────────────
    keyword_top_k: int = field(
        default_factory=lambda: _env_int("RETRIEVAL_KEYWORD_TOP_K", 20)
    )
    keyword_enabled: bool = field(
        default_factory=lambda: _env_bool("RETRIEVAL_KEYWORD_ENABLED", True)
    )
    keyword_min_ts_rank: float = field(
        default_factory=lambda: _env_float("RETRIEVAL_KEYWORD_MIN_TS_RANK", 0.0)
    )

    # ── Reciprocal Rank Fusion ─────────────────────────────────────────────
    rrf_k: int = field(
        default_factory=lambda: _env_int("RETRIEVAL_RRF_K", 60)
    )
    fusion_top_k: int = field(
        default_factory=lambda: _env_int("RETRIEVAL_FUSION_TOP_K", 20)
    )

    # ── Reranking ──────────────────────────────────────────────────────────
    rerank_enabled: bool = field(
        default_factory=lambda: _env_bool("RETRIEVAL_RERANK_ENABLED", True)
    )
    rerank_top_k: int = field(
        default_factory=lambda: _env_int("RETRIEVAL_RERANK_TOP_K", 5)
    )

    # ── Final output ───────────────────────────────────────────────────────
    final_top_k: int = field(
        default_factory=lambda: _env_int("RETRIEVAL_FINAL_TOP_K", 5)
    )

    # ── Confidence thresholds ─────────────────────────────────────────────
    min_similarity: float = field(
        default_factory=lambda: _env_float("RETRIEVAL_MIN_SIMILARITY", 0.2)
    )
    min_rerank_score: float = field(
        default_factory=lambda: _env_float("RETRIEVAL_MIN_RERANK_SCORE", 0.05)
    )

    # ── Metadata filter flags ─────────────────────────────────────────────
    filter_by_index_version: bool = field(
        default_factory=lambda: _env_bool("RETRIEVAL_FILTER_INDEX_VERSION", True)
    )
    filter_by_tenant: bool = field(
        default_factory=lambda: _env_bool("RETRIEVAL_FILTER_TENANT", True)
    )

    # ── Tracing ───────────────────────────────────────────────────────────
    trace_enabled: bool = field(
        default_factory=lambda: _env_bool("DEBUG_TRACE", True)
    )

    def to_dict(self) -> dict:
        """Snapshot for trace logging."""
        return {
            "semantic_top_k": self.semantic_top_k,
            "semantic_threshold": self.semantic_threshold,
            "keyword_top_k": self.keyword_top_k,
            "keyword_enabled": self.keyword_enabled,
            "rrf_k": self.rrf_k,
            "fusion_top_k": self.fusion_top_k,
            "rerank_enabled": self.rerank_enabled,
            "rerank_top_k": self.rerank_top_k,
            "final_top_k": self.final_top_k,
            "min_similarity": self.min_similarity,
            "min_rerank_score": self.min_rerank_score,
        }


# Module-level default instance — override with RetrievalConfig() per-request if needed
DEFAULT_CONFIG = RetrievalConfig()
