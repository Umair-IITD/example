"""
metrics_platform/prometheus_parser.py

Sprint 2.50: Pure-function Prometheus text-exposition parser for Uptime Kuma.

Only the four metrics Uptime Kuma exposes are recognised:

    monitor_status              gauge, 0=DOWN 1=UP 2=PENDING 3=MAINTENANCE
    monitor_response_time       gauge, milliseconds
    monitor_cert_days_remaining gauge, integer days
    monitor_cert_is_valid       gauge, 0 or 1

All lines that do not match `^monitor_\\w+\\{...\\}\\s+<value>` are ignored,
including `# HELP`, `# TYPE`, blank lines, and any other metric families a
future Uptime Kuma release might add.

Dependency direction
--------------------
    prometheus_parser.py → stdlib only.
    Consumes:   raw prometheus text.
    Produces:   PrometheusQueryResult (models.py).
"""
from __future__ import annotations

import re
from typing import Iterable

from metrics_platform.models import (
    MetricPoint,
    MonitorStatus,
    MonitorStatusValue,
    MonitorType,
    PrometheusQueryResult,
)

# Compiled at import time — the parser is on the hot path of every investigation.
_LINE_RE = re.compile(
    r'^(monitor_[A-Za-z_][A-Za-z_0-9]*)\{([^}]*)\}\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)',
    re.MULTILINE,
)
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')

# The four label keys Uptime Kuma emits, verified live.
_LABEL_MONITOR_NAME     = "monitor_name"
_LABEL_MONITOR_TYPE     = "monitor_type"
_LABEL_MONITOR_URL      = "monitor_url"
_LABEL_MONITOR_HOSTNAME = "monitor_hostname"
_LABEL_MONITOR_PORT     = "monitor_port"


def parse_prometheus_text(raw: str) -> PrometheusQueryResult:
    """
    Parse the entire body of Uptime Kuma's `/metrics` endpoint into a
    `PrometheusQueryResult`.

    Robust against:
      - `# HELP` / `# TYPE` comment lines
      - blank lines
      - unknown metric families
      - re-ordered labels
      - escaped quotes inside label values
    """
    if not raw:
        return PrometheusQueryResult(metric_points=(), monitors=())

    points: list[MetricPoint] = []
    per_monitor: dict[str, dict[str, object]] = {}

    for metric_name, labels_raw, value_raw in _LINE_RE.findall(raw):
        try:
            value = float(value_raw)
        except ValueError:
            continue

        labels = _parse_labels(labels_raw)
        points.append(MetricPoint(
            metric_name=metric_name, value=value, labels=labels
        ))
        _fold_into_monitor(per_monitor, metric_name, value, labels)

    monitors = tuple(sorted(
        (_finalise_monitor(name, rec) for name, rec in per_monitor.items()),
        key=lambda m: m.monitor_name,
    ))
    return PrometheusQueryResult(metric_points=tuple(points), monitors=monitors)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _parse_labels(labels_raw: str) -> dict[str, str]:
    """
    Convert `foo="a",bar="b\\"c"` into `{"foo": "a", "bar": 'b"c'}`.
    """
    out: dict[str, str] = {}
    for k, v in _LABEL_RE.findall(labels_raw):
        # Unescape the two common escapes: \" and \\.
        v = v.replace('\\"', '"').replace("\\\\", "\\")
        out[k] = v
    return out


def _fold_into_monitor(
    accum: dict[str, dict[str, object]],
    metric_name: str,
    value: float,
    labels: dict[str, str],
) -> None:
    name = labels.get(_LABEL_MONITOR_NAME, "").strip()
    if not name:
        # A monitor line without monitor_name is malformed — drop it.
        return
    rec = accum.setdefault(name, {
        "monitor_type":     labels.get(_LABEL_MONITOR_TYPE, ""),
        "monitor_url":      labels.get(_LABEL_MONITOR_URL, ""),
        "monitor_hostname": labels.get(_LABEL_MONITOR_HOSTNAME, ""),
        "monitor_port":     labels.get(_LABEL_MONITOR_PORT, ""),
    })
    # Later lines may carry richer type/url info — fill blanks.
    for k in (_LABEL_MONITOR_TYPE, _LABEL_MONITOR_URL,
              _LABEL_MONITOR_HOSTNAME, _LABEL_MONITOR_PORT):
        if not rec.get(k):
            v = labels.get(k, "")
            if v:
                rec[k] = v

    if metric_name == "monitor_status":
        rec["status"] = int(value)
    elif metric_name == "monitor_response_time":
        rec["response_time_ms"] = int(value)
    elif metric_name == "monitor_cert_days_remaining":
        rec["cert_days_remaining"] = int(value)
    elif metric_name == "monitor_cert_is_valid":
        rec["cert_is_valid"] = bool(int(value))
    # Other monitor_* metrics are recorded as MetricPoint but not folded here.


def _finalise_monitor(name: str, rec: dict[str, object]) -> MonitorStatus:
    return MonitorStatus(
        monitor_name=       name,
        monitor_type=       MonitorType.from_str(str(rec.get("monitor_type", ""))),
        monitor_url=        str(rec.get("monitor_url", "")),
        monitor_hostname=   str(rec.get("monitor_hostname", "")),
        monitor_port=       str(rec.get("monitor_port", "")),
        status=             MonitorStatusValue.from_int(rec.get("status")),          # type: ignore[arg-type]
        response_time_ms=   rec.get("response_time_ms"),                             # type: ignore[assignment]
        cert_days_remaining=rec.get("cert_days_remaining"),                          # type: ignore[assignment]
        cert_is_valid=      rec.get("cert_is_valid"),                                # type: ignore[assignment]
    )


# ── Convenience filters ─────────────────────────────────────────────────────

def filter_by_name_keywords(
    monitors: Iterable[MonitorStatus],
    keywords: Iterable[str],
    *,
    case_sensitive: bool = False,
) -> tuple[MonitorStatus, ...]:
    """
    Filter monitors whose name contains ANY of the given keywords.

    Used by MetricTool to narrow the fleet down to the tenant/service the
    ticket concerns. Case-insensitive by default.
    """
    kws = [k.strip() for k in keywords if k and k.strip()]
    if not kws:
        return tuple(monitors)
    if not case_sensitive:
        kws = [k.lower() for k in kws]
    out: list[MonitorStatus] = []
    for m in monitors:
        haystack = m.monitor_name if case_sensitive else m.monitor_name.lower()
        if any(kw in haystack for kw in kws):
            out.append(m)
    return tuple(out)
