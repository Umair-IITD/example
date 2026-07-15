"""
tests/test_sprint252_platform_wiring.py

Sprint 2.52 — Platform wiring & runtime stabilization.

Sections:
    A — MetricsPlatformConfig Prometheus credentials + effective_auth_mode
    B — UptimeKumaClient uses Prometheus creds when both set
    C — StartupReport / ComponentStatus / ComponentCheck models
    D — Individual startup checks (Freshdesk, Unity, Metrics, Prometheus,
                                    Database, Audit, Required Env Vars)
    E — validate_startup() end-to-end (registers Unity + Metrics tools,
                                        emits STARTUP_READY summary)
    F — Runtime traces (13 canonical tags, PII sanitization)
    G — Registered production tool inventory (7 = 5 Unity + 2 Metrics)
    H — Public export surface

No network. All httpx interactions use MockTransport where needed.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

os.environ.setdefault("RAG_API_KEY", "test-252-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-252")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

import httpx
import pytest

from app.runtime_traces import (
    ALL_RUNTIME_TRACES,
    TRACE_ENTER_EVIDENCE_BUNDLE,
    TRACE_ENTER_INVESTIGATION,
    TRACE_ENTER_METRICS,
    TRACE_ENTER_TOOL_EXECUTOR,
    TRACE_ENTER_UNITY,
    TRACE_ENTER_WEBHOOK,
    TRACE_EXIT_EVIDENCE_BUNDLE,
    TRACE_EXIT_INVESTIGATION,
    TRACE_EXIT_METRICS,
    TRACE_EXIT_TOOL_EXECUTOR,
    TRACE_EXIT_UNITY,
    TRACE_EXIT_WEBHOOK,
    TRACE_RUNTIME_STOP_LLM_BOUNDARY,
    emit_exception_trace,
    emit_return_trace,
    emit_runtime_trace,
)
from app.startup_validator import (
    ComponentCheck,
    ComponentStatus,
    StartupReport,
    StartupValidationError,
    validate_startup,
)
from case_engine.tools.tool_registry import ToolRegistry
from metrics_platform import UptimeKumaClient
from metrics_platform.config import MetricsPlatformConfig as _MPC


# ── Helpers ───────────────────────────────────────────────────────────────

@pytest.fixture
def caplog_runtime(caplog):
    caplog.set_level(logging.WARNING, logger="app.runtime_traces")
    return caplog


@pytest.fixture
def caplog_startup(caplog):
    caplog.set_level(logging.WARNING, logger="app.startup")
    return caplog


def _clean_env(monkeypatch, keep: dict[str, str] | None = None) -> None:
    """Strip all env vars this sprint reads, then apply `keep`."""
    for var in (
        "UNITY_ENABLED", "UNITY_PASSWORD", "UNITY_USERNAME", "UNITY_BASE_URL",
        "METRICS_PLATFORM_ENABLED", "METRICS_PLATFORM_API_KEY",
        "METRICS_PROMETHEUS_USERNAME", "METRICS_PROMETHEUS_PASSWORD",
        "FRESHDESK_WEBHOOK_ENABLED", "FRESHDESK_WEBHOOK_SECRET",
        "FRESHDESK_WEBHOOK_ENFORCE_HMAC", "FRESHDESK_DOMAIN", "FRESHDESK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    for k, v in (keep or {}).items():
        monkeypatch.setenv(k, v)


def _make_transport(handler):
    return httpx.AsyncClient(
        base_url="https://kuma.test:3001",
        transport=httpx.MockTransport(handler),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section A — MetricsPlatformConfig Prometheus credentials
# ══════════════════════════════════════════════════════════════════════════════

class TestA_MetricsConfigPrometheus:
    def _mk(self, **overrides):
        kwargs = dict(
            base_url="https://kuma.test:3001",
            api_key="uk_bearer_key",
            timeout_s=5, max_retries=1,
            default_slug="kwikid", auth_mode="bearer",
            user_agent="test/2.52",
            enabled=True,
        )
        kwargs.update(overrides)
        return _MPC(**kwargs)

    def test_A1_new_fields_default_empty(self):
        cfg = self._mk()
        assert cfg.prometheus_username == ""
        assert cfg.prometheus_password == ""
        assert cfg.has_prometheus_credentials is False

    def test_A2_both_prometheus_credentials_required_for_true(self):
        # Wave 3 (Sprint 2.53) Part J: password alone is sufficient — the
        # production Prometheus scrape endpoint accepts an empty username
        # and uses the value in METRICS_PROMETHEUS_PASSWORD as the API key.
        cfg = self._mk(prometheus_username="prom_user", prometheus_password="")
        assert cfg.has_prometheus_credentials is False   # username-only is not enough
        cfg = self._mk(prometheus_username="", prometheus_password="prom_pw")
        assert cfg.has_prometheus_credentials is True    # password-only IS enough (Part J)
        cfg = self._mk(prometheus_username="prom_user", prometheus_password="prom_pw")
        assert cfg.has_prometheus_credentials is True

    def test_A3_effective_auth_mode_flips_to_basic_when_prom_set(self):
        cfg = self._mk(auth_mode="bearer",
                       prometheus_username="prom_user",
                       prometheus_password="prom_pw")
        assert cfg.effective_auth_mode == "basic"

    def test_A4_effective_auth_mode_falls_back_to_configured(self):
        cfg = self._mk(auth_mode="none")
        assert cfg.effective_auth_mode == "none"
        cfg = self._mk(auth_mode="bearer")
        assert cfg.effective_auth_mode == "bearer"

    def test_A5_masked_prometheus_username_hides_value(self):
        cfg = self._mk(prometheus_username="prom_scraper_v2",
                       prometheus_password="pw")
        assert cfg.masked_prometheus_username == "pr****"
        assert "prom_scraper_v2" not in cfg.masked_prometheus_username

    def test_A6_short_prometheus_username_fully_masked(self):
        cfg = self._mk(prometheus_username="pu", prometheus_password="pw")
        assert cfg.masked_prometheus_username == "***"

    def test_A7_empty_prometheus_username_masks_to_empty(self):
        cfg = self._mk()
        assert cfg.masked_prometheus_username == ""

    def test_A8_env_reads_new_fields(self, monkeypatch):
        _clean_env(monkeypatch, {
            "METRICS_PROMETHEUS_USERNAME": "scraper_bot",
            "METRICS_PROMETHEUS_PASSWORD": "s3cret",
            "METRICS_PLATFORM_API_KEY": "unused_when_prom_set",
        })
        cfg = _MPC.from_env()
        assert cfg.prometheus_username == "scraper_bot"
        assert cfg.prometheus_password == "s3cret"
        assert cfg.has_prometheus_credentials is True
        assert cfg.effective_auth_mode == "basic"


# ══════════════════════════════════════════════════════════════════════════════
# Section B — UptimeKumaClient uses Prometheus credentials
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestB_ClientPrometheusAuth:
    async def test_B1_prometheus_creds_used_over_bearer_when_both_set(self):
        cfg = _MPC(
            base_url="https://kuma.test:3001", api_key="unused_api_key",
            timeout_s=5, max_retries=0,
            default_slug="kwikid", auth_mode="bearer",  # would normally use bearer
            user_agent="test",
            enabled=True,
            prometheus_username="scraper_bot", prometheus_password="s3cret",
        )
        seen = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("authorization", "")
            return httpx.Response(200, content=b"# HELP x\n",
                                  headers={"content-type": "text/plain"})
        transport = _make_transport(h)
        client = UptimeKumaClient(cfg, http_client=transport)
        try:
            await client.get_prometheus_metrics()
        finally:
            await client.close()
        assert seen["auth"].startswith("Basic ")
        # NOT the api_key — must be the prometheus username/password combo.
        import base64
        decoded = base64.b64decode(seen["auth"].split()[1]).decode()
        assert decoded == "scraper_bot:s3cret"

    async def test_B2_falls_back_to_bearer_when_no_prom_creds(self):
        cfg = _MPC(
            base_url="https://kuma.test:3001", api_key="uk_bearer_key",
            timeout_s=5, max_retries=0,
            default_slug="kwikid", auth_mode="bearer",
            user_agent="test",
            enabled=True,
        )
        seen = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("authorization", "")
            return httpx.Response(200, content=b"# HELP x\n",
                                  headers={"content-type": "text/plain"})
        transport = _make_transport(h)
        client = UptimeKumaClient(cfg, http_client=transport)
        try:
            await client.get_prometheus_metrics()
        finally:
            await client.close()
        assert seen["auth"] == "Bearer uk_bearer_key"

    async def test_B3_no_auth_header_on_public_endpoints_even_with_prom(self):
        cfg = _MPC(
            base_url="https://kuma.test:3001", api_key="",
            timeout_s=5, max_retries=0,
            default_slug="kwikid", auth_mode="bearer",
            user_agent="test",
            enabled=True,
            prometheus_username="scraper_bot", prometheus_password="s3cret",
        )
        seen = {}
        def h(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("authorization", "")
            return httpx.Response(200, content=b'{"config":{}}',
                                  headers={"content-type": "application/json"})
        transport = _make_transport(h)
        client = UptimeKumaClient(cfg, http_client=transport)
        try:
            await client.get_status_page("kwikid")
        finally:
            await client.close()
        # Public endpoint: `auth=None` is passed and _NO_AUTH strips headers.
        assert seen["auth"] == ""


# ══════════════════════════════════════════════════════════════════════════════
# Section C — StartupReport / ComponentStatus / ComponentCheck models
# ══════════════════════════════════════════════════════════════════════════════

class TestC_ReportModels:
    def test_C1_component_status_values(self):
        assert ComponentStatus.OK.value == "OK"
        assert ComponentStatus.WARN.value == "WARN"
        assert ComponentStatus.FAIL.value == "FAIL"
        assert ComponentStatus.SKIPPED.value == "SKIPPED"
        assert ComponentStatus.NOT_WIRED.value == "NOT_WIRED"

    def test_C2_component_check_to_dict(self):
        c = ComponentCheck("Freshdesk", ComponentStatus.OK, detail="ok")
        d = c.to_dict()
        assert d == {"name": "Freshdesk", "status": "OK", "detail": "ok"}

    def test_C3_startup_report_has_failures(self):
        r = StartupReport(
            checks=(ComponentCheck("X", ComponentStatus.OK),
                    ComponentCheck("Y", ComponentStatus.FAIL, detail="down")),
            ready=False,
        )
        assert r.has_failures is True

    def test_C4_startup_report_warnings_only(self):
        r = StartupReport(
            checks=(ComponentCheck("X", ComponentStatus.WARN, detail="soft"),),
        )
        assert len(r.warnings) == 1
        assert r.warnings[0].name == "X"

    def test_C5_startup_report_to_dict_json_safe(self):
        r = StartupReport(
            checks=(ComponentCheck("X", ComponentStatus.OK),),
            registered_tools=("A", "B"),
            ready=True,
        )
        json.dumps(r.to_dict())   # must not raise

    def test_C6_startup_validation_error_carries_items(self):
        exc = StartupValidationError(["FOO", "BAR"])
        assert exc.items == ("FOO", "BAR")


# ══════════════════════════════════════════════════════════════════════════════
# Section D — Individual startup checks
# ══════════════════════════════════════════════════════════════════════════════

class TestD_IndividualChecks:
    def test_D1_freshdesk_disabled_skipped(self, monkeypatch):
        _clean_env(monkeypatch, {"FRESHDESK_WEBHOOK_ENABLED": "false"})
        from app.startup_validator import _check_freshdesk
        c = _check_freshdesk()
        assert c.status == ComponentStatus.SKIPPED

    def test_D2_freshdesk_enforce_hmac_without_secret_fails(self, monkeypatch):
        _clean_env(monkeypatch, {
            "FRESHDESK_WEBHOOK_ENABLED": "true",
            "FRESHDESK_WEBHOOK_ENFORCE_HMAC": "true",
        })
        from app.startup_validator import _check_freshdesk
        c = _check_freshdesk()
        assert c.status == ComponentStatus.FAIL

    def test_D3_freshdesk_enabled_no_secret_warn(self, monkeypatch):
        _clean_env(monkeypatch, {
            "FRESHDESK_WEBHOOK_ENABLED": "true",
            "FRESHDESK_WEBHOOK_ENFORCE_HMAC": "false",
        })
        from app.startup_validator import _check_freshdesk
        c = _check_freshdesk()
        assert c.status == ComponentStatus.WARN

    def test_D4_freshdesk_ok_with_secret_and_domain(self, monkeypatch):
        _clean_env(monkeypatch, {
            "FRESHDESK_WEBHOOK_ENABLED": "true",
            "FRESHDESK_WEBHOOK_SECRET": "s3cret",
            "FRESHDESK_DOMAIN": "test.freshdesk.com",
            "FRESHDESK_API_KEY": "key",
        })
        from app.startup_validator import _check_freshdesk
        c = _check_freshdesk()
        assert c.status == ComponentStatus.OK

    def test_D5_unity_disabled_skipped(self, monkeypatch):
        _clean_env(monkeypatch, {"UNITY_ENABLED": "false"})
        from app.startup_validator import _check_unity
        c = _check_unity()
        assert c.status == ComponentStatus.SKIPPED

    def test_D6_unity_missing_password_warn(self, monkeypatch):
        _clean_env(monkeypatch, {"UNITY_ENABLED": "true"})
        from app.startup_validator import _check_unity
        c = _check_unity()
        assert c.status == ComponentStatus.WARN

    def test_D7_unity_ok_with_credentials(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "true",
            "UNITY_USERNAME": "unity",
            "UNITY_PASSWORD": "unity",
        })
        from app.startup_validator import _check_unity
        c = _check_unity()
        assert c.status == ComponentStatus.OK

    def test_D8_metrics_disabled_skipped(self, monkeypatch):
        _clean_env(monkeypatch, {"METRICS_PLATFORM_ENABLED": "false"})
        from app.startup_validator import _check_metrics_platform
        c = _check_metrics_platform()
        assert c.status == ComponentStatus.SKIPPED

    def test_D9_metrics_no_creds_warn(self, monkeypatch):
        _clean_env(monkeypatch, {"METRICS_PLATFORM_ENABLED": "true"})
        from app.startup_validator import _check_metrics_platform
        c = _check_metrics_platform()
        assert c.status == ComponentStatus.WARN

    def test_D10_metrics_ok_with_api_key(self, monkeypatch):
        _clean_env(monkeypatch, {
            "METRICS_PLATFORM_ENABLED": "true",
            "METRICS_PLATFORM_API_KEY": "uk_abc",
        })
        from app.startup_validator import _check_metrics_platform
        c = _check_metrics_platform()
        assert c.status == ComponentStatus.OK

    def test_D11_metrics_ok_with_prometheus_creds(self, monkeypatch):
        _clean_env(monkeypatch, {
            "METRICS_PLATFORM_ENABLED": "true",
            "METRICS_PROMETHEUS_USERNAME": "scraper",
            "METRICS_PROMETHEUS_PASSWORD": "pw",
        })
        from app.startup_validator import _check_metrics_platform
        c = _check_metrics_platform()
        assert c.status == ComponentStatus.OK
        assert "basic" in c.detail

    def test_D12_prometheus_ok_when_set(self, monkeypatch):
        _clean_env(monkeypatch, {
            "METRICS_PLATFORM_ENABLED": "true",
            "METRICS_PROMETHEUS_USERNAME": "scraper",
            "METRICS_PROMETHEUS_PASSWORD": "pw",
        })
        from app.startup_validator import _check_prometheus
        c = _check_prometheus()
        assert c.status == ComponentStatus.OK

    def test_D13_prometheus_skipped_when_not_set(self, monkeypatch):
        _clean_env(monkeypatch, {"METRICS_PLATFORM_ENABLED": "true"})
        from app.startup_validator import _check_prometheus
        c = _check_prometheus()
        assert c.status == ComponentStatus.SKIPPED

    def test_D14_database_missing_fails(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_KEY", "")
        from app.startup_validator import _check_database
        c = _check_database()
        assert c.status == ComponentStatus.FAIL

    def test_D15_database_ok_with_https(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://prod.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "key")
        from app.startup_validator import _check_database
        c = _check_database()
        assert c.status == ComponentStatus.OK

    def test_D16_database_localhost_ok(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "http://localhost:8000")
        monkeypatch.setenv("SUPABASE_KEY", "key")
        from app.startup_validator import _check_database
        c = _check_database()
        assert c.status == ComponentStatus.OK

    def test_D17_audit_inmemory_warns(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
        from app.startup_validator import _check_audit_backend
        c = _check_audit_backend()
        assert c.status == ComponentStatus.WARN

    def test_D18_audit_supabase_ok(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "supabase")
        from app.startup_validator import _check_audit_backend
        c = _check_audit_backend()
        assert c.status == ComponentStatus.OK

    def test_D19_audit_invalid_fails(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "sqlite")
        from app.startup_validator import _check_audit_backend
        c = _check_audit_backend()
        assert c.status == ComponentStatus.FAIL

    def test_D20_required_env_vars_present(self, monkeypatch):
        monkeypatch.setenv("RAG_API_KEY", "x")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "x")
        from app.startup_validator import _check_hard_required
        assert _check_hard_required() == []

    def test_D21_required_env_vars_missing(self, monkeypatch):
        monkeypatch.delenv("RAG_API_KEY", raising=False)
        monkeypatch.delenv("RAG_API_KEYS", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "x")
        from app.startup_validator import _check_hard_required
        assert "RAG_API_KEY" in _check_hard_required()

    def test_D22_rag_api_keys_plural_accepted(self, monkeypatch):
        monkeypatch.delenv("RAG_API_KEY", raising=False)
        monkeypatch.setenv("RAG_API_KEYS", "k1,k2")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "x")
        from app.startup_validator import _check_hard_required
        assert "RAG_API_KEY" not in _check_hard_required()


# ══════════════════════════════════════════════════════════════════════════════
# Section E — validate_startup() end-to-end
# ══════════════════════════════════════════════════════════════════════════════

class TestE_ValidateStartupE2E:
    def test_E1_full_run_returns_report(self, monkeypatch, caplog_startup):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        report = validate_startup(tool_registry=reg, investigation_service=None)
        assert isinstance(report, StartupReport)
        assert report.ready

    def test_E2_registers_5_unity_and_2_metrics_tools(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        report = validate_startup(tool_registry=reg, investigation_service=None)
        assert len(report.registered_tools) == 7
        assert set(report.registered_tools) == {
            "GetSessionDetailsTool", "GetUserDetailsTool",
            "GetFailureReasonTool", "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
            "MetricTool", "ServerTool",
        }

    def test_E3_registered_tools_appear_in_registry(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        validate_startup(tool_registry=reg, investigation_service=None)
        for name in ("GetSessionDetailsTool", "MetricTool", "ServerTool"):
            assert reg.get(name) is not None

    def test_E4_startup_ready_summary_emitted(self, monkeypatch, caplog_startup):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        validate_startup(tool_registry=reg)
        messages = [r.getMessage() for r in caplog_startup.records]
        assert any("STARTUP_READY" in m for m in messages)

    def test_E5_registered_tools_listed_in_summary(self, monkeypatch, caplog_startup):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        validate_startup(tool_registry=reg)
        combined = "\n".join(r.getMessage() for r in caplog_startup.records)
        assert "REGISTERED_TOOLS" in combined
        assert "MetricTool" in combined
        assert "GetSessionDetailsTool" in combined

    def test_E6_strict_mode_raises_on_missing_required_var(self, monkeypatch):
        monkeypatch.delenv("RAG_API_KEY", raising=False)
        monkeypatch.delenv("RAG_API_KEYS", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        with pytest.raises(StartupValidationError) as exc:
            validate_startup(strict=True)
        assert "SUPABASE_URL" in exc.value.items or "RAG_API_KEY" in exc.value.items

    def test_E7_non_strict_records_failure_in_report(self, monkeypatch, caplog_startup):
        monkeypatch.delenv("RAG_API_KEY", raising=False)
        monkeypatch.delenv("RAG_API_KEYS", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        report = validate_startup(strict=False)
        assert report.ready is False
        assert report.has_failures is True

    def test_E8_register_tools_flag_false_skips_registration(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        report = validate_startup(tool_registry=reg, register_production_tools=False)
        assert report.registered_tools == ()
        assert reg.get("MetricTool") is None

    def test_E9_investigation_service_ok_when_supplied(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        from unittest.mock import MagicMock
        svc = MagicMock()
        reg = ToolRegistry()
        report = validate_startup(tool_registry=reg, investigation_service=svc)
        inv = next(c for c in report.checks if c.name == "Investigation Runtime")
        assert inv.status == ComponentStatus.OK

    def test_E10_investigation_service_not_wired_when_absent(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        report = validate_startup(tool_registry=reg, investigation_service=None)
        inv = next(c for c in report.checks if c.name == "Investigation Runtime")
        assert inv.status == ComponentStatus.NOT_WIRED

    def test_E11_tool_registry_not_wired_when_missing(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        report = validate_startup(tool_registry=None)
        assert any(
            c.name == "Tool Registry" and c.status == ComponentStatus.NOT_WIRED
            for c in report.checks
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section F — Runtime traces
# ══════════════════════════════════════════════════════════════════════════════

class TestF_RuntimeTraces:
    def test_F1_thirteen_canonical_tags(self):
        assert len(ALL_RUNTIME_TRACES) == 13

    def test_F2_all_expected_tags_present(self):
        expected = {
            TRACE_ENTER_WEBHOOK, TRACE_EXIT_WEBHOOK,
            TRACE_ENTER_TOOL_EXECUTOR, TRACE_EXIT_TOOL_EXECUTOR,
            TRACE_ENTER_UNITY, TRACE_EXIT_UNITY,
            TRACE_ENTER_METRICS, TRACE_EXIT_METRICS,
            TRACE_ENTER_INVESTIGATION, TRACE_EXIT_INVESTIGATION,
            TRACE_ENTER_EVIDENCE_BUNDLE, TRACE_EXIT_EVIDENCE_BUNDLE,
            TRACE_RUNTIME_STOP_LLM_BOUNDARY,
        }
        assert ALL_RUNTIME_TRACES == frozenset(expected)

    def test_F3_emit_at_warning_level(self, caplog_runtime):
        emit_runtime_trace(TRACE_ENTER_WEBHOOK, component="webhook")
        assert any(r.levelno == logging.WARNING for r in caplog_runtime.records)

    def test_F4_stable_five_field_layout(self, caplog_runtime):
        emit_runtime_trace(
            TRACE_ENTER_UNITY,
            component="unity", stage="get_session", case_id="c-1",
            trace_id="t-1", status="STARTED",
        )
        msg = caplog_runtime.records[-1].getMessage()
        for f in ("component=unity", "stage=get_session", "case_id=c-1",
                  "trace_id=t-1", "status=STARTED"):
            assert f in msg

    def test_F5_email_redacted(self, caplog_runtime):
        emit_runtime_trace(TRACE_ENTER_WEBHOOK, component="user@example.com")
        assert "REDACTED" in caplog_runtime.records[-1].getMessage()

    def test_F6_bearer_redacted(self, caplog_runtime):
        emit_runtime_trace(TRACE_ENTER_WEBHOOK, status="Bearer xyz")
        assert "REDACTED" in caplog_runtime.records[-1].getMessage()

    def test_F7_long_value_redacted(self, caplog_runtime):
        emit_runtime_trace(TRACE_ENTER_WEBHOOK, stage="x" * 200)
        assert "REDACTED" in caplog_runtime.records[-1].getMessage()

    def test_F8_unknown_tag_no_normal_line(self, caplog_runtime):
        emit_runtime_trace("NOT_A_TAG", component="x")
        assert any("unknown_tag" in r.getMessage() for r in caplog_runtime.records)

    def test_F9_missing_values_render_dash(self, caplog_runtime):
        emit_runtime_trace(TRACE_EXIT_METRICS)
        msg = caplog_runtime.records[-1].getMessage()
        assert "component=-" in msg

    def test_F10_exception_trace_format(self, caplog_runtime):
        emit_exception_trace("unity", case_id="c-1", reason="timeout")
        msg = caplog_runtime.records[-1].getMessage()
        assert msg.startswith("EXCEPTION_UNITY")
        assert "case_id=c-1" in msg
        assert "status=timeout" in msg

    def test_F11_return_trace_format(self, caplog_runtime):
        emit_return_trace("duplicate", component="webhook", case_id="c-2")
        msg = caplog_runtime.records[-1].getMessage()
        assert msg.startswith("RETURN_DUPLICATE")
        assert "case_id=c-2" in msg

    def test_F12_never_raises_on_none_input(self):
        emit_runtime_trace(TRACE_ENTER_WEBHOOK, component=None)  # type: ignore[arg-type]

    def test_F13_llm_boundary_marker_present(self):
        assert TRACE_RUNTIME_STOP_LLM_BOUNDARY == "RUNTIME_STOP_LLM_BOUNDARY"
        assert TRACE_RUNTIME_STOP_LLM_BOUNDARY in ALL_RUNTIME_TRACES


# ══════════════════════════════════════════════════════════════════════════════
# Section G — Production tool inventory (5 Unity + 2 Metrics = 7)
# ══════════════════════════════════════════════════════════════════════════════

class TestG_ProductionToolInventory:
    def test_G1_five_unity_two_metrics_registered(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        reg = ToolRegistry()
        report = validate_startup(tool_registry=reg, investigation_service=None)
        unity_tools = {"GetSessionDetailsTool", "GetUserDetailsTool",
                       "GetFailureReasonTool", "GetCaseHistoryTool",
                       "GetOnboardingStatusTool"}
        metric_tools = {"MetricTool", "ServerTool"}
        assert unity_tools.issubset(set(report.registered_tools))
        assert metric_tools.issubset(set(report.registered_tools))

    def test_G2_all_tools_have_provider_and_capability(self, monkeypatch):
        _clean_env(monkeypatch, {
            "UNITY_ENABLED": "false",
            "METRICS_PLATFORM_ENABLED": "false",
        })
        from case_engine.tools.tool_models import ToolCapability, ToolProvider
        reg = ToolRegistry()
        validate_startup(tool_registry=reg, investigation_service=None)
        for name in ("GetSessionDetailsTool", "GetUserDetailsTool",
                     "GetFailureReasonTool", "GetCaseHistoryTool",
                     "GetOnboardingStatusTool"):
            tool = reg.get(name)
            assert tool.definition.provider == ToolProvider.UNITY
            assert tool.definition.capability == ToolCapability.READ
        for name in ("MetricTool", "ServerTool"):
            tool = reg.get(name)
            assert tool.definition.provider == ToolProvider.METRICS_PLATFORM
            assert tool.definition.capability == ToolCapability.READ


# ══════════════════════════════════════════════════════════════════════════════
# Section H — Public export surface
# ══════════════════════════════════════════════════════════════════════════════

class TestH_Exports:
    def test_H1_startup_validator_exports(self):
        import app.startup_validator as sv
        for name in (
            "validate_startup", "StartupReport", "StartupValidationError",
            "ComponentStatus", "ComponentCheck",
        ):
            assert hasattr(sv, name)

    def test_H2_runtime_traces_exports(self):
        import app.runtime_traces as rt
        for name in (
            "emit_runtime_trace", "emit_exception_trace", "emit_return_trace",
            "ALL_RUNTIME_TRACES",
            "TRACE_ENTER_WEBHOOK", "TRACE_EXIT_WEBHOOK",
            "TRACE_ENTER_TOOL_EXECUTOR", "TRACE_EXIT_TOOL_EXECUTOR",
            "TRACE_ENTER_UNITY", "TRACE_EXIT_UNITY",
            "TRACE_ENTER_METRICS", "TRACE_EXIT_METRICS",
            "TRACE_ENTER_INVESTIGATION", "TRACE_EXIT_INVESTIGATION",
            "TRACE_ENTER_EVIDENCE_BUNDLE", "TRACE_EXIT_EVIDENCE_BUNDLE",
            "TRACE_RUNTIME_STOP_LLM_BOUNDARY",
        ):
            assert hasattr(rt, name)

    def test_H3_metrics_config_has_new_prom_fields(self):
        import metrics_platform as mp
        cfg = mp.MetricsPlatformConfig(
            base_url="https://x", api_key="", timeout_s=1, max_retries=0,
            default_slug="k", auth_mode="none", user_agent="t",
        )
        assert hasattr(cfg, "prometheus_username")
        assert hasattr(cfg, "prometheus_password")
        assert hasattr(cfg, "has_prometheus_credentials")
        assert hasattr(cfg, "effective_auth_mode")
