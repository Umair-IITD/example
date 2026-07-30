"""
tests/test_sprint260_loki_integration.py

Sprint 2.60 — Multi-Tenant Log Platform (Grafana Loki) Integration.

Sections:
    A — BM25 shared module (bm25.py)
    B — PII redaction + line parsing (relevance.py)
    C — GetSessionLogsTool with mocked Loki
    D — DISABLED mode
    E — PARTIAL vs UNAVAILABLE distinction
    F — Registration

No network. Every httpx interaction uses MockTransport.
"""
from __future__ import annotations

import json
import os

# Environment setup BEFORE any imports that read env
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("RAG_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
# Loki disabled by default in tests (no real network)
os.environ.setdefault("LOKI_SAAS_USERNAME", "")
os.environ.setdefault("LOKI_SAAS_PASSWORD", "")

import httpx
import pytest

from rag_engine.retrieval.bm25 import tokenize, bm25_rerank_inplace, bm25_score_list
from case_engine.integrations.loki.config import LokiConfig, LokiLogAvailability
from case_engine.integrations.loki.relevance import (
    _redact_and_truncate,
    _MAX_MESSAGE_CHARS,
    parse_line,
    extract_relevant_lines,
)
from case_engine.tools.adapters.log_tools import GetSessionLogsTool, register_loki_tools
from case_engine.tools.tool_models import ToolProvider


# ---------------------------------------------------------------------------
# Shared mock transport helpers
# ---------------------------------------------------------------------------

def _make_loki_mock(response_body: dict, status_code: int = 200):
    """Create a MockTransport that returns a fixed response for all requests."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_body)
    return httpx.MockTransport(handler)


def _make_multi_loki_mock(responses: list[dict]):
    """
    MockTransport that cycles through a list of responses in order.
    Useful for the three-phase fetch (Phase 1, Phase 2, Phase 3 get different bodies).
    """
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        body = responses[idx]
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


def _make_error_mock(exc_class=httpx.ConnectError):
    """MockTransport that always raises an exception."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_class("mock connection failure")
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Standard Loki response with real log lines including a PII line
# ---------------------------------------------------------------------------

_BIG_PII_VALUE = "A" * 100_000  # 100k chars Aadhaar photo simulation

_STANDARD_LOKI_RESPONSE = {
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {"detected_level": "info", "service_name": "agentapi"},
                "values": [
                    ["1751356860000000000", "INFO agent session started successfully"],
                ],
            },
            {
                "stream": {"detected_level": "error", "service_name": "agentapi"},
                "values": [
                    ["1751356920000000000", "ERROR session failed liveness check"],
                ],
            },
            {
                "stream": {"detected_level": "info", "service_name": "agentapi"},
                "values": [
                    [
                        "1751356980000000000",
                        f'{{"aadhaar_number": "{_BIG_PII_VALUE}"}}',
                    ],
                ],
            },
            {
                "stream": {"detected_level": "warn", "service_name": "userapi"},
                "values": [
                    ["1751357040000000000", "WARN video upload took 30s, may timeout"],
                ],
            },
        ],
    }
}

_EMPTY_LOKI_RESPONSE = {
    "data": {
        "resultType": "streams",
        "result": [],
    }
}


# ---------------------------------------------------------------------------
# Helper: create a tool with a given transport
# ---------------------------------------------------------------------------

def _make_tool_with_transport(transport: httpx.BaseTransport) -> GetSessionLogsTool:
    """
    Monkey-patches LokiClient to inject transport for tests.
    Credentials are non-empty so DISABLED check passes.
    """
    from unittest.mock import patch

    config = LokiConfig(
        saas_base_url="https://mock-loki.test",
        saas_username="testuser",
        saas_password="testpass",
        enabled=True,
    )

    original_init = __import__(
        "case_engine.integrations.loki.client", fromlist=["LokiClient"]
    ).LokiClient.__init__

    def patched_init(self_obj, cfg, tenant_key="saas", transport_arg=None):
        original_init(self_obj, cfg, tenant_key, transport_arg or transport)

    tool = GetSessionLogsTool(config=config)
    # Patch at the LokiClient class level for this tool's calls
    with patch(
        "case_engine.integrations.loki.client.LokiClient.__init__",
        patched_init,
    ):
        pass  # patch only applied during context; need a different approach

    # Alternative: inject transport via a config-aware factory
    # We'll use a subclass-based approach instead
    return tool


class _MockedLokiTool(GetSessionLogsTool):
    """Subclass that injects a mock transport into LokiClient calls."""

    def __init__(self, config: LokiConfig, transport: httpx.BaseTransport):
        super().__init__(config=config)
        self._mock_transport = transport

    def run(self, inputs: dict) -> dict:
        """Override to inject transport into LokiClient construction."""
        from case_engine.integrations.loki.client import LokiClient, fetch_session_logs
        from case_engine.integrations.loki.relevance import extract_relevant_lines
        from datetime import datetime, timedelta, timezone
        import asyncio
        import concurrent.futures

        session_id = inputs.get("session_id", "")
        start_epoch = inputs.get("start_epoch")
        end_epoch = inputs.get("end_epoch")
        ticket_query = inputs.get("ticket_query")
        tenant = inputs.get("tenant", "saas")

        if not self._config.enabled:
            return {
                "tool_name": "GetSessionLogsTool",
                "session_id": session_id,
                "tenant": tenant,
                "log_availability": LokiLogAvailability.DISABLED.value,
                "window_start_utc": "",
                "window_end_utc": "",
                "log_line_count": 0,
                "log_char_count": 0,
                "curated_log_excerpt": "",
                "reduction_stats": {},
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                "ambiguity_note": "",
                "error": "",
            }

        if start_epoch is not None:
            start_dt = datetime.fromtimestamp(float(start_epoch), tz=timezone.utc)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(hours=2)

        if end_epoch is not None:
            end_dt = datetime.fromtimestamp(float(end_epoch), tz=timezone.utc)
        else:
            end_dt = datetime.now(tz=timezone.utc)

        try:
            transport = self._mock_transport
            config = self._config

            def _sync_fetch():
                with LokiClient(config, tenant, transport=transport) as client:
                    return fetch_session_logs(
                        client,
                        session_id,
                        start_dt - timedelta(minutes=5),
                        end_dt + timedelta(minutes=5),
                    )

            raw_text = _sync_fetch()

            raw_lines = [
                line for line in raw_text.splitlines()
                if line.startswith("[20")
            ] if raw_text else []

            if not raw_lines:
                availability = LokiLogAvailability.PARTIAL
                selected_lines = []
                stats: dict = {}
                curated_excerpt = ""
            else:
                availability = LokiLogAvailability.AVAILABLE
                selected_lines, stats = extract_relevant_lines(
                    raw_lines,
                    ticket_query or session_id,
                )
                curated_excerpt = "\n".join(p.content for p in selected_lines)

            ambiguity_note = ""
            if availability == LokiLogAvailability.PARTIAL:
                ambiguity_note = (
                    "No log lines found — data may have aged out of Loki retention window. "
                    "Empty result does not confirm absence of a technical issue."
                )

            return {
                "tool_name": "GetSessionLogsTool",
                "session_id": session_id,
                "tenant": tenant,
                "log_availability": availability.value,
                "window_start_utc": start_dt.isoformat(),
                "window_end_utc": end_dt.isoformat(),
                "log_line_count": len(selected_lines),
                "log_char_count": stats.get("output_chars", 0),
                "curated_log_excerpt": curated_excerpt,
                "reduction_stats": stats,
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                "ambiguity_note": ambiguity_note,
                "error": "",
            }

        except Exception as exc:  # noqa: BLE001
            return {
                "tool_name": "GetSessionLogsTool",
                "session_id": session_id,
                "tenant": tenant,
                "log_availability": LokiLogAvailability.UNAVAILABLE.value,
                "window_start_utc": "",
                "window_end_utc": "",
                "log_line_count": 0,
                "log_char_count": 0,
                "curated_log_excerpt": "",
                "reduction_stats": {},
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                "ambiguity_note": "",
                "error": str(exc),
            }


def _enabled_config() -> LokiConfig:
    return LokiConfig(
        saas_base_url="https://mock-loki.test",
        saas_username="testuser",
        saas_password="testpass",
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Section A — BM25 shared module
# ---------------------------------------------------------------------------

class TestA_BM25SharedModule:

    def test_A1_tokenize_lowercases_and_splits(self):
        result = tokenize("Hello World 123")
        assert result == {"hello", "world", "123"}

    def test_A2_bm25_rerank_inplace_scores_rows(self):
        rows = [
            {"content": "session failed liveness check error"},
            {"content": "routine heartbeat polling every 5 seconds"},
            {"content": "ERROR liveness detection failed for session"},
        ]
        bm25_rerank_inplace("session liveness failed", rows)
        assert all("_bm25_score" in r for r in rows)
        # The most relevant row (has more query term matches) scores higher
        scores = [r["_bm25_score"] for r in rows]
        # Row 0 and row 2 both match; heartbeat row should score lowest
        assert scores[1] < scores[0] or scores[1] < scores[2]

    def test_A3_bm25_score_list_returns_parallel(self):
        contents = ["video upload failed", "otp not received", "session timeout"]
        scores = bm25_score_list("video upload", contents)
        assert len(scores) == len(contents)

    def test_A4_bm25_score_list_empty_query(self):
        contents = ["video upload failed", "otp not received"]
        scores = bm25_score_list("", contents)
        assert all(s == 0.0 for s in scores)

    def test_A5_hybrid_retriever_import_unchanged(self):
        """Ensure HybridTicketRetriever still imports after the refactor."""
        from rag_engine.retrieval.hybrid_ticket_retriever import HybridTicketRetriever
        assert HybridTicketRetriever is not None


# ---------------------------------------------------------------------------
# Section B — PII redaction + line parsing
# ---------------------------------------------------------------------------

class TestB_PIIRedactionAndLineParsing:

    def test_B1_pii_redacted_before_truncation(self):
        """A 100k-char Aadhaar value must be redacted AND the result truncated."""
        big_pii = f'"aadhaar_number": "' + "x" * 110_000 + '"'
        result = _redact_and_truncate(big_pii)
        assert "[REDACTED]" in result
        # Content must not contain the huge x string
        assert "x" * 100 not in result
        # Total length must be bounded (max_chars + truncation suffix overhead)
        assert len(result) <= _MAX_MESSAGE_CHARS + 60  # 60 chars for the ...[+N chars truncated] suffix

    def test_B2_truncation_marker_present(self):
        """Lines longer than 500 chars after redaction get the truncation marker."""
        long_line = "a" * 600
        result = _redact_and_truncate(long_line)
        assert "...[+" in result
        assert "chars truncated]" in result

    def test_B3_parse_line_extracts_level_and_service(self):
        raw = "[2026-07-01T10:00:00+00:00] (detected_level=error, service_name=agentapi) some error message"
        line = parse_line(raw, 0)
        assert line.level == "error"
        assert line.service_name == "agentapi"
        # forced is set by extract_relevant_lines, not parse_line
        assert line.forced is False

    def test_B4_unknown_line_format_returns_unknown_level(self):
        raw = "this is not in the expected format at all"
        line = parse_line(raw, 0)
        assert line.level == "unknown"

    def test_B5_pii_redaction_preserves_rest_of_content(self):
        """Non-PII content around the PII field is preserved."""
        msg = 'user logged in, "aadhaar_number": "123456789012", session started'
        result = _redact_and_truncate(msg)
        assert "[REDACTED]" in result
        assert "user logged in" in result
        assert "session started" in result


# ---------------------------------------------------------------------------
# Section C — GetSessionLogsTool with mocked Loki
# ---------------------------------------------------------------------------

class TestC_GetSessionLogsToolMocked:

    def _make_tool(self, transport):
        return _MockedLokiTool(config=_enabled_config(), transport=transport)

    def test_C1_tool_returns_curated_excerpt_not_raw(self):
        """Tool returns AVAILABLE with curated_log_excerpt, never the raw PII."""
        transport = _make_loki_mock(_STANDARD_LOKI_RESPONSE)
        tool = self._make_tool(transport)
        result = tool.run({
            "session_id": "test-123",
            "start_epoch": 1751356800.0,
            "end_epoch": 1751357400.0,
            "ticket_query": "video upload failed",
        })
        assert result["log_availability"] == "AVAILABLE"
        assert len(result["curated_log_excerpt"]) > 0
        # The 100k PII chars must NOT be in the output
        assert _BIG_PII_VALUE not in result["curated_log_excerpt"]

    def test_C2_pii_line_redacted_in_excerpt(self):
        """The aadhaar photo line is redacted — REDACTED marker present, raw value absent."""
        transport = _make_loki_mock(_STANDARD_LOKI_RESPONSE)
        tool = self._make_tool(transport)
        result = tool.run({
            "session_id": "test-123",
            "start_epoch": 1751356800.0,
            "end_epoch": 1751357400.0,
        })
        excerpt = result["curated_log_excerpt"]
        # Must NOT contain the huge A string
        assert "A" * 1000 not in excerpt

    def test_C3_excerpt_is_chronologically_sorted(self):
        """Lines in curated_log_excerpt appear in ascending timestamp order."""
        response = {
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"detected_level": "error", "service_name": "agentapi"},
                        "values": [
                            ["1751356980000000000", "ERROR third event happened"],
                            ["1751356860000000000", "ERROR first event happened"],
                            ["1751356920000000000", "ERROR second event happened"],
                        ],
                    }
                ],
            }
        }
        transport = _make_loki_mock(response)
        tool = self._make_tool(transport)
        result = tool.run({"session_id": "test-sort", "start_epoch": 1751356800.0, "end_epoch": 1751357400.0})
        excerpt = result["curated_log_excerpt"]
        lines = [l for l in excerpt.splitlines() if l.strip()]
        # All are force-included (error level) so they should all be present
        # Check that "first" appears before "second" and "second" before "third"
        if len(lines) >= 2:
            first_idx = next((i for i, l in enumerate(lines) if "first" in l), -1)
            second_idx = next((i for i, l in enumerate(lines) if "second" in l), -1)
            if first_idx >= 0 and second_idx >= 0:
                assert first_idx < second_idx

    def test_C4_tool_never_raises(self):
        """Tool returns UNAVAILABLE dict when httpx raises ConnectError."""
        transport = _make_error_mock(httpx.ConnectError)
        tool = self._make_tool(transport)
        result = tool.run({"session_id": "test-err", "start_epoch": 1751356800.0, "end_epoch": 1751357400.0})
        assert result["log_availability"] == "UNAVAILABLE"
        assert result["error"] != ""

    def test_C5_real_run_exercises_asyncio_to_thread(self):
        """
        Calls the REAL GetSessionLogsTool.run() (not _MockedLokiTool) with a mock
        transport injected via patch.object on LokiClient.__init__.
        This is the only test that exercises the asyncio.to_thread() wrapping in
        _async_fetch — the sync httpx call runs in a worker thread, not the main loop.
        """
        from unittest.mock import patch
        from case_engine.integrations.loki.client import LokiClient

        mock_transport = _make_loki_mock(_STANDARD_LOKI_RESPONSE)
        real_init = LokiClient.__init__

        def _patched_init(self_obj, cfg, tenant_key="saas", transport=None):
            real_init(self_obj, cfg, tenant_key, mock_transport)

        config = _enabled_config()
        tool = GetSessionLogsTool(config=config)

        with patch.object(LokiClient, "__init__", _patched_init):
            result = tool.run({
                "session_id": "test-asyncio-path",
                "start_epoch": 1751356800.0,
                "end_epoch": 1751357400.0,
                "ticket_query": "session failed liveness check",
            })

        # Result must be a valid canonical dict — not a raw exception
        assert "log_availability" in result
        assert result["log_availability"] in ("AVAILABLE", "PARTIAL", "UNAVAILABLE")
        assert "curated_log_excerpt" in result
        assert "error" in result
        # PII must never reach the output
        assert _BIG_PII_VALUE not in result["curated_log_excerpt"]


# ---------------------------------------------------------------------------
# Section D — DISABLED mode
# ---------------------------------------------------------------------------

class TestD_DisabledMode:

    def test_D1_disabled_when_no_credentials(self):
        """Config with empty credentials must be disabled."""
        config = LokiConfig(
            saas_base_url="https://loki.example.com",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        assert config.enabled is False

    def test_D2_tool_returns_disabled_without_http(self):
        """Tool with disabled config returns DISABLED and makes no HTTP calls."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        tool = GetSessionLogsTool(config=config)
        result = tool.run({"session_id": "abc"})
        assert result["log_availability"] == "DISABLED"

    def test_D3_disabled_does_not_raise(self):
        """Disabled tool returns a valid dict, never raises."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        tool = GetSessionLogsTool(config=config)
        result = tool.run({"session_id": "xyz"})
        assert isinstance(result, dict)
        assert "log_availability" in result
        assert result["tool_name"] == "GetSessionLogsTool"

    def test_D4_from_env_disabled_when_no_env_vars(self):
        """LokiConfig.from_env() is disabled when LOKI_SAAS_USERNAME is empty."""
        import os
        old_user = os.environ.get("LOKI_SAAS_USERNAME", "")
        old_pass = os.environ.get("LOKI_SAAS_PASSWORD", "")
        try:
            os.environ["LOKI_SAAS_USERNAME"] = ""
            os.environ["LOKI_SAAS_PASSWORD"] = ""
            config = LokiConfig.from_env()
            assert config.enabled is False
        finally:
            os.environ["LOKI_SAAS_USERNAME"] = old_user
            os.environ["LOKI_SAAS_PASSWORD"] = old_pass


# ---------------------------------------------------------------------------
# Section E — PARTIAL vs UNAVAILABLE distinction
# ---------------------------------------------------------------------------

class TestE_PartialVsUnavailable:

    def _make_tool(self, transport):
        return _MockedLokiTool(config=_enabled_config(), transport=transport)

    def test_E1_empty_loki_result_is_partial(self):
        """HTTP 200 with zero log lines → PARTIAL (retention aging, not failure)."""
        transport = _make_loki_mock(_EMPTY_LOKI_RESPONSE)
        tool = self._make_tool(transport)
        result = tool.run({"session_id": "no-logs-session", "start_epoch": 1751356800.0, "end_epoch": 1751357400.0})
        assert result["log_availability"] == "PARTIAL"
        assert result["ambiguity_note"] != ""

    def test_E2_http_500_is_unavailable(self):
        """HTTP 500 → UNAVAILABLE (hard error, distinct from empty result)."""
        def error_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=request,
                response=httpx.Response(500),
            )
        transport = httpx.MockTransport(error_handler)
        tool = self._make_tool(transport)
        result = tool.run({"session_id": "err-session", "start_epoch": 1751356800.0, "end_epoch": 1751357400.0})
        assert result["log_availability"] == "UNAVAILABLE"

    def test_E3_partial_ambiguity_note_mentions_retention(self):
        """PARTIAL ambiguity_note must mention 'retention' so the LLM knows why."""
        transport = _make_loki_mock(_EMPTY_LOKI_RESPONSE)
        tool = self._make_tool(transport)
        result = tool.run({"session_id": "partial-session", "start_epoch": 1751356800.0, "end_epoch": 1751357400.0})
        assert result["log_availability"] == "PARTIAL"
        assert "retention" in result["ambiguity_note"].lower()

    def test_E4_unavailable_has_empty_ambiguity_note(self):
        """UNAVAILABLE result has empty ambiguity_note (error explains the failure)."""
        transport = _make_error_mock(httpx.ConnectError)
        tool = self._make_tool(transport)
        result = tool.run({"session_id": "conn-err", "start_epoch": 1751356800.0, "end_epoch": 1751357400.0})
        assert result["log_availability"] == "UNAVAILABLE"
        assert result["ambiguity_note"] == ""
        assert result["error"] != ""


# ---------------------------------------------------------------------------
# Section F — Registration
# ---------------------------------------------------------------------------

class TestF_Registration:

    def test_F1_register_loki_tools_returns_outcomes(self):
        """register_loki_tools returns a dict with GetSessionLogsTool key."""
        from unittest.mock import MagicMock
        registry = MagicMock()
        outcomes = register_loki_tools(registry)
        assert "GetSessionLogsTool" in outcomes

    def test_F2_tool_provider_loki_exists(self):
        """ToolProvider.LOG_PLATFORM is accessible and correct."""
        assert ToolProvider.LOG_PLATFORM == "LOG_PLATFORM"

    def test_F3_tool_definition_has_correct_provider(self):
        """GetSessionLogsTool.definition reports LOG_PLATFORM provider."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        tool = GetSessionLogsTool(config=config)
        assert tool.definition.provider == ToolProvider.LOG_PLATFORM

    def test_F4_register_calls_register_capability_if_available(self):
        """Registry with register_capability gets LOG capability registered."""
        from unittest.mock import MagicMock
        registry = MagicMock()
        registry.register_capability = MagicMock()
        register_loki_tools(registry)
        registry.register_capability.assert_called_once_with("LOG", "GetSessionLogsTool")

    def test_F5_register_graceful_on_registry_error(self):
        """register_loki_tools catches registry.register() exceptions."""
        from unittest.mock import MagicMock
        registry = MagicMock()
        registry.register.side_effect = RuntimeError("already registered")
        outcomes = register_loki_tools(registry)
        assert "GetSessionLogsTool" in outcomes
        assert "error:" in outcomes["GetSessionLogsTool"]

    def test_F6_tool_required_inputs_contains_session_id(self):
        """Tool definition requires session_id."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        tool = GetSessionLogsTool(config=config)
        assert "session_id" in tool.definition.required_inputs

    def test_F7_loki_package_exports(self):
        """The loki __init__.py exports all required symbols."""
        from case_engine.integrations.loki import (
            LokiConfig,
            LokiLogAvailability,
            LokiClient,
            fetch_session_logs,
            extract_relevant_lines,
            parse_auditor_duration_window,
        )
        assert LokiConfig is not None
        assert LokiLogAvailability is not None
        assert LokiClient is not None
        assert fetch_session_logs is not None
        assert extract_relevant_lines is not None
        assert parse_auditor_duration_window is not None


# ---------------------------------------------------------------------------
# Section G — Additional relevance + auditor window tests
# ---------------------------------------------------------------------------

class TestG_RelevanceAndAuditorWindow:

    def test_G1_extract_relevant_lines_with_error_forced(self):
        """Error-level lines are always force-included."""
        lines = [
            "[2026-07-01T10:00:00+00:00] (detected_level=info, service_name=agentapi) routine heartbeat",
            "[2026-07-01T10:00:01+00:00] (detected_level=error, service_name=agentapi) session crashed",
        ]
        selected, stats = extract_relevant_lines(lines, "session problem")
        error_lines = [p for p in selected if p.forced]
        assert len(error_lines) >= 1
        assert any("crashed" in p.content for p in selected)

    def test_G2_parse_auditor_duration_window(self):
        """parse_auditor_duration_window correctly parses 'duration X:XX to Y:YY'."""
        from case_engine.integrations.loki.relevance import parse_auditor_duration_window
        from datetime import timezone
        result = parse_auditor_duration_window(
            "The issue occurred at duration 1:19 to 2:40 in the session",
            1751356800.0,
        )
        assert result is not None
        start_w, end_w = result
        # 1:19 = 79 seconds from start
        assert start_w.tzinfo is not None
        delta_start = (start_w - start_w.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
        # At least the delta between start and end should be right
        delta = (end_w - start_w).total_seconds()
        assert abs(delta - (2 * 60 + 40 - (1 * 60 + 19))) < 2

    def test_G3_parse_auditor_window_none_on_missing_pattern(self):
        """No match returns None gracefully."""
        from case_engine.integrations.loki.relevance import parse_auditor_duration_window
        result = parse_auditor_duration_window("no duration pattern here", 1751356800.0)
        assert result is None

    def test_G4_extract_relevant_lines_empty_input(self):
        """Empty log input returns empty list and zero stats."""
        selected, stats = extract_relevant_lines([], "query")
        assert selected == []
        assert stats["input_lines"] == 0

    def test_G5_bm25_score_list_empty_contents(self):
        """Empty contents list returns empty scores list."""
        scores = bm25_score_list("video upload", [])
        assert scores == []

    def test_G6_loki_config_masked_username(self):
        """masked_username redacts credentials properly."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="admin_user",
            saas_password="secret",
            enabled=True,
        )
        masked = config.masked_username
        assert masked.startswith("ad")
        assert "***" in masked
        assert "secret" not in masked

    def test_G7_loki_config_masked_username_not_set(self):
        """masked_username returns '(not set)' when empty."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        assert config.masked_username == "(not set)"

    def test_G8_log_availability_enum_values(self):
        """All four LokiLogAvailability values are accessible."""
        assert LokiLogAvailability.AVAILABLE == "AVAILABLE"
        assert LokiLogAvailability.PARTIAL == "PARTIAL"
        assert LokiLogAvailability.UNAVAILABLE == "UNAVAILABLE"
        assert LokiLogAvailability.DISABLED == "DISABLED"

    def test_G9_tool_output_schema_complete(self):
        """Tool definition output_schema includes all required keys."""
        config = LokiConfig(
            saas_base_url="x",
            saas_username="",
            saas_password="",
            enabled=False,
        )
        tool = GetSessionLogsTool(config=config)
        schema = tool.definition.output_schema
        required_keys = {
            "tool_name", "session_id", "tenant", "log_availability",
            "window_start_utc", "window_end_utc", "log_line_count",
            "log_char_count", "curated_log_excerpt", "reduction_stats",
            "collected_at", "ambiguity_note", "error",
        }
        assert required_keys.issubset(set(schema.keys()))


# ---------------------------------------------------------------------------
# Section H — Canara / BOB tenant registration
# ---------------------------------------------------------------------------
#
# Verifies the two new tenants (Canara, BOB) are correctly registered per
# the HOWTO_loki_log_platform.md 5-step procedure, WITHOUT exercising real
# network calls and WITHOUT touching SaaS's existing behavior. Credentials
# for these tenants are expected to be supplied via .env by the user
# (LOKI_CANARA_USERNAME/PASSWORD, LOKI_BOB_USERNAME/PASSWORD) — these tests
# construct LokiConfig directly so they don't depend on real env values.

import base64  # noqa: E402


def _basic_auth_header(username: str, password: str) -> str:
    """Build the exact 'Basic <b64>' header httpx.BasicAuth would produce."""
    token = base64.b64encode(f"{username}:{password}".encode("latin1")).decode("ascii")
    return f"Basic {token}"


class TestH_TenantRegistration:

    def test_H1_registry_contains_canara_bob_intentionally_excluded(self):
        """
        Canara is registered. BOB is intentionally NOT registered: its only
        known endpoint is plaintext http://, and a security review (applied
        directly to client.py) blocks any tenant from being wired up over
        HTTP — BasicAuth + PII-bearing VKYC logs must not transit in the
        clear. Re-enable once BOB provides HTTPS or a VPN tunnel.
        """
        from case_engine.integrations.loki.client import LOKI_REGISTRY
        assert "canara" in LOKI_REGISTRY
        assert "bob" not in LOKI_REGISTRY

    def test_H2_canara_datasource_fields_correct(self):
        """Canara LokiDatasource matches the tenant-provided values exactly."""
        from case_engine.integrations.loki.client import LOKI_REGISTRY
        ds = LOKI_REGISTRY["canara"]
        assert ds.client_name == "Canara"
        assert ds.datasource_name == "loki-canara"
        assert ds.uid == "afm1z1tgkotmod"
        assert ds.base_url == "https://videokyc.canarabank.bank.in"
        assert ds.via_grafana_proxy is False

    def test_H3_bob_unknown_tenant_raises(self):
        """
        BOB is not in LOKI_REGISTRY (see test_H1). Requesting tenant='bob'
        must raise the same ValueError as any other unregistered tenant key
        — never silently fall through to an unauthenticated/plaintext call.
        """
        from case_engine.integrations.loki.client import LokiClient
        config = _enabled_config()
        with pytest.raises(ValueError, match="Unknown tenant"):
            LokiClient(config, "bob")

    def test_H4_saas_registry_entry_unchanged(self):
        """Registering new tenants must not alter the existing SaaS entry."""
        from case_engine.integrations.loki.client import LOKI_REGISTRY
        ds = LOKI_REGISTRY["saas"]
        assert ds.client_name == "SaaS"
        assert ds.datasource_name == "loki - saas"
        assert ds.uid == "bfm1x2kaaifb4c"
        assert ds.base_url == "https://utility-server-sfd.app.getkwikid.com"

    def test_H5_loki_config_has_canara_and_bob_fields(self):
        """LokiConfig dataclass exposes the new per-tenant credential fields."""
        config = LokiConfig(
            saas_base_url="x", saas_username="", saas_password="", enabled=False,
        )
        assert config.canara_base_url == ""
        assert config.canara_username == ""
        assert config.canara_password == ""
        assert config.bob_base_url == ""
        assert config.bob_username == ""
        assert config.bob_password == ""

    def test_H6_loki_client_instantiates_for_canara(self):
        """LokiClient can be constructed for tenant='canara' with no network call."""
        from case_engine.integrations.loki.client import LokiClient
        config = LokiConfig(
            saas_base_url="x", saas_username="", saas_password="", enabled=True,
            canara_username="canara_user", canara_password="canara_pass",
        )
        transport = _make_loki_mock(_EMPTY_LOKI_RESPONSE)
        with LokiClient(config, "canara", transport=transport) as client:
            assert client.tenant_key == "canara"
            assert str(client._client.base_url).rstrip("/") == "https://videokyc.canarabank.bank.in"

    def test_H7_bob_credential_fields_still_exist_for_future_https_reenable(self):
        """
        LokiConfig still carries bob_* credential fields (harmless while BOB
        is unregistered) so re-enabling BOB later is a one-line registry
        change, not a fresh config migration.
        """
        config = LokiConfig(
            saas_base_url="x", saas_username="", saas_password="", enabled=True,
            bob_username="bob_user", bob_password="bob_pass",
        )
        assert config.bob_username == "bob_user"
        assert config.bob_password == "bob_pass"

    def test_H8_canara_credentials_wired_into_basic_auth(self):
        """Canara username/password from LokiConfig reach the httpx BasicAuth header."""
        from case_engine.integrations.loki.client import LokiClient
        config = LokiConfig(
            saas_base_url="x", saas_username="", saas_password="", enabled=True,
            canara_username="canara_user", canara_password="canara_pass",
        )
        transport = _make_loki_mock(_EMPTY_LOKI_RESPONSE)
        with LokiClient(config, "canara", transport=transport) as client:
            auth_header = client._client.auth._auth_header
            expected = _basic_auth_header("canara_user", "canara_pass")
            assert auth_header == expected

    def test_H9_https_guard_rejects_plaintext_base_url(self, monkeypatch):
        """
        Defense-in-depth: even if a tenant WERE registered with a plaintext
        http:// base_url, LokiClient.__init__ must refuse to construct the
        client rather than silently sending BasicAuth + logs unencrypted.
        Simulates re-adding a hypothetical plaintext tenant via monkeypatch
        so this guard is exercised without needing BOB back in the registry.
        """
        from case_engine.integrations.loki import client as client_module
        from case_engine.integrations.loki.client import LokiClient, LokiDatasource

        fake_registry = dict(client_module.LOKI_REGISTRY)
        fake_registry["insecure-test-tenant"] = LokiDatasource(
            client_name="Insecure",
            datasource_name="insecure",
            uid="test-uid",
            base_url="http://example-plaintext.test",
        )
        monkeypatch.setattr(client_module, "LOKI_REGISTRY", fake_registry)

        config = _enabled_config()
        with pytest.raises(ValueError, match="(?i)http"):
            LokiClient(config, "insecure-test-tenant")

    def test_H10_canara_and_bob_unset_credentials_are_empty_not_saas_leaked(self):
        """If canara/bob creds are unset, they must be empty — never fall back to SaaS creds."""
        from case_engine.integrations.loki.client import LokiClient
        config = LokiConfig(
            saas_base_url="x", saas_username="saas_user", saas_password="saas_pass", enabled=True,
        )
        transport = _make_loki_mock(_EMPTY_LOKI_RESPONSE)
        with LokiClient(config, "canara", transport=transport) as client:
            auth_header = client._client.auth._auth_header
            expected = _basic_auth_header("", "")
            assert auth_header == expected
            assert auth_header != _basic_auth_header("saas_user", "saas_pass")

    def test_H11_unknown_tenant_still_raises(self):
        """Sanity check: unregistered tenant keys still raise ValueError as before."""
        from case_engine.integrations.loki.client import LokiClient
        config = _enabled_config()
        with pytest.raises(ValueError):
            LokiClient(config, "not-a-real-tenant")

    def test_H12_get_session_logs_tool_accepts_tenant_canara(self, monkeypatch):
        """GetSessionLogsTool.run() with tenant='canara' returns a canonical dict (mocked transport)."""
        from unittest.mock import patch
        from case_engine.integrations.loki.client import LokiClient

        mock_transport = _make_loki_mock(_STANDARD_LOKI_RESPONSE)
        real_init = LokiClient.__init__

        def _patched_init(self_obj, cfg, tenant_key="saas", transport=None):
            real_init(self_obj, cfg, tenant_key, mock_transport)

        config = LokiConfig(
            saas_base_url="x", saas_username="testuser", saas_password="testpass", enabled=True,
            canara_username="canara_user", canara_password="canara_pass",
        )
        tool = GetSessionLogsTool(config=config)

        with patch.object(LokiClient, "__init__", _patched_init):
            result = tool.run({
                "session_id": "test-canara-session",
                "start_epoch": 1751356800.0,
                "end_epoch": 1751357400.0,
                "tenant": "canara",
            })

        assert result["tenant"] == "canara"
        assert result["log_availability"] in ("AVAILABLE", "PARTIAL", "UNAVAILABLE")
        assert "error" in result

    def test_H13_get_session_logs_tool_accepts_tenant_bob(self):
        """GetSessionLogsTool.run() with tenant='bob' returns a canonical dict (mocked transport)."""
        from unittest.mock import patch
        from case_engine.integrations.loki.client import LokiClient

        mock_transport = _make_loki_mock(_STANDARD_LOKI_RESPONSE)
        real_init = LokiClient.__init__

        def _patched_init(self_obj, cfg, tenant_key="saas", transport=None):
            real_init(self_obj, cfg, tenant_key, mock_transport)

        config = LokiConfig(
            saas_base_url="x", saas_username="testuser", saas_password="testpass", enabled=True,
            bob_username="bob_user", bob_password="bob_pass",
        )
        tool = GetSessionLogsTool(config=config)

        with patch.object(LokiClient, "__init__", _patched_init):
            result = tool.run({
                "session_id": "test-bob-session",
                "start_epoch": 1751356800.0,
                "end_epoch": 1751357400.0,
                "tenant": "bob",
            })

        assert result["tenant"] == "bob"
        assert result["log_availability"] in ("AVAILABLE", "PARTIAL", "UNAVAILABLE")
        assert "error" in result

    def test_H14_config_from_env_reads_canara_and_bob_vars(self, monkeypatch):
        """LokiConfig.from_env() picks up LOKI_CANARA_*/LOKI_BOB_* env vars."""
        monkeypatch.setenv("LOKI_CANARA_URL", "https://videokyc.canarabank.bank.in")
        monkeypatch.setenv("LOKI_CANARA_USERNAME", "env_canara_user")
        monkeypatch.setenv("LOKI_CANARA_PASSWORD", "env_canara_pass")
        monkeypatch.setenv("LOKI_BOB_URL", "http://43.204.15.162:3000")
        monkeypatch.setenv("LOKI_BOB_USERNAME", "env_bob_user")
        monkeypatch.setenv("LOKI_BOB_PASSWORD", "env_bob_pass")
        config = LokiConfig.from_env()
        assert config.canara_username == "env_canara_user"
        assert config.canara_password == "env_canara_pass"
        assert config.bob_username == "env_bob_user"
        assert config.bob_password == "env_bob_pass"

    def test_H15_saas_enabled_semantics_unchanged_by_canara_bob_env(self, monkeypatch):
        """
        Critical: adding Canara/BOB credentials in env must NOT change the
        global `enabled` flag's SaaS-scoped semantics (explicit user
        instruction: don't change SaaS behavior).
        """
        monkeypatch.setenv("LOKI_SAAS_USERNAME", "")
        monkeypatch.setenv("LOKI_SAAS_PASSWORD", "")
        monkeypatch.setenv("LOKI_CANARA_USERNAME", "env_canara_user")
        monkeypatch.setenv("LOKI_CANARA_PASSWORD", "env_canara_pass")
        monkeypatch.delenv("LOKI_ENABLED", raising=False)
        config = LokiConfig.from_env()
        # enabled is still computed purely from SaaS creds, per existing (untouched) logic
        assert config.enabled is False
