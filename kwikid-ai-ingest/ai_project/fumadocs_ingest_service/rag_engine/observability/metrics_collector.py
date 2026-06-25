"""
rag_engine/observability/metrics_collector.py

Lightweight in-process metrics for ingestion runs.
Designed for single-process ingestion; not a distributed metrics system.

Tracked metrics:
  - document_count by automation_label
  - chunk_count by chunk_type
  - embedding_failures by error_type
  - upsert_latency_ms distribution

Knowledge-specific metrics (knowledge_ prefix — no conflict with freshdesk_ metrics):
  - knowledge_embedding_latency_ms   — per-article embedding round-trip latency
  - knowledge_retrieval_hit_rate     — fraction of retrievals returning ≥1 result
  - knowledge_completeness_score_*   — completeness score distribution buckets
  - knowledge_quality_rejection_rate — fraction of articles rejected by quality gate
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from typing import Generator

LOGGER = logging.getLogger(__name__)

# ── Knowledge metric name constants (knowledge_ prefix) ───────────────────────
# These are distinct from freshdesk_ metrics in freshdesk/metrics.py
KNOWLEDGE_EMBEDDING_LATENCY_MS      = "knowledge_embedding_latency_ms"
KNOWLEDGE_RETRIEVAL_HIT_RATE        = "knowledge_retrieval_hit_rate"
KNOWLEDGE_COMPLETENESS_SCORE_LOW    = "knowledge_completeness_score_low"    # score < 0.5
KNOWLEDGE_COMPLETENESS_SCORE_MED    = "knowledge_completeness_score_medium" # 0.5 ≤ score < 0.8
KNOWLEDGE_COMPLETENESS_SCORE_HIGH   = "knowledge_completeness_score_high"   # score ≥ 0.8
KNOWLEDGE_QUALITY_REJECTION_RATE    = "knowledge_quality_rejection_rate"
KNOWLEDGE_ARTICLES_PROCESSED        = "knowledge_articles_processed_total"
KNOWLEDGE_ARTICLES_REJECTED         = "knowledge_articles_rejected_total"
KNOWLEDGE_RETRIEVAL_REQUESTS        = "knowledge_retrieval_requests_total"
KNOWLEDGE_RETRIEVAL_HITS            = "knowledge_retrieval_hits_total"


class MetricsCollector:
    """
    In-process counter and timing collector for one ingestion run.
    Reset between runs by creating a new instance.
    """

    def __init__(self) -> None:
        self._counters: Counter = Counter()
        self._latencies: dict[str, list[float]] = defaultdict(list)

    def increment(self, metric: str, value: int = 1) -> None:
        self._counters[metric] += value

    def record_latency(self, operation: str, latency_ms: float) -> None:
        self._latencies[operation].append(latency_ms)

    @contextmanager
    def timer(self, operation: str) -> Generator[None, None, None]:
        """Context manager that records latency for an operation block."""
        t_start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            self.record_latency(operation, elapsed_ms)

    def p95_latency(self, operation: str) -> float:
        """Return p95 latency in ms for an operation."""
        samples = sorted(self._latencies.get(operation, []))
        if not samples:
            return 0.0
        idx = max(0, int(len(samples) * 0.95) - 1)
        return samples[idx]

    # ── Knowledge-specific metric helpers ──────────────────────────────────────

    def record_knowledge_embedding_latency(self, latency_ms: float) -> None:
        """Record per-article embedding latency in milliseconds."""
        self.record_latency(KNOWLEDGE_EMBEDDING_LATENCY_MS, latency_ms)

    def record_knowledge_article_processed(self, *, rejected: bool = False) -> None:
        """Track article through quality gate. rejected=True increments rejection counter."""
        self.increment(KNOWLEDGE_ARTICLES_PROCESSED)
        if rejected:
            self.increment(KNOWLEDGE_ARTICLES_REJECTED)

    def record_knowledge_completeness_score(self, score: float) -> None:
        """
        Bin completeness score into low/medium/high distribution counters.

        Buckets:
          low    — score < 0.50
          medium — 0.50 ≤ score < 0.80
          high   — score ≥ 0.80
        """
        if score < 0.50:
            self.increment(KNOWLEDGE_COMPLETENESS_SCORE_LOW)
        elif score < 0.80:
            self.increment(KNOWLEDGE_COMPLETENESS_SCORE_MED)
        else:
            self.increment(KNOWLEDGE_COMPLETENESS_SCORE_HIGH)

    def record_knowledge_retrieval(self, *, hit: bool) -> None:
        """
        Track a retrieval request and whether it returned ≥1 result above threshold.

        hit=True  → both retrieval_requests and retrieval_hits incremented
        hit=False → only retrieval_requests incremented
        """
        self.increment(KNOWLEDGE_RETRIEVAL_REQUESTS)
        if hit:
            self.increment(KNOWLEDGE_RETRIEVAL_HITS)

    def knowledge_quality_rejection_rate(self) -> float:
        """
        Return the fraction of processed articles rejected by the quality gate.

        Returns 0.0 if no articles have been processed yet.
        """
        total    = self._counters.get(KNOWLEDGE_ARTICLES_PROCESSED, 0)
        rejected = self._counters.get(KNOWLEDGE_ARTICLES_REJECTED, 0)
        if total == 0:
            return 0.0
        return round(rejected / total, 4)

    def knowledge_retrieval_hit_rate(self) -> float:
        """
        Return the fraction of retrieval requests that returned ≥1 result.

        Returns 0.0 if no retrievals have been recorded yet.
        """
        total = self._counters.get(KNOWLEDGE_RETRIEVAL_REQUESTS, 0)
        hits  = self._counters.get(KNOWLEDGE_RETRIEVAL_HITS, 0)
        if total == 0:
            return 0.0
        return round(hits / total, 4)

    # ── Reporting ──────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "counters": dict(self._counters),
            "latency_p95_ms": {
                op: round(self.p95_latency(op), 1)
                for op in self._latencies
            },
            "knowledge_quality_rejection_rate": self.knowledge_quality_rejection_rate(),
            "knowledge_retrieval_hit_rate": self.knowledge_retrieval_hit_rate(),
        }

    def log_summary(self) -> None:
        s = self.summary()
        LOGGER.info(
            "MetricsCollector summary: counters=%s | p95_latencies=%s | "
            "knowledge_rejection_rate=%.4f | knowledge_hit_rate=%.4f",
            s["counters"],
            s["latency_p95_ms"],
            s["knowledge_quality_rejection_rate"],
            s["knowledge_retrieval_hit_rate"],
        )
