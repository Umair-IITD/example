"""
metrics — Sprint 2.10 observability layer.

Public API:
    MetricsCollector  — thread-safe counter and latency storage
    MetricsService    — facade for recording action lifecycle events
"""
from metrics.collector import MetricsCollector
from metrics.service import MetricsService

__all__ = ["MetricsCollector", "MetricsService"]
