"""
observability/metrics.py

Prometheus metrics for the KwikID RAG service.

All metrics are no-ops when PROMETHEUS_ENABLED=false (default), so this module
can be safely imported regardless of whether prometheus_client is installed.

Instruments:
  - HTTP request count and latency by path and status
  - Retrieval pipeline stage latency (embedding, semantic, keyword, fusion, rerank)
  - LLM generation latency
  - Rate limit rejection events
  - Active in-flight requests (gauge)
  - Retrieval candidate counts at each stage

Environment:
  PROMETHEUS_ENABLED=false        — master switch (default: disabled)

Usage:
  from observability.metrics import record_request, record_retrieval_latency, ...
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

LOGGER = logging.getLogger(__name__)

_ENABLED = os.getenv("PROMETHEUS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

_http_requests_total: Any = None
_http_request_duration_seconds: Any = None
_retrieval_latency_seconds: Any = None
_llm_latency_seconds: Any = None
_rate_limit_rejections_total: Any = None
_active_requests: Any = None
_retrieval_candidates: Any = None

_metrics_available = False


def _init_metrics() -> bool:
    global _http_requests_total, _http_request_duration_seconds
    global _retrieval_latency_seconds, _llm_latency_seconds
    global _rate_limit_rejections_total, _active_requests, _retrieval_candidates

    if not _ENABLED:
        return False

    try:
        from prometheus_client import Counter, Gauge, Histogram  # noqa: PLC0415

        _http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status"],
        )
        _http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path"],
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )
        _retrieval_latency_seconds = Histogram(
            "retrieval_latency_seconds",
            "Retrieval pipeline stage latency",
            ["stage"],
            buckets=[0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )
        _llm_latency_seconds = Histogram(
            "llm_latency_seconds",
            "LLM generation latency in seconds",
            ["model"],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
        )
        _rate_limit_rejections_total = Counter(
            "rate_limit_rejections_total",
            "Rate limit rejection events",
            ["path"],
        )
        _active_requests = Gauge(
            "active_requests",
            "In-flight requests currently being processed",
            ["path"],
        )
        _retrieval_candidates = Histogram(
            "retrieval_candidates_count",
            "Candidate count at each retrieval stage",
            ["stage"],
            buckets=[1, 5, 10, 20, 50, 100, 200],
        )

        LOGGER.info("Prometheus metrics initialized")
        return True

    except ImportError:
        LOGGER.warning(
            "PROMETHEUS_ENABLED=true but prometheus_client is not installed. "
            "Install with: pip install prometheus-client>=0.21. Metrics disabled."
        )
        return False
    except Exception as exc:
        LOGGER.warning("Prometheus metrics initialization failed: %s", exc)
        return False


_metrics_available = _init_metrics()


def record_request(method: str, path: str, status: int, duration_s: float) -> None:
    if not _metrics_available:
        return
    try:
        _http_requests_total.labels(method=method, path=path, status=str(status)).inc()
        _http_request_duration_seconds.labels(method=method, path=path).observe(duration_s)
    except Exception:
        pass


def record_retrieval_latency(stage: str, latency_s: float) -> None:
    if not _metrics_available:
        return
    try:
        _retrieval_latency_seconds.labels(stage=stage).observe(latency_s)
    except Exception:
        pass


def record_llm_latency(model: str, latency_s: float) -> None:
    if not _metrics_available:
        return
    try:
        _llm_latency_seconds.labels(model=model).observe(latency_s)
    except Exception:
        pass


def record_rate_limit_rejection(path: str) -> None:
    if not _metrics_available:
        return
    try:
        _rate_limit_rejections_total.labels(path=path).inc()
    except Exception:
        pass


def record_retrieval_candidates(stage: str, count: int) -> None:
    if not _metrics_available:
        return
    try:
        _retrieval_candidates.labels(stage=stage).observe(count)
    except Exception:
        pass


class ActiveRequestContext:
    """Context manager that increments/decrements the active_requests gauge."""

    def __init__(self, path: str) -> None:
        self._path = path

    def __enter__(self) -> "ActiveRequestContext":
        if _metrics_available and _active_requests is not None:
            try:
                _active_requests.labels(path=self._path).inc()
            except Exception:
                pass
        return self

    def __exit__(self, *_: object) -> None:
        if _metrics_available and _active_requests is not None:
            try:
                _active_requests.labels(path=self._path).dec()
            except Exception:
                pass


def get_metrics_response() -> tuple[bytes, str] | None:
    """
    Generate Prometheus text-format response.
    Returns (data_bytes, content_type_string) or None if unavailable.
    """
    if not _metrics_available:
        return None
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # noqa: PLC0415

        return generate_latest(), CONTENT_TYPE_LATEST
    except Exception as exc:
        LOGGER.warning("Failed to generate Prometheus output: %s", exc)
        return None
