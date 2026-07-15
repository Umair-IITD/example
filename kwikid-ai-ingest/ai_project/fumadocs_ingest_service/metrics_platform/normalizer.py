"""
metrics_platform/normalizer.py

Sprint 2.50: Normalise raw Uptime Kuma responses into the canonical
`MetricsEvidence` and `ServerHealthEvidence` domain models.

The collector never sees Uptime Kuma's raw JSON — this module is the
canonical boundary.

Design rules
------------
- Pure functions. No I/O.
- No exceptions escape — malformed input yields the safest canonical shape
  (empty tuples, `data_available=PARTIAL`).
- No coupling to case_engine or freshdesk.

Dependency direction
--------------------
    normalizer.py → stdlib + metrics_platform.{models, prometheus_parser}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from metrics_platform.models import (
    ComponentStatus,
    DashboardSnapshot,
    DataAvailability,
    IncidentInfo,
    MaintenanceInfo,
    MetricsEvidence,
    MonitorStatus,
    OutageEvent,
    ServerHealthEvidence,
    UptimePercentage,
)
from metrics_platform.prometheus_parser import filter_by_name_keywords, parse_prometheus_text


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _to_iso(value: Any) -> str:
    """
    Uptime Kuma emits timestamps as `"YYYY-MM-DD HH:MM:SS.mmm"` (server-local).
    Store them exactly as received; downstream code can attach tz if needed.
    """
    if value is None:
        return ""
    return str(value).strip()


# ── Public: build MetricsEvidence ─────────────────────────────────────────────

def build_metrics_evidence(
    *,
    tool_name:               str,
    prometheus_text:         str | None,
    status_page:             dict[str, Any] | None,
    heartbeats:              dict[str, Any] | None,
    symptom_keywords:        Iterable[str] = (),
    ticket_created_at_iso:   str | None = None,
    tenant_id:               str = "",
    trace_id:                str = "",
    case_id:                 str = "",
    slug:                    str = "",
    availability:            DataAvailability = DataAvailability.AVAILABLE,
    error:                   str = "",
) -> MetricsEvidence:
    """
    Combine any subset of the three Uptime Kuma data sources into a canonical
    `MetricsEvidence`. Any None input is treated as "not fetched" (not
    "failed") — the caller sets `availability` to signal partial fetches.
    """
    all_monitors: tuple[MonitorStatus, ...] = ()
    if prometheus_text:
        result = parse_prometheus_text(prometheus_text)
        all_monitors = result.monitors

    if symptom_keywords:
        all_monitors = filter_by_name_keywords(all_monitors, symptom_keywords)

    monitors_down = tuple(m.monitor_name for m in all_monitors if m.is_down)

    active_incident = _extract_active_incident(status_page)
    maintenance = _extract_maintenance(status_page)

    outages = ()
    if heartbeats and ticket_created_at_iso:
        outages = _extract_outages_at_ticket_time(
            heartbeats.get("heartbeatList") or {}, ticket_created_at_iso,
        )
    uptime_24h = _extract_uptime(heartbeats, window_hours=24)
    uptime_720h = _extract_uptime(heartbeats, window_hours=720)

    return MetricsEvidence(
        tool_name=tool_name,
        collected_at=_iso_now(),
        data_available=availability,
        error=error,
        monitors=all_monitors,
        monitors_currently_down=monitors_down,
        monitors_down_at_ticket_time=outages,
        active_incident=active_incident,
        maintenance_windows=maintenance,
        uptime_24h=uptime_24h,
        uptime_720h=uptime_720h,
        tenant_id=tenant_id,
        trace_id=trace_id,
        case_id=case_id,
        slug=slug,
    )


# ── Public: build ServerHealthEvidence ────────────────────────────────────────

def build_server_health_evidence(
    *,
    tool_name:      str,
    status_page:    dict[str, Any] | None,
    heartbeats:     dict[str, Any] | None,
    component_keywords: Iterable[str] = ("db", "database", "api", "queue", "worker"),
    tenant_id:      str = "",
    trace_id:       str = "",
    case_id:        str = "",
    slug:           str = "",
    availability:   DataAvailability = DataAvailability.AVAILABLE,
    error:          str = "",
) -> ServerHealthEvidence:
    """
    Distil the same status-page + heartbeats data into component-level
    infrastructure status.

    Grouping strategy: any monitor whose name contains one of
    `component_keywords` becomes a `ComponentStatus`. Latest heartbeat
    determines up/down; response_time_ms comes from the ping value.
    """
    components: list[ComponentStatus] = []
    latest = _latest_heartbeats(heartbeats)
    kws = tuple(k.lower() for k in component_keywords if k)

    for monitor_ref in _walk_status_page_monitors(status_page):
        name = str(monitor_ref.get("name") or "").strip()
        if not name:
            continue
        low = name.lower()
        if kws and not any(k in low for k in kws):
            continue
        monitor_id = str(monitor_ref.get("id") or "")
        hb = latest.get(monitor_id)
        if hb is None:
            components.append(ComponentStatus(
                component_name=name, is_up=False, reason="no_heartbeat"
            ))
            continue
        status_int = _safe_int(hb.get("status"))
        is_up = status_int == 1
        ping = _safe_int(hb.get("ping"))
        components.append(ComponentStatus(
            component_name=name,
            is_up=is_up,
            response_time_ms=ping,
            reason=str(hb.get("msg") or "") if not is_up else "",
        ))

    down = tuple(c.component_name for c in components if not c.is_up)
    return ServerHealthEvidence(
        tool_name=tool_name,
        collected_at=_iso_now(),
        data_available=availability,
        error=error,
        infrastructure_status=tuple(components),
        any_infrastructure_down=bool(down),
        down_components=down,
        tenant_id=tenant_id,
        trace_id=trace_id,
        case_id=case_id,
        slug=slug,
    )


# ── Public: build DashboardSnapshot ───────────────────────────────────────────

def build_dashboard_snapshot(
    *,
    status_page:  dict[str, Any] | None,
    slug:         str,
) -> DashboardSnapshot | None:
    if not status_page:
        return None
    config = status_page.get("config") or {}
    active_incident = _extract_active_incident(status_page)
    maintenance = _extract_maintenance(status_page)
    monitor_ids = tuple(
        str(m.get("id"))
        for m in _walk_status_page_monitors(status_page)
        if m.get("id") is not None
    )
    return DashboardSnapshot(
        slug=      slug,
        title=     str(config.get("title") or ""),
        published= bool(config.get("published", False)),
        active_incident=active_incident,
        maintenance_list=maintenance,
        monitor_ids=monitor_ids,
        fetched_at=_iso_now(),
    )


# ── Internal parsers ─────────────────────────────────────────────────────────

def _extract_active_incident(status_page: dict[str, Any] | None) -> IncidentInfo | None:
    if not status_page:
        return None
    incident = status_page.get("incident")
    if not isinstance(incident, dict):
        return None
    return IncidentInfo(
        incident_id=str(incident.get("id") or ""),
        title=      str(incident.get("title") or ""),
        content=    str(incident.get("content") or "")[:2000],
        style=      str(incident.get("style") or ""),
        created_at= _to_iso(incident.get("createdDate") or incident.get("created_at")),
        pin=        bool(incident.get("pin", False)),
    )


def _extract_maintenance(status_page: dict[str, Any] | None) -> tuple[MaintenanceInfo, ...]:
    if not status_page:
        return ()
    raw = status_page.get("maintenanceList") or []
    if not isinstance(raw, list):
        return ()
    out: list[MaintenanceInfo] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        out.append(MaintenanceInfo(
            maintenance_id=str(m.get("id") or ""),
            title=         str(m.get("title") or ""),
            description=   str(m.get("description") or ""),
            strategy=      str(m.get("strategy") or "manual"),
            active=        bool(m.get("active", False)),
            start_at=      _to_iso(m.get("start_date") or m.get("startDate")) or None,
            end_at=        _to_iso(m.get("end_date") or m.get("endDate")) or None,
        ))
    return tuple(out)


def _extract_outages_at_ticket_time(
    heartbeat_list: dict[str, Any],
    ticket_created_at_iso: str,
) -> tuple[OutageEvent, ...]:
    """
    Walk each monitor's heartbeat window; identify runs of status=0 (DOWN)
    that span the ticket-creation instant.

    Robust to missing timestamps and to monitors with no history yet.

    Uptime Kuma emits naive-UTC timestamps in the heartbeat list; the ticket
    creation time may be tz-aware (Freshdesk sends `Z` / `+00:00`). We
    normalize BOTH to naive-UTC before comparing so datetime.compare() never
    raises TypeError.
    """
    if not isinstance(heartbeat_list, dict) or not heartbeat_list:
        return ()

    try:
        ticket_dt = _parse_iso(ticket_created_at_iso)
    except Exception:
        return ()
    if ticket_dt is None:
        return ()
    ticket_dt = _to_naive_utc(ticket_dt)

    outages: list[OutageEvent] = []
    for monitor_id, records in heartbeat_list.items():
        if not isinstance(records, list) or not records:
            continue
        run_start: datetime | None = None
        run_message: str = ""
        for rec in records:
            if not isinstance(rec, dict):
                continue
            status_int = _safe_int(rec.get("status"))
            ts = _parse_iso(rec.get("time"))
            if ts is None:
                continue
            ts = _to_naive_utc(ts)
            if status_int == 0:
                if run_start is None:
                    run_start = ts
                    run_message = str(rec.get("msg") or "")
            else:
                if run_start is not None and run_start <= ticket_dt <= ts:
                    duration = int((ts - run_start).total_seconds())
                    outages.append(OutageEvent(
                        monitor_id=str(monitor_id),
                        monitor_name=str(monitor_id),  # heartbeat list keys are IDs
                        started_at=run_start.isoformat(),
                        ended_at=ts.isoformat(),
                        duration_seconds=duration,
                        message=run_message,
                        still_ongoing=False,
                    ))
                run_start = None
                run_message = ""
        # Ongoing run at end of window that includes the ticket time.
        if run_start is not None and run_start <= ticket_dt:
            duration = int((ticket_dt - run_start).total_seconds())
            outages.append(OutageEvent(
                monitor_id=str(monitor_id),
                monitor_name=str(monitor_id),
                started_at=run_start.isoformat(),
                ended_at=None,
                duration_seconds=duration,
                message=run_message,
                still_ongoing=True,
            ))
    return tuple(outages)


def _extract_uptime(
    heartbeats: dict[str, Any] | None,
    *,
    window_hours: int,
) -> tuple[UptimePercentage, ...]:
    if not heartbeats:
        return ()
    raw = heartbeats.get("uptimeList") or {}
    if not isinstance(raw, dict):
        return ()
    suffix = f"_{window_hours}"
    out: list[UptimePercentage] = []
    for key, val in raw.items():
        if not isinstance(key, str) or not key.endswith(suffix):
            continue
        monitor_id = key[:-len(suffix)]
        try:
            ratio = float(val)
        except (TypeError, ValueError):
            continue
        out.append(UptimePercentage(
            monitor_id=monitor_id,
            window_hours=window_hours,
            ratio=max(0.0, min(1.0, ratio)),
        ))
    return tuple(out)


def _walk_status_page_monitors(status_page: dict[str, Any] | None):
    if not status_page:
        return
    groups = status_page.get("publicGroupList") or []
    if not isinstance(groups, list):
        return
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        for m in grp.get("monitorList") or []:
            if isinstance(m, dict):
                yield m


def _latest_heartbeats(heartbeats: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Take the last heartbeat per monitor from the heartbeat list."""
    if not heartbeats:
        return {}
    hbl = heartbeats.get("heartbeatList") or {}
    if not isinstance(hbl, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for monitor_id, records in hbl.items():
        if isinstance(records, list) and records and isinstance(records[-1], dict):
            out[str(monitor_id)] = records[-1]
    return out


# ── Utility ─────────────────────────────────────────────────────────────────

def _to_naive_utc(dt: datetime) -> datetime:
    """
    Return a naive datetime whose clock value is UTC.
    If dt is already naive, assume it is already UTC (Uptime Kuma's default).
    If dt is aware, convert to UTC and strip tzinfo.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Uptime Kuma emits "YYYY-MM-DD HH:MM:SS.mmm" without timezone.
    try:
        # Try full ISO with Z first
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    # Fallback: replace space with T (Uptime Kuma format).
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
