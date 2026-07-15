"""
metrics_platform/models.py

Sprint 2.50: Canonical, strongly-typed domain models for Uptime Kuma
monitoring evidence.

These are the *canonical* representations the Investigation Layer sees.
Everything upstream (Root Cause Engine, Reasoning Engine, Observation
Generator) consumes these types; NOTHING upstream needs to know that
Uptime Kuma is the source.

Design rules
------------
- All models are `@dataclass(frozen=True)`.
- All models expose `to_dict()` for JSON-safe serialization.
- Enum values are strings (JSON-safe).
- No `Optional` overuse — prefer sentinel values (empty list, empty dict)
  where the field is always semantically present.

Dependency direction
--------------------
    models.py → stdlib only.
    models.py → NO imports from case_engine / freshdesk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────

class MonitorStatusValue(int, Enum):
    """Uptime Kuma monitor status codes (verified live 2026-07-11)."""
    DOWN        = 0
    UP          = 1
    PENDING     = 2
    MAINTENANCE = 3

    @property
    def label(self) -> str:
        return self.name

    @classmethod
    def from_int(cls, value: int | None) -> "MonitorStatusValue | None":
        if value is None:
            return None
        try:
            return cls(int(value))
        except (ValueError, TypeError):
            return None


class MonitorType(str, Enum):
    """Uptime Kuma monitor kinds relevant to KwikID."""
    HTTP    = "http"
    KEYWORD = "keyword"
    PUSH    = "push"
    GROUP   = "group"
    OTHER   = "other"

    @classmethod
    def from_str(cls, value: str | None) -> "MonitorType":
        if not value:
            return cls.OTHER
        low = value.strip().lower()
        for m in cls:
            if m.value == low:
                return m
        return cls.OTHER


class DataAvailability(str, Enum):
    """
    Availability of the underlying monitoring data.

    - AVAILABLE:     platform reachable and returned data
    - PARTIAL:       platform reachable but at least one required endpoint failed
    - UNAVAILABLE:   platform not reachable / auth failed at every endpoint
    - DISABLED:      integration is turned off via config
    """
    AVAILABLE   = "AVAILABLE"
    PARTIAL     = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED    = "DISABLED"


# ── Fine-grained models ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class MonitorStatus:
    """
    Current status of a single Uptime Kuma monitor.

    Populated from Prometheus /metrics output and, when available, enriched
    with response time and cert data.
    """
    monitor_name:        str
    monitor_type:        MonitorType     = MonitorType.OTHER
    monitor_url:         str             = ""
    status:              MonitorStatusValue | None = None
    response_time_ms:    int | None      = None
    cert_days_remaining: int | None      = None
    cert_is_valid:       bool | None     = None
    monitor_hostname:    str             = ""
    monitor_port:        str             = ""

    @property
    def is_down(self) -> bool:
        return self.status == MonitorStatusValue.DOWN

    @property
    def is_up(self) -> bool:
        return self.status == MonitorStatusValue.UP

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_name":        self.monitor_name,
            "monitor_type":        self.monitor_type.value,
            "monitor_url":         self.monitor_url,
            "monitor_hostname":    self.monitor_hostname,
            "monitor_port":        self.monitor_port,
            "status":              None if self.status is None else self.status.value,
            "status_label":        None if self.status is None else self.status.label,
            "response_time_ms":    self.response_time_ms,
            "cert_days_remaining": self.cert_days_remaining,
            "cert_is_valid":       self.cert_is_valid,
        }


@dataclass(frozen=True)
class MetricPoint:
    """
    Single Prometheus data point (one parsed line from the /metrics endpoint).

    This is the raw shape emitted by the parser before it is folded into
    per-monitor MonitorStatus records.
    """
    metric_name: str
    value:       float
    labels:      dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "value":       self.value,
            "labels":      dict(self.labels),
        }


@dataclass(frozen=True)
class PrometheusQueryResult:
    """Structured result of parsing Uptime Kuma's Prometheus text output."""
    metric_points: tuple[MetricPoint, ...]
    monitors:      tuple[MonitorStatus, ...]

    @property
    def monitors_down(self) -> tuple[MonitorStatus, ...]:
        return tuple(m for m in self.monitors if m.is_down)

    @property
    def monitors_up(self) -> tuple[MonitorStatus, ...]:
        return tuple(m for m in self.monitors if m.is_up)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_point_count": len(self.metric_points),
            "monitors":           [m.to_dict() for m in self.monitors],
            "monitors_down":      [m.monitor_name for m in self.monitors_down],
        }


@dataclass(frozen=True)
class OutageEvent:
    """Historical outage occurrence — either currently ongoing or recovered."""
    monitor_id:       str
    monitor_name:     str
    started_at:       str            # ISO 8601
    ended_at:         str | None     # None if still ongoing
    duration_seconds: int
    message:          str            = ""
    still_ongoing:    bool           = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_id":       self.monitor_id,
            "monitor_name":     self.monitor_name,
            "started_at":       self.started_at,
            "ended_at":         self.ended_at,
            "duration_seconds": self.duration_seconds,
            "message":          self.message,
            "still_ongoing":    self.still_ongoing,
        }


@dataclass(frozen=True)
class IncidentInfo:
    """Active status-page incident (from /api/status-page/<slug>)."""
    incident_id: str
    title:       str
    content:     str
    style:       str            # "danger" | "warning" | "info" | ...
    created_at:  str
    pin:         bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title":       self.title,
            "content":     self.content[:500],   # truncate for private-note safety
            "style":       self.style,
            "created_at":  self.created_at,
            "pin":         self.pin,
        }


@dataclass(frozen=True)
class MaintenanceInfo:
    """Scheduled maintenance window."""
    maintenance_id: str
    title:          str
    description:    str
    strategy:       str            # e.g. "recurring-interval" | "cron" | "single" | "manual"
    active:         bool
    start_at:       str | None
    end_at:         str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "maintenance_id": self.maintenance_id,
            "title":          self.title,
            "description":    self.description,
            "strategy":       self.strategy,
            "active":         self.active,
            "start_at":       self.start_at,
            "end_at":         self.end_at,
        }


@dataclass(frozen=True)
class UptimePercentage:
    """
    Uptime percentage for a monitor over a specific window.
    Values are 0.0 – 1.0 (multiply by 100 for display).
    """
    monitor_id:  str
    window_hours: int
    ratio:       float

    @property
    def percent(self) -> float:
        return round(self.ratio * 100.0, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_id":   self.monitor_id,
            "window_hours": self.window_hours,
            "ratio":        self.ratio,
            "percent":      self.percent,
        }


# ── Canonical evidence container ─────────────────────────────────────────────

@dataclass(frozen=True)
class MetricsEvidence:
    """
    Canonical output of METRICTOOL — the single evidence shape the
    Investigation Layer sees, no matter which endpoints produced it.

    This is the type returned to the Root Cause Engine.
    """
    tool_name:                    str
    collected_at:                 str
    source:                       str = "uptime_kuma"

    # Availability
    data_available:               DataAvailability = DataAvailability.AVAILABLE
    error:                        str = ""

    # Monitors (from /metrics)
    monitors:                     tuple[MonitorStatus, ...] = field(default_factory=tuple)
    monitors_currently_down:      tuple[str, ...]           = field(default_factory=tuple)

    # Correlation (from heartbeats)
    monitors_down_at_ticket_time: tuple[OutageEvent, ...]   = field(default_factory=tuple)

    # Context (from status page)
    active_incident:              IncidentInfo | None       = None
    maintenance_windows:          tuple[MaintenanceInfo, ...] = field(default_factory=tuple)
    uptime_24h:                   tuple[UptimePercentage, ...] = field(default_factory=tuple)
    uptime_720h:                  tuple[UptimePercentage, ...] = field(default_factory=tuple)

    # Passthrough context
    tenant_id:                    str = ""
    trace_id:                     str = ""
    case_id:                      str = ""
    slug:                         str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":                    self.tool_name,
            "collected_at":                 self.collected_at,
            "source":                       self.source,
            "data_available":               self.data_available.value,
            "error":                        self.error,
            "monitors":                     [m.to_dict() for m in self.monitors],
            "monitors_currently_down":      list(self.monitors_currently_down),
            "monitors_down_at_ticket_time": [o.to_dict() for o in self.monitors_down_at_ticket_time],
            "active_incident":              None if self.active_incident is None
                                             else self.active_incident.to_dict(),
            "maintenance_windows":          [m.to_dict() for m in self.maintenance_windows],
            "uptime_24h":                   [u.to_dict() for u in self.uptime_24h],
            "uptime_720h":                  [u.to_dict() for u in self.uptime_720h],
            "tenant_id":                    self.tenant_id,
            "trace_id":                     self.trace_id,
            "case_id":                      self.case_id,
            "slug":                         self.slug,
        }


# ── Server / infrastructure evidence ─────────────────────────────────────────

@dataclass(frozen=True)
class ComponentStatus:
    """Per-component health snapshot (used by SERVERTOOL)."""
    component_name:   str
    is_up:            bool
    response_time_ms: int | None = None
    reason:           str        = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_name":   self.component_name,
            "is_up":            self.is_up,
            "response_time_ms": self.response_time_ms,
            "reason":           self.reason,
        }


@dataclass(frozen=True)
class ServerHealthEvidence:
    """
    Canonical output of SERVERTOOL — server-level health snapshot.

    Uses the same status page as METRICTOOL but distils it into
    infrastructure-component form.
    """
    tool_name:                str
    collected_at:             str
    source:                   str = "uptime_kuma"

    data_available:           DataAvailability = DataAvailability.AVAILABLE
    error:                    str = ""

    infrastructure_status:    tuple[ComponentStatus, ...] = field(default_factory=tuple)
    any_infrastructure_down:  bool = False
    down_components:          tuple[str, ...] = field(default_factory=tuple)

    tenant_id:                str = ""
    trace_id:                 str = ""
    case_id:                  str = ""
    slug:                     str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":                self.tool_name,
            "collected_at":             self.collected_at,
            "source":                   self.source,
            "data_available":           self.data_available.value,
            "error":                    self.error,
            "infrastructure_status":    [c.to_dict() for c in self.infrastructure_status],
            "any_infrastructure_down":  self.any_infrastructure_down,
            "down_components":          list(self.down_components),
            "tenant_id":                self.tenant_id,
            "trace_id":                 self.trace_id,
            "case_id":                  self.case_id,
            "slug":                     self.slug,
        }


# ── Dashboard snapshot ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class DashboardSnapshot:
    """
    Raw status page snapshot — the untransformed response of
    GET /api/status-page/<slug>. Kept for audit / debugging.
    """
    slug:              str
    title:             str
    published:         bool
    active_incident:   IncidentInfo | None       = None
    maintenance_list:  tuple[MaintenanceInfo, ...] = field(default_factory=tuple)
    monitor_ids:       tuple[str, ...]           = field(default_factory=tuple)
    fetched_at:        str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug":             self.slug,
            "title":            self.title,
            "published":        self.published,
            "active_incident":  None if self.active_incident is None
                                 else self.active_incident.to_dict(),
            "maintenance_list": [m.to_dict() for m in self.maintenance_list],
            "monitor_ids":      list(self.monitor_ids),
            "fetched_at":       self.fetched_at,
        }
