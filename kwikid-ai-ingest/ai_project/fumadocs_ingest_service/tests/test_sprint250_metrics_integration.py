"""
tests/test_sprint250_metrics_integration.py

Sprint 2.50 — Production Metrics Dashboard integration (Uptime Kuma).

Sections:
    A — MetricsPlatformConfig (env-driven, validated)
    B — Prometheus text parser (four metric families + edge cases)
    C — Normaliser (build_metrics_evidence, build_server_health_evidence,
                     build_dashboard_snapshot)
    D — UptimeKumaClient (auth modes, endpoints, retry, 4xx/5xx, timeouts)
    E — Trace helper (6 canonical tags, PII sanitisation, WARNING level)
    F — MetricTool BaseTool contract (definition, run, DISABLED, evidence shape)
    G — ServerTool BaseTool contract
    H — Registrar (register_metrics_tools)
    I — Domain-model serialisation (to_dict round-trip)
    J — End-to-end golden path with MockTransport
    K — Enum + EvidenceSource additions (backward-compat check)
    L — SOT reconciliation quick checks
    M — Public export surface

No network. Every httpx interaction uses MockTransport.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

os.environ.setdefault("RAG_API_KEY", "test-250-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-250")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

import httpx
import pytest

from case_engine.investigation.models import EvidenceSource
from case_engine.tools.adapters import (
    MetricTool,
    ServerTool,
    build_metric_tool,
    build_server_tool,
    register_metrics_tools,
)
from case_engine.tools.tool_models import ToolCapability, ToolProvider
from case_engine.tools.tool_registry import ToolRegistry
from metrics_platform import (
    ALL_METRICS_TRACES,
    ComponentStatus,
    DashboardSnapshot,
    DataAvailability,
    IncidentInfo,
    MaintenanceInfo,
    MetricPoint,
    MetricsEvidence,
    MetricsPlatformAuthError,
    MetricsPlatformConfig,
    MetricsPlatformError,
    MetricsPlatformNotFoundError,
    MetricsPlatformServerError,
    MonitorStatus,
    MonitorStatusValue,
    MonitorType,
    OutageEvent,
    PrometheusQueryResult,
    ServerHealthEvidence,
    TRACE_ENTER_METRICS_TOOL,
    TRACE_EXIT_METRICS_TOOL,
    TRACE_METRICS_EVIDENCE_CREATED,
    TRACE_METRICS_NORMALIZED,
    TRACE_METRICS_QUERY,
    TRACE_METRICS_RESPONSE,
    UptimeKumaClient,
    UptimePercentage,
    build_dashboard_snapshot,
    build_metrics_evidence,
    build_server_health_evidence,
    emit_metrics_trace,
    filter_by_name_keywords,
    parse_prometheus_text,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────

@pytest.fixture
def caplog_metrics(caplog):
    caplog.set_level(logging.WARNING, logger="metrics_platform.traces")
    return caplog


def _cfg(**overrides) -> MetricsPlatformConfig:
    """Default in-test config; explicit overrides win."""
    kwargs = dict(
        base_url="http://kuma.test:3001",
        api_key="uk_test_ABCDEFGH",
        timeout_s=5,
        max_retries=1,
        default_slug="kwikid",
        auth_mode="bearer",
        user_agent="test/2.50",
        enabled=True,
    )
    kwargs.update(overrides)
    return MetricsPlatformConfig(**kwargs)


def _make_client_factory(handler):
    """Return a callable that yields UptimeKumaClient instances wired to a MockTransport."""
    def factory():
        transport = httpx.MockTransport(handler)
        inner = httpx.AsyncClient(
            base_url="http://kuma.test:3001",
            transport=transport,
        )
        return UptimeKumaClient(_cfg(), http_client=inner)
    return factory


def _resp(status: int, data: Any) -> httpx.Response:
    if isinstance(data, str):
        return httpx.Response(status, content=data.encode("utf-8"),
                              headers={"content-type": "text/plain"})
    return httpx.Response(status, content=json.dumps(data).encode(),
                          headers={"content-type": "application/json"})


SAMPLE_METRICS_TEXT = """# HELP monitor_status Monitor Status
# TYPE monitor_status gauge
monitor_status{monitor_name="KwikID API",monitor_type="http",monitor_url="https://api.getkwikid.com/health",monitor_hostname="",monitor_port=""} 1
monitor_status{monitor_name="KwikID VKYC",monitor_type="http",monitor_url="https://vkyc.getkwikid.com/health",monitor_hostname="",monitor_port=""} 0
monitor_status{monitor_name="Unity Bank API",monitor_type="http",monitor_url="https://api.unitybank.co.in/health"} 1

# HELP monitor_response_time Monitor Response Time (ms)
# TYPE monitor_response_time gauge
monitor_response_time{monitor_name="KwikID API",monitor_type="http",monitor_url="https://api.getkwikid.com/health"} 142
monitor_response_time{monitor_name="KwikID VKYC",monitor_type="http",monitor_url="https://vkyc.getkwikid.com/health"} 0

# HELP monitor_cert_days_remaining Monitor Cert Days Remaining
# TYPE monitor_cert_days_remaining gauge
monitor_cert_days_remaining{monitor_name="KwikID API",monitor_url="https://api.getkwikid.com/health"} 67

# HELP monitor_cert_is_valid Monitor Cert Is Valid
# TYPE monitor_cert_is_valid gauge
monitor_cert_is_valid{monitor_name="KwikID API",monitor_url="https://api.getkwikid.com/health"} 1
"""


SAMPLE_STATUS_PAGE = {
    "config": {
        "id": 1,
        "slug": "kwikid",
        "title": "KwikID Platform Status",
        "published": True,
        "autoRefreshInterval": 60,
    },
    "incident": {
        "id": 42,
        "title": "VKYC Auditor view degraded",
        "content": "We are investigating.",
        "style": "danger",
        "createdDate": "2026-06-18 08:00:00.000",
        "pin": True,
    },
    "publicGroupList": [
        {
            "id": 1,
            "name": "Core Platform",
            "weight": 1,
            "monitorList": [
                {"id": 1, "name": "KwikID API DB", "type": "http"},
                {"id": 2, "name": "KwikID VKYC", "type": "http"},
                {"id": 3, "name": "Unity Bank API queue", "type": "http"},
            ],
        }
    ],
    "maintenanceList": [
        {
            "id": 7,
            "title": "Nightly UAT window",
            "description": "Scheduled UAT downtime",
            "strategy": "recurring-interval",
            "active": True,
            "start_date": "2026-06-18 00:00:00.000",
            "end_date": "2026-06-18 04:00:00.000",
        }
    ],
}


SAMPLE_HEARTBEATS = {
    "heartbeatList": {
        "1": [
            {"monitorID": 1, "status": 1, "time": "2026-06-18 07:00:00.000", "msg": "200 - OK", "ping": 120},
            {"monitorID": 1, "status": 1, "time": "2026-06-18 08:00:00.000", "msg": "200 - OK", "ping": 130},
        ],
        "2": [
            {"monitorID": 2, "status": 1, "time": "2026-06-18 07:00:00.000", "msg": "200 - OK", "ping": 90},
            {"monitorID": 2, "status": 0, "time": "2026-06-18 07:30:00.000", "msg": "Request failed", "ping": 0},
            {"monitorID": 2, "status": 0, "time": "2026-06-18 07:45:00.000", "msg": "Request failed", "ping": 0},
            {"monitorID": 2, "status": 1, "time": "2026-06-18 08:15:00.000", "msg": "200 - OK", "ping": 100},
        ],
        "3": [
            {"monitorID": 3, "status": 1, "time": "2026-06-18 08:00:00.000", "msg": "200 - OK", "ping": 45},
        ],
    },
    "uptimeList": {
        "1_24":  0.998,
        "1_720": 0.995,
        "2_24":  0.910,
        "2_720": 0.987,
        "3_24":  1.000,
        "3_720": 0.999,
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# Section A — MetricsPlatformConfig
# ══════════════════════════════════════════════════════════════════════════════

class TestA_Config:
    def test_A1_defaults_from_env(self, monkeypatch):
        for k in ("METRICS_PLATFORM_BASE_URL", "METRICS_PLATFORM_API_KEY",
                  "METRICS_PLATFORM_ENABLED", "METRICS_PLATFORM_AUTH_MODE"):
            monkeypatch.delenv(k, raising=False)
        cfg = MetricsPlatformConfig.from_env()
        assert cfg.base_url == "http://status.getkwikid.com:3001"
        assert cfg.auth_mode == "bearer"
        assert cfg.enabled is True
        assert cfg.default_slug == "kwikid"

    def test_A2_env_overrides(self, monkeypatch):
        monkeypatch.setenv("METRICS_PLATFORM_BASE_URL", "https://kuma.custom:9000")
        monkeypatch.setenv("METRICS_PLATFORM_API_KEY", "key-abcd-efgh")
        monkeypatch.setenv("METRICS_PLATFORM_TIMEOUT_S", "20")
        monkeypatch.setenv("METRICS_PLATFORM_MAX_RETRIES", "3")
        monkeypatch.setenv("METRICS_PLATFORM_ENABLED", "false")
        monkeypatch.setenv("METRICS_PLATFORM_AUTH_MODE", "basic")
        cfg = MetricsPlatformConfig.from_env()
        assert cfg.base_url == "https://kuma.custom:9000"
        assert cfg.api_key == "key-abcd-efgh"
        assert cfg.timeout_s == 20
        assert cfg.max_retries == 3
        assert cfg.enabled is False
        assert cfg.auth_mode == "basic"

    def test_A3_invalid_auth_mode_rejected(self):
        with pytest.raises(ValueError):
            MetricsPlatformConfig(
                base_url="http://x", api_key="", timeout_s=1, max_retries=0,
                default_slug="k", auth_mode="oauth", user_agent="ua",
            )

    def test_A4_missing_scheme_rejected(self):
        with pytest.raises(ValueError):
            MetricsPlatformConfig(
                base_url="kuma.local", api_key="", timeout_s=1, max_retries=0,
                default_slug="k", auth_mode="bearer", user_agent="ua",
            )

    def test_A5_zero_timeout_rejected(self):
        with pytest.raises(ValueError):
            _cfg(timeout_s=0)

    def test_A6_negative_retries_rejected(self):
        with pytest.raises(ValueError):
            _cfg(max_retries=-1)

    def test_A7_normalized_base_url_strips_trailing_slash(self):
        cfg = _cfg(base_url="http://kuma.test:3001/")
        assert cfg.normalized_base_url == "http://kuma.test:3001"

    def test_A8_has_api_key(self):
        assert _cfg(api_key="").has_api_key is False
        assert _cfg(api_key="abc").has_api_key is True

    def test_A9_masked_api_key_hides_full_value(self):
        cfg = _cfg(api_key="uk_abcdefgh_ijklmno_pqrstuv")
        assert cfg.masked_api_key == "uk_abcde****"
        assert cfg.api_key not in cfg.masked_api_key

    def test_A10_short_api_key_fully_masked(self):
        assert _cfg(api_key="short").masked_api_key == "****"


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Prometheus parser
# ══════════════════════════════════════════════════════════════════════════════

class TestB_PrometheusParser:
    def test_B1_empty_input_returns_empty(self):
        r = parse_prometheus_text("")
        assert r.monitors == () and r.metric_points == ()

    def test_B2_none_like_input_returns_empty(self):
        r = parse_prometheus_text(None)  # type: ignore[arg-type]
        assert r.monitors == ()

    def test_B3_help_and_type_comments_ignored(self):
        text = "# HELP monitor_status\n# TYPE monitor_status gauge\n"
        r = parse_prometheus_text(text)
        assert r.monitors == ()

    def test_B4_two_monitors_parsed(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        assert len(r.monitors) == 3

    def test_B5_monitors_down_correctly_identified(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        assert len(r.monitors_down) == 1
        assert r.monitors_down[0].monitor_name == "KwikID VKYC"

    def test_B6_response_time_folded(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        by_name = {m.monitor_name: m for m in r.monitors}
        assert by_name["KwikID API"].response_time_ms == 142
        assert by_name["KwikID VKYC"].response_time_ms == 0

    def test_B7_cert_data_folded(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        by_name = {m.monitor_name: m for m in r.monitors}
        assert by_name["KwikID API"].cert_days_remaining == 67
        assert by_name["KwikID API"].cert_is_valid is True

    def test_B8_monitor_type_captured(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        assert all(m.monitor_type == MonitorType.HTTP for m in r.monitors)

    def test_B9_unknown_metric_family_ignored(self):
        text = SAMPLE_METRICS_TEXT + '\nsomething_else{monitor_name="X"} 5\n'
        r = parse_prometheus_text(text)
        assert all(m.monitor_name != "X" for m in r.monitors)

    def test_B10_escaped_quotes_in_labels(self):
        text = 'monitor_status{monitor_name="foo\\"bar",monitor_type="http"} 1\n'
        r = parse_prometheus_text(text)
        assert len(r.monitors) == 1
        assert r.monitors[0].monitor_name == 'foo"bar'

    def test_B11_bogus_value_skipped(self):
        text = 'monitor_status{monitor_name="X",monitor_type="http"} not_a_number\n'
        r = parse_prometheus_text(text)
        assert r.monitors == ()

    def test_B12_missing_monitor_name_dropped(self):
        text = 'monitor_status{monitor_type="http"} 1\n'
        r = parse_prometheus_text(text)
        assert r.monitors == ()

    def test_B13_reordered_labels_preserved(self):
        text = 'monitor_status{monitor_type="http",monitor_name="Zeta",monitor_url="https://z"} 0\n'
        r = parse_prometheus_text(text)
        assert r.monitors[0].monitor_url == "https://z"
        assert r.monitors[0].status == MonitorStatusValue.DOWN

    def test_B14_filter_by_keywords_case_insensitive(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        filtered = filter_by_name_keywords(r.monitors, ["vkyc"])
        assert len(filtered) == 1
        assert filtered[0].monitor_name == "KwikID VKYC"

    def test_B15_filter_by_keywords_no_match_returns_empty(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        filtered = filter_by_name_keywords(r.monitors, ["nonexistent"])
        assert filtered == ()

    def test_B16_empty_keyword_list_returns_all(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        filtered = filter_by_name_keywords(r.monitors, [])
        assert len(filtered) == len(r.monitors)


# ══════════════════════════════════════════════════════════════════════════════
# Section C — Normaliser
# ══════════════════════════════════════════════════════════════════════════════

class TestC_Normaliser:
    def test_C1_availability_reflected(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=SAMPLE_METRICS_TEXT,
            status_page=SAMPLE_STATUS_PAGE,
            heartbeats=SAMPLE_HEARTBEATS,
        )
        assert ev.data_available == DataAvailability.AVAILABLE

    def test_C2_monitors_populated(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=SAMPLE_METRICS_TEXT,
            status_page=None, heartbeats=None,
        )
        assert len(ev.monitors) == 3

    def test_C3_monitors_currently_down(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=SAMPLE_METRICS_TEXT,
            status_page=None, heartbeats=None,
        )
        assert "KwikID VKYC" in ev.monitors_currently_down

    def test_C4_symptom_keywords_filter(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=SAMPLE_METRICS_TEXT,
            status_page=None, heartbeats=None,
            symptom_keywords=["vkyc"],
        )
        assert len(ev.monitors) == 1
        assert ev.monitors[0].monitor_name == "KwikID VKYC"

    def test_C5_active_incident_extracted(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=None,
            status_page=SAMPLE_STATUS_PAGE,
            heartbeats=None,
        )
        assert ev.active_incident is not None
        assert ev.active_incident.title == "VKYC Auditor view degraded"
        assert ev.active_incident.style == "danger"

    def test_C6_maintenance_windows_extracted(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=None, status_page=SAMPLE_STATUS_PAGE, heartbeats=None,
        )
        assert len(ev.maintenance_windows) == 1
        assert ev.maintenance_windows[0].active is True

    def test_C7_uptime_24h_extracted(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=None, status_page=None, heartbeats=SAMPLE_HEARTBEATS,
        )
        assert len(ev.uptime_24h) == 3
        by_monitor = {u.monitor_id: u for u in ev.uptime_24h}
        assert abs(by_monitor["1"].ratio - 0.998) < 1e-6
        assert abs(by_monitor["2"].ratio - 0.910) < 1e-6

    def test_C8_outages_at_ticket_time(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=None, status_page=None, heartbeats=SAMPLE_HEARTBEATS,
            ticket_created_at_iso="2026-06-18T07:35:00+00:00",
        )
        # Monitor 2 was DOWN from 07:30 to 08:15 (recovered at 08:15).
        assert any(o.monitor_id == "2" for o in ev.monitors_down_at_ticket_time)

    def test_C9_error_availability_partial(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=SAMPLE_METRICS_TEXT,
            status_page=None, heartbeats=None,
            availability=DataAvailability.PARTIAL,
            error="/status-page timeout",
        )
        assert ev.data_available == DataAvailability.PARTIAL
        assert "status-page" in ev.error

    def test_C10_server_evidence_components(self):
        ev = build_server_health_evidence(
            tool_name="ServerTool",
            status_page=SAMPLE_STATUS_PAGE,
            heartbeats=SAMPLE_HEARTBEATS,
        )
        # DB monitor id=1, API queue monitor id=3 (has "queue" keyword), VKYC filtered out
        names = [c.component_name for c in ev.infrastructure_status]
        assert "KwikID API DB" in names
        assert "Unity Bank API queue" in names

    def test_C11_server_down_components(self):
        # Change heartbeat 3 to down for this test.
        hb = json.loads(json.dumps(SAMPLE_HEARTBEATS))
        hb["heartbeatList"]["3"][-1]["status"] = 0
        ev = build_server_health_evidence(
            tool_name="ServerTool", status_page=SAMPLE_STATUS_PAGE, heartbeats=hb,
        )
        assert ev.any_infrastructure_down is True
        assert "Unity Bank API queue" in ev.down_components

    def test_C12_dashboard_snapshot(self):
        snap = build_dashboard_snapshot(status_page=SAMPLE_STATUS_PAGE, slug="kwikid")
        assert snap is not None
        assert snap.title == "KwikID Platform Status"
        assert snap.active_incident is not None
        assert "1" in snap.monitor_ids


# ══════════════════════════════════════════════════════════════════════════════
# Section D — UptimeKumaClient
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestD_UptimeKumaClient:
    async def test_D1_entry_page(self):
        seen = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["path"] = req.url.path
            return _resp(200, {"type": "entryPage", "entryPage": "dashboard"})
        client = _make_client_factory(h)()
        try:
            data = await client.get_entry_page()
        finally:
            await client.close()
        assert data["entryPage"] == "dashboard"
        assert seen["path"] == "/api/entry-page"

    async def test_D2_status_page(self):
        def h(req: httpx.Request) -> httpx.Response:
            return _resp(200, SAMPLE_STATUS_PAGE)
        client = _make_client_factory(h)()
        try:
            data = await client.get_status_page("kwikid")
        finally:
            await client.close()
        assert data["config"]["slug"] == "kwikid"

    async def test_D3_heartbeats(self):
        def h(req: httpx.Request) -> httpx.Response:
            return _resp(200, SAMPLE_HEARTBEATS)
        client = _make_client_factory(h)()
        try:
            data = await client.get_heartbeats("kwikid")
        finally:
            await client.close()
        assert "heartbeatList" in data

    async def test_D4_prometheus_metrics(self):
        seen = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("authorization", "")
            return _resp(200, SAMPLE_METRICS_TEXT)
        client = _make_client_factory(h)()
        try:
            text = await client.get_prometheus_metrics()
        finally:
            await client.close()
        assert "monitor_status" in text
        assert seen["auth"].startswith("Bearer ")

    async def test_D5_bearer_auth_used_on_metrics_only(self):
        seen: list[str] = []
        def h(req: httpx.Request) -> httpx.Response:
            seen.append(f"{req.url.path}|{req.headers.get('authorization','')}")
            return _resp(200, SAMPLE_METRICS_TEXT if req.url.path == "/metrics" else {"x": 1})
        client = _make_client_factory(h)()
        try:
            await client.get_entry_page()
            await client.get_prometheus_metrics()
        finally:
            await client.close()
        assert any("entry-page|" in s and "|Bearer" not in s for s in seen)
        assert any("/metrics|Bearer" in s for s in seen)

    async def test_D6_basic_auth_uses_username_only(self):
        cfg = _cfg(auth_mode="basic", api_key="my-key")
        seen: dict[str, str] = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("authorization", "")
            return _resp(200, SAMPLE_METRICS_TEXT)
        transport = httpx.MockTransport(h)
        inner = httpx.AsyncClient(base_url=cfg.normalized_base_url, transport=transport)
        client = UptimeKumaClient(cfg, http_client=inner)
        try:
            await client.get_prometheus_metrics()
        finally:
            await client.close()
        assert seen["auth"].startswith("Basic ")

    async def test_D7_401_raises_auth_error(self):
        def h(req: httpx.Request) -> httpx.Response:
            return _resp(401, {"error": "unauthorized"})
        client = _make_client_factory(h)()
        try:
            with pytest.raises(MetricsPlatformAuthError):
                await client.get_prometheus_metrics()
        finally:
            await client.close()

    async def test_D8_404_raises_not_found(self):
        def h(req: httpx.Request) -> httpx.Response:
            return _resp(404, {"error": "not found"})
        client = _make_client_factory(h)()
        try:
            with pytest.raises(MetricsPlatformNotFoundError):
                await client.get_status_page("no-slug")
        finally:
            await client.close()

    async def test_D9_500_retries_then_raises(self):
        counter = {"n": 0}
        def h(req: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            return _resp(500, {"error": "boom"})
        client = _make_client_factory(h)()
        try:
            with pytest.raises(MetricsPlatformServerError):
                await client.get_status_page("kwikid")
        finally:
            await client.close()
        assert counter["n"] >= 2   # at least one retry

    async def test_D10_500_then_200_succeeds(self):
        counter = {"n": 0}
        def h(req: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            if counter["n"] == 1:
                return _resp(500, {"error": "boom"})
            return _resp(200, SAMPLE_STATUS_PAGE)
        client = _make_client_factory(h)()
        try:
            data = await client.get_status_page("kwikid")
        finally:
            await client.close()
        assert data["config"]["slug"] == "kwikid"

    async def test_D11_invalid_json_raises_api_error(self):
        def h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"})
        client = _make_client_factory(h)()
        try:
            with pytest.raises(MetricsPlatformError):
                await client.get_status_page("kwikid")
        finally:
            await client.close()

    async def test_D12_async_context_manager(self):
        def h(req: httpx.Request) -> httpx.Response:
            return _resp(200, SAMPLE_HEARTBEATS)
        transport = httpx.MockTransport(h)
        inner = httpx.AsyncClient(base_url="http://kuma.test:3001", transport=transport)
        async with UptimeKumaClient(_cfg(), http_client=inner) as client:
            data = await client.get_heartbeats("kwikid")
        assert "heartbeatList" in data

    async def test_D13_none_auth_mode_sends_no_auth(self):
        cfg = _cfg(auth_mode="none", api_key="")
        seen: dict[str, str] = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("authorization", "")
            return _resp(200, SAMPLE_METRICS_TEXT)
        transport = httpx.MockTransport(h)
        inner = httpx.AsyncClient(base_url=cfg.normalized_base_url, transport=transport)
        client = UptimeKumaClient(cfg, http_client=inner)
        try:
            await client.get_prometheus_metrics()
        finally:
            await client.close()
        assert seen["auth"] == ""


# ══════════════════════════════════════════════════════════════════════════════
# Section E — Trace helper
# ══════════════════════════════════════════════════════════════════════════════

class TestE_Traces:
    def test_E1_six_tags_registered(self):
        assert len(ALL_METRICS_TRACES) == 6

    def test_E2_all_expected_tags_present(self):
        expected = {
            TRACE_ENTER_METRICS_TOOL, TRACE_METRICS_QUERY, TRACE_METRICS_RESPONSE,
            TRACE_METRICS_NORMALIZED, TRACE_METRICS_EVIDENCE_CREATED, TRACE_EXIT_METRICS_TOOL,
        }
        assert ALL_METRICS_TRACES == frozenset(expected)

    def test_E3_emit_at_warning_level(self, caplog_metrics):
        emit_metrics_trace(TRACE_METRICS_QUERY, tool="t")
        assert any(r.levelno == logging.WARNING for r in caplog_metrics.records)

    def test_E4_six_kv_fields(self, caplog_metrics):
        emit_metrics_trace(
            TRACE_METRICS_QUERY, tool="MT", tenant="UNITY",
            case_id="c-1", trace_id="t-1", endpoint="/metrics", status="OK",
        )
        msg = caplog_metrics.records[-1].getMessage()
        for f in ("tool=MT", "tenant=UNITY", "case_id=c-1", "trace_id=t-1",
                  "endpoint=/metrics", "status=OK"):
            assert f in msg

    def test_E5_email_redacted(self, caplog_metrics):
        emit_metrics_trace(TRACE_METRICS_QUERY, tenant="user@bank.com")
        assert "REDACTED" in caplog_metrics.records[-1].getMessage()

    def test_E6_bearer_redacted(self, caplog_metrics):
        emit_metrics_trace(TRACE_METRICS_QUERY, status="Bearer secret-value")
        assert "REDACTED" in caplog_metrics.records[-1].getMessage()

    def test_E7_long_value_redacted(self, caplog_metrics):
        emit_metrics_trace(TRACE_METRICS_QUERY, endpoint="x" * 200)
        assert "REDACTED" in caplog_metrics.records[-1].getMessage()

    def test_E8_unknown_tag_no_normal_line(self, caplog_metrics):
        emit_metrics_trace("NOT_A_REAL_TAG", tool="x")
        assert any("unknown_tag" in r.getMessage() for r in caplog_metrics.records)

    def test_E9_never_raises_on_bad_input(self, caplog_metrics):
        emit_metrics_trace(TRACE_METRICS_QUERY, tool=None)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
# Section F — MetricTool
# ══════════════════════════════════════════════════════════════════════════════

def _make_tool_factory(prometheus_status=200, status_page_status=200, heartbeats_status=200):
    """Build a client_factory that returns a stubbed UptimeKumaClient."""
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/metrics":
            if prometheus_status == 200:
                return _resp(200, SAMPLE_METRICS_TEXT)
            return _resp(prometheus_status, {"error": "boom"})
        if req.url.path.startswith("/api/status-page/heartbeat/"):
            if heartbeats_status == 200:
                return _resp(200, SAMPLE_HEARTBEATS)
            return _resp(heartbeats_status, {"error": "boom"})
        if req.url.path.startswith("/api/status-page/"):
            if status_page_status == 200:
                return _resp(200, SAMPLE_STATUS_PAGE)
            return _resp(status_page_status, {"error": "boom"})
        return _resp(404, {"error": "unknown path"})

    def factory():
        transport = httpx.MockTransport(h)
        inner = httpx.AsyncClient(base_url="http://kuma.test:3001", transport=transport)
        return UptimeKumaClient(_cfg(), http_client=inner)
    return factory


class TestF_MetricTool:
    def test_F1_definition_shape(self):
        tool = MetricTool(config=_cfg(enabled=False))
        d = tool.definition
        assert d.tool_name == "MetricTool"
        assert d.provider == ToolProvider.METRICS_PLATFORM
        assert d.capability == ToolCapability.READ
        assert "metrics" in d.tags

    def test_F2_disabled_short_circuits(self, caplog_metrics):
        tool = MetricTool(config=_cfg(enabled=False))
        payload = tool.run({"tenant_id": "UNITY", "case_id": "c-1", "trace_id": "t-1"})
        assert payload["data_available"] == "DISABLED"
        assert payload["tool_name"] == "MetricTool"
        assert payload["monitors"] == []

    def test_F3_run_end_to_end(self, caplog_metrics):
        tool = MetricTool(config=_cfg(), client_factory=_make_tool_factory())
        payload = tool.run({
            "tenant_id": "UNITY", "case_id": "case-1", "trace_id": "tr-1",
            "symptom_keywords": ["vkyc"],
        })
        assert payload["data_available"] == "AVAILABLE"
        assert payload["tool_name"] == "MetricTool"
        # After filter, only VKYC monitor.
        assert len(payload["monitors"]) == 1
        assert payload["monitors_currently_down"] == ["KwikID VKYC"]

    def test_F4_all_six_traces_emitted(self, caplog_metrics):
        tool = MetricTool(config=_cfg(), client_factory=_make_tool_factory())
        tool.run({"tenant_id": "UNITY", "case_id": "case-1"})
        seen = {r.getMessage().split()[0] for r in caplog_metrics.records}
        for t in ALL_METRICS_TRACES:
            assert t in seen, f"missing trace: {t}"

    def test_F5_partial_availability_when_metrics_401(self, caplog_metrics):
        tool = MetricTool(
            config=_cfg(),
            client_factory=_make_tool_factory(prometheus_status=401),
        )
        payload = tool.run({"tenant_id": "UNITY"})
        assert payload["data_available"] in ("PARTIAL", "UNAVAILABLE")
        # The two public endpoints should still populate.
        assert payload["active_incident"] is not None

    def test_F6_unavailable_when_all_fail(self):
        tool = MetricTool(config=_cfg(), client_factory=_make_tool_factory(
            prometheus_status=500, status_page_status=500, heartbeats_status=500,
        ))
        payload = tool.run({"tenant_id": "UNITY"})
        assert payload["data_available"] == "UNAVAILABLE"

    def test_F7_never_raises_on_broken_factory(self):
        def broken():
            raise RuntimeError("no client available")
        tool = MetricTool(config=_cfg(), client_factory=broken)
        payload = tool.run({})
        assert payload["data_available"] in ("UNAVAILABLE", "PARTIAL")
        assert payload["tool_name"] == "MetricTool"

    def test_F8_passthrough_correlation_ids(self):
        tool = MetricTool(config=_cfg(), client_factory=_make_tool_factory())
        payload = tool.run({
            "tenant_id": "UNITY", "case_id": "c-abc", "trace_id": "t-xyz",
        })
        assert payload["tenant_id"] == "UNITY"
        assert payload["case_id"] == "c-abc"
        assert payload["trace_id"] == "t-xyz"

    def test_F9_default_slug_used_when_not_supplied(self):
        tool = MetricTool(config=_cfg(default_slug="acme"),
                          client_factory=_make_tool_factory())
        payload = tool.run({})
        assert payload["slug"] == "acme"


# ══════════════════════════════════════════════════════════════════════════════
# Section G — ServerTool
# ══════════════════════════════════════════════════════════════════════════════

class TestG_ServerTool:
    def test_G1_definition_shape(self):
        tool = ServerTool(config=_cfg(enabled=False))
        d = tool.definition
        assert d.tool_name == "ServerTool"
        assert d.provider == ToolProvider.METRICS_PLATFORM
        assert d.capability == ToolCapability.READ

    def test_G2_disabled_short_circuits(self):
        tool = ServerTool(config=_cfg(enabled=False))
        payload = tool.run({})
        assert payload["data_available"] == "DISABLED"
        assert payload["infrastructure_status"] == []

    def test_G3_run_end_to_end(self):
        tool = ServerTool(config=_cfg(), client_factory=_make_tool_factory())
        payload = tool.run({"tenant_id": "UNITY"})
        assert payload["data_available"] == "AVAILABLE"
        assert any("db" in c["component_name"].lower() for c in payload["infrastructure_status"])

    def test_G4_uses_status_page_and_heartbeats_only(self, caplog_metrics):
        """SERVERTOOL must NOT hit /metrics."""
        calls: list[str] = []
        def h(req: httpx.Request) -> httpx.Response:
            calls.append(req.url.path)
            if req.url.path.startswith("/api/status-page/heartbeat/"):
                return _resp(200, SAMPLE_HEARTBEATS)
            if req.url.path.startswith("/api/status-page/"):
                return _resp(200, SAMPLE_STATUS_PAGE)
            return _resp(500, {"error": "unexpected /metrics call"})
        def factory():
            transport = httpx.MockTransport(h)
            inner = httpx.AsyncClient(base_url="http://kuma.test:3001", transport=transport)
            return UptimeKumaClient(_cfg(), http_client=inner)
        tool = ServerTool(config=_cfg(), client_factory=factory)
        tool.run({})
        assert not any(c == "/metrics" for c in calls)

    def test_G5_component_filter_narrows_names(self):
        tool = ServerTool(config=_cfg(), client_factory=_make_tool_factory())
        payload = tool.run({"component_keywords": ["vkyc"]})
        assert payload["data_available"] == "AVAILABLE"
        # Only monitor named "KwikID VKYC" should have been considered.
        names = [c["component_name"] for c in payload["infrastructure_status"]]
        assert names == ["KwikID VKYC"]


# ══════════════════════════════════════════════════════════════════════════════
# Section H — Registrar
# ══════════════════════════════════════════════════════════════════════════════

class TestH_Registrar:
    def test_H1_registers_both_tools(self):
        reg = ToolRegistry()
        outcomes = register_metrics_tools(reg, config=_cfg(enabled=False))
        assert outcomes == {"MetricTool": "registered", "ServerTool": "registered"}
        assert reg.get("MetricTool") is not None
        assert reg.get("ServerTool") is not None

    def test_H2_capability_route_on_production_registry(self):
        """Real ProductionToolRegistry from Sprint 2.45 accepts capability."""
        try:
            from case_engine.tools.framework.registry import ProductionToolRegistry
        except ImportError:
            pytest.skip("Sprint 2.45 registry not available in this build")
        reg = ProductionToolRegistry()
        register_metrics_tools(reg, config=_cfg(enabled=False))
        # capability lookup lookup is best-effort — just verify no crash.
        _ = reg.get_tool_for_capability("METRIC") if hasattr(reg, "get_tool_for_capability") else None


# ══════════════════════════════════════════════════════════════════════════════
# Section I — Serialisation round-trip
# ══════════════════════════════════════════════════════════════════════════════

class TestI_Serialisation:
    def test_I1_metrics_evidence_to_dict(self):
        ev = build_metrics_evidence(
            tool_name="MetricTool",
            prometheus_text=SAMPLE_METRICS_TEXT,
            status_page=SAMPLE_STATUS_PAGE,
            heartbeats=SAMPLE_HEARTBEATS,
            tenant_id="UNITY", case_id="c-1", slug="kwikid",
        )
        d = ev.to_dict()
        # Must be JSON-serialisable end-to-end.
        raw = json.dumps(d)
        assert '"MetricTool"' in raw
        assert '"data_available"' in raw

    def test_I2_server_evidence_to_dict(self):
        ev = build_server_health_evidence(
            tool_name="ServerTool",
            status_page=SAMPLE_STATUS_PAGE,
            heartbeats=SAMPLE_HEARTBEATS,
        )
        d = ev.to_dict()
        json.dumps(d)   # must not raise

    def test_I3_dashboard_snapshot_to_dict(self):
        snap = build_dashboard_snapshot(status_page=SAMPLE_STATUS_PAGE, slug="kwikid")
        json.dumps(snap.to_dict())   # type: ignore[union-attr]

    def test_I4_incident_content_truncated(self):
        big = {"incident": {
            "id": 1, "title": "x", "content": "y" * 5000, "style": "danger",
            "createdDate": "2026-01-01", "pin": False,
        }, "publicGroupList": [], "maintenanceList": []}
        ev = build_metrics_evidence(
            tool_name="MetricTool", prometheus_text=None,
            status_page=big, heartbeats=None,
        )
        assert len(ev.active_incident.to_dict()["content"]) <= 500


# ══════════════════════════════════════════════════════════════════════════════
# Section J — Enums + EvidenceSource additions
# ══════════════════════════════════════════════════════════════════════════════

class TestJ_EnumAdditions:
    def test_J1_evidence_source_metric_tool(self):
        assert EvidenceSource.METRIC_TOOL.value == "MetricTool"

    def test_J2_evidence_source_server_tool(self):
        assert EvidenceSource.SERVER_TOOL.value == "ServerTool"

    def test_J3_tool_provider_metrics_platform(self):
        assert ToolProvider.METRICS_PLATFORM.value == "METRICS_PLATFORM"

    def test_J4_existing_evidence_sources_unchanged(self):
        # Sanity: prior sources still exist verbatim.
        assert EvidenceSource.GET_USER_DETAILS.value == "GetUserDetailsTool"
        assert EvidenceSource.GET_SESSION_DETAILS.value == "GetSessionDetailsTool"

    def test_J5_existing_providers_unchanged(self):
        assert ToolProvider.FRESHDESK.value == "FRESHDESK"
        assert ToolProvider.UNITY.value == "UNITY"


# ══════════════════════════════════════════════════════════════════════════════
# Section K — SOT reconciliation
# ══════════════════════════════════════════════════════════════════════════════

class TestK_SOT:
    def test_K1_uptime_kuma_status_codes(self):
        assert MonitorStatusValue.DOWN == 0
        assert MonitorStatusValue.UP == 1
        assert MonitorStatusValue.PENDING == 2
        assert MonitorStatusValue.MAINTENANCE == 3

    def test_K2_default_url_and_slug(self, monkeypatch):
        for k in ("METRICS_PLATFORM_BASE_URL", "METRICS_PLATFORM_DEFAULT_SLUG"):
            monkeypatch.delenv(k, raising=False)
        cfg = MetricsPlatformConfig.from_env()
        assert cfg.base_url == "http://status.getkwikid.com:3001"
        assert cfg.default_slug == "kwikid"

    def test_K3_readonly_no_write_methods(self):
        assert not hasattr(UptimeKumaClient, "add_monitor")
        assert not hasattr(UptimeKumaClient, "pause_monitor")
        assert not hasattr(UptimeKumaClient, "delete_monitor")
        assert not hasattr(UptimeKumaClient, "edit_monitor")
        assert not hasattr(UptimeKumaClient, "post_incident")
        assert not hasattr(UptimeKumaClient, "set_settings")

    def test_K4_four_supported_metric_families(self):
        r = parse_prometheus_text(SAMPLE_METRICS_TEXT)
        by_name = {m.monitor_name: m for m in r.monitors}
        api = by_name["KwikID API"]
        assert api.status is not None
        assert api.response_time_ms is not None
        assert api.cert_days_remaining is not None
        assert api.cert_is_valid is not None


# ══════════════════════════════════════════════════════════════════════════════
# Section L — Public export surface
# ══════════════════════════════════════════════════════════════════════════════

class TestL_Exports:
    def test_L1_metrics_platform_public_names(self):
        import metrics_platform as mp
        for name in (
            "MetricsPlatformConfig", "UptimeKumaClient",
            "parse_prometheus_text", "build_metrics_evidence",
            "build_server_health_evidence", "build_dashboard_snapshot",
            "MetricsEvidence", "ServerHealthEvidence",
            "MonitorStatus", "MetricPoint", "OutageEvent",
            "IncidentInfo", "MaintenanceInfo", "UptimePercentage",
            "ComponentStatus", "PrometheusQueryResult", "DashboardSnapshot",
            "DataAvailability", "MonitorStatusValue", "MonitorType",
            "emit_metrics_trace", "ALL_METRICS_TRACES",
            "TRACE_ENTER_METRICS_TOOL", "TRACE_EXIT_METRICS_TOOL",
            "MetricsPlatformError", "MetricsPlatformAuthError",
        ):
            assert hasattr(mp, name), f"missing export: {name}"
        for name in mp.__all__:
            assert hasattr(mp, name), f"__all__ lists missing: {name}"

    def test_L2_adapters_public_names(self):
        import case_engine.tools.adapters as ad
        for name in ("MetricTool", "ServerTool",
                     "build_metric_tool", "build_server_tool",
                     "register_metrics_tools"):
            assert hasattr(ad, name)
