"""
retrieval/models.py
-------------------
Pure-dataclass models for the hybrid retrieval layer.
No pydantic dependency — keeps this importable in any environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SemanticCandidate:
    """A single result returned by the vector similarity search."""
    doc_id: str
    content: str
    metadata: dict[str, Any]
    similarity: float                    # 0.0 – 1.0 cosine similarity
    semantic_rank: int = 0               # rank within semantic result set


@dataclass
class KeywordCandidate:
    """A single result returned by the PostgreSQL full-text search."""
    doc_id: str
    content: str
    metadata: dict[str, Any]
    ts_rank: float                       # PostgreSQL ts_rank score
    keyword_rank: int = 0                # rank within keyword result set
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class FusedCandidate:
    """Result after Reciprocal Rank Fusion combining semantic + keyword."""
    doc_id: str
    content: str
    metadata: dict[str, Any]

    # Individual ranks (0 = not present in that result set)
    semantic_rank: int = 0
    keyword_rank: int = 0

    # Score components
    semantic_score: float = 0.0         # original similarity score
    keyword_score: float = 0.0          # original ts_rank score
    rrf_score: float = 0.0              # combined RRF score

    # Post-fusion
    rerank_score: float = 0.0           # lexical rerank score
    final_rank: int = 0                 # rank after reranking + selection


@dataclass
class RetrievalTrace:
    """Full trace of a single hybrid retrieval request for observability."""
    query: str
    query_hash: str = ""

    # Stage counts
    semantic_candidates_count: int = 0
    keyword_candidates_count: int = 0
    fused_candidates_count: int = 0
    final_count: int = 0

    # Latencies (ms)
    embedding_latency_ms: float = 0.0
    semantic_latency_ms: float = 0.0
    keyword_latency_ms: float = 0.0
    fusion_latency_ms: float = 0.0
    rerank_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    # Debug data
    semantic_candidates: list[dict[str, Any]] = field(default_factory=list)
    keyword_candidates: list[dict[str, Any]] = field(default_factory=list)
    fused_candidates: list[dict[str, Any]] = field(default_factory=list)
    final_candidates: list[dict[str, Any]] = field(default_factory=list)

    # Config snapshot
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    thresholds_applied: dict[str, float] = field(default_factory=dict)

    errors: list[str] = field(default_factory=list)


@dataclass
class HybridResult:
    """Final output of the hybrid retrieval pipeline."""
    matches: list[dict[str, Any]]
    matches_by_source_type: dict[str, list[dict[str, Any]]]
    insufficient_context: bool
    clarification: str | None
    diagnostics: dict[str, Any]
    trace: RetrievalTrace | None = None
