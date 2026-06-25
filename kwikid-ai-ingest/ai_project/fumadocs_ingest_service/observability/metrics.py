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

Initialization:
  The preferred path is force_initialize(enabled=settings.prometheus_enabled) called
  from the lifespan startup — this bypasses os.getenv() and load_dotenv() timing
  entirely and guarantees metrics are up before the first request arrives.

  Fallback: _ensure_initialized() is called lazily on every public function.
  It reads PROMETHEUS_ENABLED from os.environ at the time of the first call.
  Because load_dotenv() runs at import time of app/config.py (before request
  processing begins), this lazy path also works — but it produces no diagnostic
  log if the env var is absent or wrong.  Prefer force_initialize() for clarity.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

LOGGER = logging.getLogger(__name__)

_init_lock = threading.Lock()
_initialized = False

_http_requests_total: Any = None
_http_request_duration_seconds: Any = None
_retrieval_latency_seconds: Any = None
_llm_latency_seconds: Any = None
_rate_limit_rejections_total: Any = None
_active_requests: Any = None
_retrieval_candidates: Any = None

# Knowledge layer metrics (WORK ITEM 7)
_knowledge_documents_ingested: Any = None
_knowledge_documents_rejected: Any = None
_knowledge_manual_review_required: Any = None
_knowledge_retrieval_requests: Any = None
_knowledge_retrieval_failures: Any = None
_knowledge_top_k_hits: Any = None
_knowledge_quality_failures: Any = None
_knowledge_image_grounding_failures: Any = None

_metrics_available = False


def _do_register_metrics() -> bool:
    """Register all prometheus_client instruments.

    Called exactly once, under _init_lock.  Separated from the env-var check so
    both the lazy path (_ensure_initialized) and the eager path (force_initialize)
    share the same registration logic.

    Returns True on success, False on any failure (ImportError, ValueError, etc.).
    """
    global _http_requests_total, _http_request_duration_seconds
    global _retrieval_latency_seconds, _llm_latency_seconds
    global _rate_limit_rejections_total, _active_requests, _retrieval_candidates
    global _knowledge_documents_ingested, _knowledge_documents_rejected
    global _knowledge_manual_review_required, _knowledge_retrieval_requests
    global _knowledge_retrieval_failures, _knowledge_top_k_hits
    global _knowledge_quality_failures, _knowledge_image_grounding_failures

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

        # Knowledge layer metrics (WORK ITEM 7)
        _knowledge_documents_ingested = Counter(
            "knowledge_documents_ingested_total",
            "Total knowledge documents successfully ingested",
            ["knowledge_class"],
        )
        _knowledge_documents_rejected = Counter(
            "knowledge_documents_rejected_total",
            "Total knowledge documents rejected during ingestion",
            ["reject_reason"],
        )
        _knowledge_manual_review_required = Counter(
            "knowledge_manual_review_required_total",
            "Knowledge documents flagged for manual review (completeness < threshold)",
        )
        _knowledge_retrieval_requests = Counter(
            "knowledge_retrieval_requests_total",
            "Total knowledge retrieval requests",
            ["topic"],
        )
        _knowledge_retrieval_failures = Counter(
            "knowledge_retrieval_failures_total",
            "Knowledge retrieval requests that returned no match above threshold",
            ["topic"],
        )
        _knowledge_top_k_hits = Histogram(
            "knowledge_top_k_hits",
            "Relevance score of top-k knowledge match",
            ["k"],
            buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        )
        _knowledge_quality_failures = Counter(
            "knowledge_quality_failures_total",
            "Chunks rejected by the quality filter during ingestion",
        )
        _knowledge_image_grounding_failures = Counter(
            "knowledge_image_grounding_failures_total",
            "Image references that could not be resolved to a manifest or local asset",
            ["failure_type"],  # "no_manifest" | "no_local_asset"
        )

        LOGGER.info("Prometheus metrics initialized successfully")
        return True

    except ImportError:
        LOGGER.error(
            "PROMETHEUS_ENABLED=true but prometheus_client is not installed. "
            "Run: pip install prometheus-client>=0.21. Metrics disabled."
        )
        return False
    except ValueError as exc:
        # Duplicated timeseries — happens when metrics module is re-initialized
        # in the same Python process (e.g. test suites that don't fork).
        LOGGER.error(
            "Prometheus metrics registration failed (ValueError: %s). "
            "This usually means the metrics were already registered in this process. "
            "In production this indicates a double-initialization bug. Metrics disabled.",
            exc,
        )
        return False
    except Exception as exc:
        LOGGER.error("Prometheus metrics initialization failed unexpectedly: %s", exc)
        return False


def _do_init_metrics() -> bool:
    """Read PROMETHEUS_ENABLED from os.environ and register metrics if enabled.

    Logs the exact env-var value it observed so the cause of any disabled state
    is always visible in the service logs.
    """
    raw = os.getenv("PROMETHEUS_ENABLED")
    enabled = (raw or "false").strip().lower() in {"1", "true", "yes", "on"}
    LOGGER.info(
        "Prometheus metrics lazy-init: PROMETHEUS_ENABLED=%r → enabled=%s",
        raw,
        enabled,
    )
    if not enabled:
        if raw is None:
            LOGGER.warning(
                "PROMETHEUS_ENABLED is not set in the process environment. "
                "If you set it in .env, ensure load_dotenv(override=True) is used in "
                "app/config.py so it is not silently ignored when the variable is "
                "already present in the shell environment. Metrics disabled."
            )
        return False
    return _do_register_metrics()


def _ensure_initialized() -> None:
    """Lazy double-checked initialization — called at the top of every public function.

    Prefers force_initialize() called from the lifespan startup, which bypasses
    the env-var read entirely.  Falls back here on first-request if force_initialize
    was never called.
    """
    global _initialized, _metrics_available
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        _metrics_available = _do_init_metrics()
        _initialized = True


def force_initialize(*, enabled: bool) -> None:
    """Eagerly initialize metrics at service startup from an explicit flag.

    Called from the lifespan context manager with the already-parsed settings
    value (settings.prometheus_enabled), bypassing os.getenv() and any
    load_dotenv() timing issues entirely.

    Idempotent: a second call (e.g. from _ensure_initialized on the first request)
    is a no-op because _initialized is True after the first call.
    """
    global _initialized, _metrics_available
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        LOGGER.info("Prometheus metrics force_initialize: enabled=%s", enabled)
        if enabled:
            _metrics_available = _do_register_metrics()
        else:
            _metrics_available = False
            LOGGER.info("Prometheus metrics disabled (force_initialize called with enabled=False)")
        _initialized = True


def record_request(method: str, path: str, status: int, duration_s: float) -> None:
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _http_requests_total.labels(method=method, path=path, status=str(status)).inc()
        _http_request_duration_seconds.labels(method=method, path=path).observe(duration_s)
    except Exception:
        pass


def record_retrieval_latency(stage: str, latency_s: float) -> None:
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _retrieval_latency_seconds.labels(stage=stage).observe(latency_s)
    except Exception:
        pass


def record_llm_latency(model: str, latency_s: float) -> None:
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _llm_latency_seconds.labels(model=model).observe(latency_s)
    except Exception:
        pass


def record_rate_limit_rejection(path: str) -> None:
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _rate_limit_rejections_total.labels(path=path).inc()
    except Exception:
        pass


def record_retrieval_candidates(stage: str, count: int) -> None:
    _ensure_initialized()
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
        _ensure_initialized()
        if _metrics_available and _active_requests is not None:
            try:
                _active_requests.labels(path=self._path).inc()
            except Exception:
                pass
        return self

    def __exit__(self, *_: object) -> None:
        _ensure_initialized()
        if _metrics_available and _active_requests is not None:
            try:
                _active_requests.labels(path=self._path).dec()
            except Exception:
                pass


# ── Knowledge layer metric recording functions (WORK ITEM 7) ─────────────────

def record_knowledge_document_ingested(knowledge_class: str) -> None:
    """Increment counter for each successfully ingested knowledge document."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_documents_ingested.labels(knowledge_class=knowledge_class).inc()
    except Exception:
        pass


def record_knowledge_document_rejected(reject_reason: str) -> None:
    """Increment counter for each rejected knowledge document."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_documents_rejected.labels(reject_reason=reject_reason).inc()
    except Exception:
        pass


def record_knowledge_manual_review_required() -> None:
    """Increment counter when a document is flagged for manual review."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_manual_review_required.inc()
    except Exception:
        pass


def record_knowledge_retrieval_request(topic: str) -> None:
    """Increment counter for each knowledge retrieval request."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_retrieval_requests.labels(topic=topic).inc()
    except Exception:
        pass


def record_knowledge_retrieval_failure(topic: str) -> None:
    """Increment counter when a knowledge retrieval finds no match above threshold."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_retrieval_failures.labels(topic=topic).inc()
    except Exception:
        pass


def record_knowledge_top_k_hit(k: int, relevance_score: float) -> None:
    """Record the relevance score of a top-k knowledge retrieval hit."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_top_k_hits.labels(k=str(k)).observe(relevance_score)
    except Exception:
        pass


def record_knowledge_quality_failure(count: int = 1) -> None:
    """Increment counter for chunks rejected by quality filter."""
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        for _ in range(count):
            _knowledge_quality_failures.inc()
    except Exception:
        pass


def record_knowledge_image_grounding_failure(failure_type: str) -> None:
    """Increment counter for unresolvable image references.

    Args:
        failure_type: "no_manifest" if images.json entry missing,
                      "no_local_asset" if binary file not found on disk.
    """
    _ensure_initialized()
    if not _metrics_available:
        return
    try:
        _knowledge_image_grounding_failures.labels(failure_type=failure_type).inc()
    except Exception:
        pass


def get_metrics_response() -> tuple[bytes, str] | None:
    """
    Generate Prometheus text-format response.
    Returns (data_bytes, content_type_string) or None if unavailable.
    """
    _ensure_initialized()
    if not _metrics_available:
        return None
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # noqa: PLC0415

        return generate_latest(), CONTENT_TYPE_LATEST
    except Exception as exc:
        LOGGER.warning("Failed to generate Prometheus output: %s", exc)
        return None
