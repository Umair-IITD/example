"""
metrics_platform — Uptime Kuma read-only integration for the KwikID
Investigation Layer (Sprint 2.50).

Public surface used by the METRICTOOL / SERVERTOOL adapters:

    MetricsPlatformConfig                — env-driven configuration
    UptimeKumaClient / build_uptime_kuma_client
                                          — async READ-ONLY HTTP client
    parse_prometheus_text                — pure-function Prometheus parser
    filter_by_name_keywords              — monitor filter
    build_metrics_evidence               — canonical MetricsEvidence normaliser
    build_server_health_evidence         — canonical ServerHealthEvidence
    build_dashboard_snapshot             — raw status-page snapshot
    emit_metrics_trace + TRACE_* constants — Sprint 2.50 trace tags

Domain models (frozen dataclasses, JSON-safe): MetricsEvidence,
ServerHealthEvidence, MonitorStatus, MetricPoint, OutageEvent,
IncidentInfo, MaintenanceInfo, UptimePercentage, ComponentStatus,
PrometheusQueryResult, DashboardSnapshot.

Typed exceptions: MetricsPlatformError, MetricsPlatformAuthError,
MetricsPlatformNotFoundError, MetricsPlatformServerError,
MetricsPlatformConnectionError, MetricsPlatformTimeoutError.

Dependency direction:
    metrics_platform → httpx + stdlib.
    metrics_platform → NO imports from case_engine / freshdesk.
"""
from metrics_platform.client import UptimeKumaClient, build_uptime_kuma_client
from metrics_platform.config import MetricsPlatformConfig
from metrics_platform.exceptions import (
    MetricsPlatformApiError,
    MetricsPlatformAuthError,
    MetricsPlatformConnectionError,
    MetricsPlatformError,
    MetricsPlatformForbiddenError,
    MetricsPlatformNotFoundError,
    MetricsPlatformServerError,
    MetricsPlatformTimeoutError,
)
from metrics_platform.models import (
    ComponentStatus,
    DashboardSnapshot,
    DataAvailability,
    IncidentInfo,
    MaintenanceInfo,
    MetricPoint,
    MetricsEvidence,
    MonitorStatus,
    MonitorStatusValue,
    MonitorType,
    OutageEvent,
    PrometheusQueryResult,
    ServerHealthEvidence,
    UptimePercentage,
)
from metrics_platform.normalizer import (
    build_dashboard_snapshot,
    build_metrics_evidence,
    build_server_health_evidence,
)
from metrics_platform.prometheus_parser import (
    filter_by_name_keywords,
    parse_prometheus_text,
)
from metrics_platform.traces import (
    ALL_METRICS_TRACES,
    TRACE_ENTER_METRICS_TOOL,
    TRACE_EXIT_METRICS_TOOL,
    TRACE_METRICS_EVIDENCE_CREATED,
    TRACE_METRICS_NORMALIZED,
    TRACE_METRICS_QUERY,
    TRACE_METRICS_RESPONSE,
    emit_metrics_trace,
)

__all__ = [
    # Configuration
    "MetricsPlatformConfig",
    # Client
    "UptimeKumaClient",
    "build_uptime_kuma_client",
    # Domain models
    "DataAvailability",
    "MonitorStatusValue",
    "MonitorType",
    "MonitorStatus",
    "MetricPoint",
    "OutageEvent",
    "IncidentInfo",
    "MaintenanceInfo",
    "UptimePercentage",
    "ComponentStatus",
    "PrometheusQueryResult",
    "DashboardSnapshot",
    "MetricsEvidence",
    "ServerHealthEvidence",
    # Parser / normaliser
    "parse_prometheus_text",
    "filter_by_name_keywords",
    "build_metrics_evidence",
    "build_server_health_evidence",
    "build_dashboard_snapshot",
    # Traces
    "emit_metrics_trace",
    "ALL_METRICS_TRACES",
    "TRACE_ENTER_METRICS_TOOL",
    "TRACE_METRICS_QUERY",
    "TRACE_METRICS_RESPONSE",
    "TRACE_METRICS_NORMALIZED",
    "TRACE_METRICS_EVIDENCE_CREATED",
    "TRACE_EXIT_METRICS_TOOL",
    # Exceptions
    "MetricsPlatformError",
    "MetricsPlatformApiError",
    "MetricsPlatformAuthError",
    "MetricsPlatformForbiddenError",
    "MetricsPlatformNotFoundError",
    "MetricsPlatformServerError",
    "MetricsPlatformConnectionError",
    "MetricsPlatformTimeoutError",
]
