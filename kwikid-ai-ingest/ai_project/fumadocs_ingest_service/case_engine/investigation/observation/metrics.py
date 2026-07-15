"""
case_engine/investigation/observation/metrics.py

Sprint 2.44: Execution metrics for the Observation Generator.

Exposes (per blueprint Section 29 observability principle):
  - generation count
  - template usage
  - average generation latency
  - contradiction count
  - escalation count
  - confidence distribution

GeneratorMetrics is thread-safe: one instance may be shared across requests.

Dependency direction:
  metrics.py → stdlib (dataclasses, threading, datetime)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


_CONFIDENCE_BUCKETS: tuple[str, ...] = (
    "0.00-0.20",
    "0.20-0.40",
    "0.40-0.60",
    "0.60-0.80",
    "0.80-1.00",
)


def _confidence_bucket(confidence: float) -> str:
    idx = min(int(confidence * 5), 4)
    idx = max(idx, 0)
    return _CONFIDENCE_BUCKETS[idx]


@dataclass(frozen=True)
class ObservationMetric:
    """
    Per-generation metric snapshot.

    duration_ms: wall-clock time taken to generate the observation.
    """
    observation_id:      str
    template_id:         str
    duration_ms:         int
    contradiction_count: int
    escalated:           bool
    confidence:          float
    status:              str   # ObservationStatus.value
    recorded_at:         str

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id":      self.observation_id,
            "template_id":         self.template_id,
            "duration_ms":         self.duration_ms,
            "contradiction_count": self.contradiction_count,
            "escalated":           self.escalated,
            "confidence":          self.confidence,
            "status":              self.status,
            "recorded_at":         self.recorded_at,
        }


class GeneratorMetrics:
    """
    Thread-safe aggregate metrics for the Observation Generator.

    All mutation goes through record(); all reads through snapshot().
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation_count = 0
        self._total_duration_ms = 0
        self._contradiction_count = 0
        self._escalation_count = 0
        self._template_usage: dict[str, int] = {}
        self._status_counts: dict[str, int] = {}
        self._confidence_distribution: dict[str, int] = {
            bucket: 0 for bucket in _CONFIDENCE_BUCKETS
        }

    def record(self, metric: ObservationMetric) -> None:
        """Record one completed generation."""
        bucket = _confidence_bucket(metric.confidence)
        with self._lock:
            self._generation_count += 1
            self._total_duration_ms += metric.duration_ms
            self._contradiction_count += metric.contradiction_count
            if metric.escalated:
                self._escalation_count += 1
            self._template_usage[metric.template_id] = (
                self._template_usage.get(metric.template_id, 0) + 1
            )
            self._status_counts[metric.status] = (
                self._status_counts.get(metric.status, 0) + 1
            )
            self._confidence_distribution[bucket] += 1

    @property
    def generation_count(self) -> int:
        with self._lock:
            return self._generation_count

    @property
    def escalation_count(self) -> int:
        with self._lock:
            return self._escalation_count

    @property
    def contradiction_count(self) -> int:
        with self._lock:
            return self._contradiction_count

    @property
    def average_latency_ms(self) -> float:
        with self._lock:
            if self._generation_count == 0:
                return 0.0
            return self._total_duration_ms / self._generation_count

    def template_usage(self) -> dict[str, int]:
        with self._lock:
            return dict(self._template_usage)

    def confidence_distribution(self) -> dict[str, int]:
        with self._lock:
            return dict(self._confidence_distribution)

    def snapshot(self) -> dict[str, Any]:
        """Return a point-in-time copy of all aggregate metrics."""
        with self._lock:
            avg = (
                self._total_duration_ms / self._generation_count
                if self._generation_count
                else 0.0
            )
            return {
                "generation_count":        self._generation_count,
                "template_usage":          dict(self._template_usage),
                "average_latency_ms":      avg,
                "contradiction_count":     self._contradiction_count,
                "escalation_count":        self._escalation_count,
                "confidence_distribution": dict(self._confidence_distribution),
                "status_counts":           dict(self._status_counts),
                "snapshot_at":             _now_iso(),
            }

    def reset(self) -> None:
        """Clear all counters. For test isolation only."""
        with self._lock:
            self._generation_count = 0
            self._total_duration_ms = 0
            self._contradiction_count = 0
            self._escalation_count = 0
            self._template_usage.clear()
            self._status_counts.clear()
            self._confidence_distribution = {
                bucket: 0 for bucket in _CONFIDENCE_BUCKETS
            }
