"""
rag_engine/observability/metrics_collector.py

Lightweight in-process metrics for ingestion runs.
Designed for single-process ingestion; not a distributed metrics system.

Tracked metrics:
  - document_count by automation_label
  - chunk_count by chunk_type
  - embedding_failures by error_type
  - upsert_latency_ms distribution
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from typing import Generator

LOGGER = logging.getLogger(__name__)


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

    def summary(self) -> dict:
        return {
            "counters": dict(self._counters),
            "latency_p95_ms": {
                op: round(self.p95_latency(op), 1)
                for op in self._latencies
            },
        }

    def log_summary(self) -> None:
        s = self.summary()
        LOGGER.info(
            "MetricsCollector summary: counters=%s | p95_latencies=%s",
            s["counters"],
            s["latency_p95_ms"],
        )
