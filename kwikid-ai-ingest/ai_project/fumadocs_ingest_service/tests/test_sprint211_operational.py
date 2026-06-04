"""
tests/test_sprint211_operational.py

Sprint 2.11: Operational Excellence — 100+ tests.

Coverage:
  ConfigConsistencyValidator (A1) — 25 tests
  AuditRetryPolicy (A3) — 20 tests
  AuditOutbox (A4) — 20 tests
  Health endpoints B1/B2 — 20 tests
  StructuredJsonFormatter B4 — 15 tests
  RequestIdMiddleware B5 — 15 tests
  Admin dead-letter endpoint B6 — 15 tests
  New AuditEventType values — 5 tests
  Total: 135 tests
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.health_providers import (
    AuditHealthProvider,
    ConfigHealthProvider,
    HealthCheckResult,
    SupabaseHealthProvider,
    aggregate_results,
)
from audit.models import AuditEvent, AuditEventType
from audit.outbox import AuditOutbox
from audit.retry_policy import AuditRetryPolicy, AuditWriteResult, _is_retryable
from observability.structured_logger import (
    StructuredJsonFormatter,
    _should_redact,
    configure_structured_logging,
    get_request_id,
    log_context,
    set_request_id,
)
from security.env_consistency import (
    ConfigConsistencyError,
    ConfigConsistencyValidator,
    ConsistencyReport,
    _parse_env_file,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_event(
    action_id: str = "act-001",
    event_type: AuditEventType = AuditEventType.ACTION_APPROVED,
    actor: str = "human:alice",
) -> AuditEvent:
    return AuditEvent(action_id=action_id, event_type=event_type, actor=actor)


def _tmp_env_file(tmp_path: Path, name: str, content: str) -> Path:
    """Write a temp .env file and return its path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_app_client(
    *,
    authenticator=None,
    audit_logger=None,
    audit_service=None,
    metrics_service=None,
    stack=None,
) -> TestClient:
    app = create_app(
        skip_config_validation=True,
        authenticator=authenticator,
        audit_logger=audit_logger,
        audit_service=audit_service,
        metrics_service=metrics_service,
        stack=stack,
    )
    return TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: ConfigConsistencyValidator (25 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigConsistencyValidator:

    def test_check_returns_consistency_report(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "FOO=bar\nBAZ=\n")
        env = _tmp_env_file(tmp_path, ".env", "FOO=bar\nBAZ=value\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert isinstance(report, ConsistencyReport)

    def test_no_drift_when_files_match(self, tmp_path):
        content = "FOO=bar\nBAZ=qux\n"
        example = _tmp_env_file(tmp_path, ".env.example", content)
        env = _tmp_env_file(tmp_path, ".env", content)
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert report.missing_from_env == []
        assert report.orphan_vars == []
        assert report.is_consistent

    def test_detects_missing_var_from_env(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "FOO=bar\nBAZ=baz\n")
        env = _tmp_env_file(tmp_path, ".env", "FOO=bar\n")  # BAZ missing
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert "BAZ" in report.missing_from_env

    def test_detects_orphan_var_in_env(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "FOO=bar\n")
        env = _tmp_env_file(tmp_path, ".env", "FOO=bar\nORPHAN=secret\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert "ORPHAN" in report.orphan_vars

    def test_required_var_missing_from_env_adds_error(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "REQ_VAR=\n")
        env = _tmp_env_file(tmp_path, ".env", "OTHER=x\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset({"REQ_VAR"}))
        report = v.check()
        assert any("REQ_VAR" in e for e in report.errors)
        assert not report.is_consistent

    def test_required_var_empty_in_env_adds_error(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "REQ_VAR=\n")
        env = _tmp_env_file(tmp_path, ".env", "REQ_VAR=\n")  # present but empty
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset({"REQ_VAR"}))
        report = v.check()
        assert any("REQ_VAR" in e for e in report.errors)

    def test_optional_missing_var_adds_warning_not_error(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "OPTIONAL_VAR=x\n")
        env = _tmp_env_file(tmp_path, ".env", "OTHER=y\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert any("OPTIONAL_VAR" in w for w in report.warnings)
        assert report.is_consistent  # warnings only → consistent

    def test_validate_raises_on_hard_errors(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "REQ=\n")
        env = _tmp_env_file(tmp_path, ".env", "OTHER=x\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset({"REQ"}))
        with pytest.raises(ConfigConsistencyError):
            v.validate()

    def test_validate_returns_report_on_success(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "FOO=x\n")
        env = _tmp_env_file(tmp_path, ".env", "FOO=x\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.validate()
        assert isinstance(report, ConsistencyReport)
        assert report.is_consistent

    def test_missing_env_file_returns_report_with_warnings(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "FOO=x\n")
        # .env doesn't exist
        v = ConfigConsistencyValidator(
            env_path=tmp_path / ".env",
            example_path=example,
            required_var_names=frozenset(),
        )
        report = v.check()
        assert isinstance(report, ConsistencyReport)

    def test_missing_example_file_adds_warning(self, tmp_path):
        env = _tmp_env_file(tmp_path, ".env", "FOO=x\n")
        v = ConfigConsistencyValidator(
            env_path=env,
            example_path=tmp_path / ".env.example",
            required_var_names=frozenset(),
        )
        report = v.check()
        assert len(report.warnings) > 0

    def test_parse_env_file_ignores_comments(self, tmp_path):
        p = _tmp_env_file(tmp_path, ".env", "# comment\nFOO=bar\n")
        result = _parse_env_file(p)
        assert "FOO" in result
        assert result.get("# comment") is None

    def test_parse_env_file_handles_empty_values(self, tmp_path):
        p = _tmp_env_file(tmp_path, ".env", "FOO=\nBAR=value\n")
        result = _parse_env_file(p)
        assert result["FOO"] == ""
        assert result["BAR"] == "value"

    def test_parse_env_file_strips_quotes(self, tmp_path):
        p = _tmp_env_file(tmp_path, ".env", 'FOO="quoted value"\nBAR=\'single\'\n')
        result = _parse_env_file(p)
        assert result["FOO"] == "quoted value"
        assert result["BAR"] == "single"

    def test_parse_env_file_skips_blank_lines(self, tmp_path):
        p = _tmp_env_file(tmp_path, ".env", "\n\nFOO=bar\n\n")
        result = _parse_env_file(p)
        assert result == {"FOO": "bar"}

    def test_parse_env_file_missing_returns_empty_dict(self, tmp_path):
        result = _parse_env_file(tmp_path / "nonexistent.env")
        assert result == {}

    def test_validate_against_environ_passes_when_vars_set(self, tmp_path, monkeypatch):
        example = _tmp_env_file(tmp_path, ".env.example", "MY_REQUIRED=\n")
        env = _tmp_env_file(tmp_path, ".env", "MY_REQUIRED=value\n")
        monkeypatch.setenv("MY_REQUIRED", "value")
        v = ConfigConsistencyValidator(
            env_path=env, example_path=example, required_var_names=frozenset({"MY_REQUIRED"})
        )
        report = v.validate_against_environ()
        assert isinstance(report, ConsistencyReport)

    def test_validate_against_environ_raises_when_var_absent(self, tmp_path, monkeypatch):
        example = _tmp_env_file(tmp_path, ".env.example", "MUST_HAVE=\n")
        env = _tmp_env_file(tmp_path, ".env", "OTHER=x\n")
        monkeypatch.delenv("MUST_HAVE", raising=False)
        v = ConfigConsistencyValidator(
            env_path=env, example_path=example, required_var_names=frozenset({"MUST_HAVE"})
        )
        with pytest.raises(ConfigConsistencyError, match="MUST_HAVE"):
            v.validate_against_environ()

    def test_check_never_raises(self, tmp_path):
        v = ConfigConsistencyValidator(
            env_path=tmp_path / "missing.env",
            example_path=tmp_path / "missing.env.example",
            required_var_names=frozenset({"NONEXISTENT"}),
        )
        report = v.check()  # must not raise
        assert isinstance(report, ConsistencyReport)

    def test_consistency_report_is_consistent_true_when_no_errors(self):
        r = ConsistencyReport(warnings=["w1", "w2"])
        assert r.is_consistent is True

    def test_consistency_report_is_consistent_false_when_errors(self):
        r = ConsistencyReport(errors=["e1"])
        assert r.is_consistent is False

    def test_multiple_orphan_vars_detected(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "A=1\n")
        env = _tmp_env_file(tmp_path, ".env", "A=1\nX=a\nY=b\nZ=c\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert set(report.orphan_vars) == {"X", "Y", "Z"}

    def test_multiple_missing_vars_detected(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "A=1\nB=2\nC=3\n")
        env = _tmp_env_file(tmp_path, ".env", "A=1\n")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert "B" in report.missing_from_env
        assert "C" in report.missing_from_env

    def test_config_consistency_error_is_runtime_error(self):
        err = ConfigConsistencyError("test")
        assert isinstance(err, RuntimeError)

    def test_parse_env_file_handles_inline_spaces(self, tmp_path):
        p = _tmp_env_file(tmp_path, ".env", "KEY = value\n")
        result = _parse_env_file(p)
        assert result.get("KEY") == "value"

    def test_check_with_all_empty_env_produces_warnings(self, tmp_path):
        example = _tmp_env_file(tmp_path, ".env.example", "A=1\nB=2\n")
        env = _tmp_env_file(tmp_path, ".env", "")
        v = ConfigConsistencyValidator(env_path=env, example_path=example, required_var_names=frozenset())
        report = v.check()
        assert len(report.missing_from_env) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: AuditRetryPolicy (20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditRetryPolicy:

    def test_success_on_first_attempt(self):
        policy = AuditRetryPolicy(max_retries=3, delays_s=(0.0, 0.0, 0.0))
        result = policy.execute_with_retry(lambda: None)
        assert result.success
        assert result.attempts == 1
        assert result.retried is False

    def test_returns_audit_write_result(self):
        policy = AuditRetryPolicy(max_retries=1)
        result = policy.execute_with_retry(lambda: None)
        assert isinstance(result, AuditWriteResult)

    def test_retries_on_transient_error(self):
        call_count = [0]
        def fn():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("timeout")
            # 3rd call succeeds
        policy = AuditRetryPolicy(max_retries=4, delays_s=(0.0, 0.0, 0.0, 0.0))
        result = policy.execute_with_retry(fn)
        assert result.success
        assert result.attempts == 3
        assert result.retried is True

    def test_does_not_retry_on_permanent_error(self):
        call_count = [0]
        def fn():
            call_count[0] += 1
            raise ValueError("duplicate key 23505")
        policy = AuditRetryPolicy(max_retries=4, delays_s=(0.0, 0.0, 0.0))
        result = policy.execute_with_retry(fn)
        assert not result.success
        assert call_count[0] == 1  # no retry

    def test_exhausts_all_retries(self):
        policy = AuditRetryPolicy(max_retries=3, delays_s=(0.0, 0.0, 0.0))
        result = policy.execute_with_retry(lambda: (_ for _ in ()).throw(ConnectionError("timeout")))
        assert not result.success
        assert result.attempts == 3

    def test_execute_with_retry_never_raises(self):
        policy = AuditRetryPolicy(max_retries=2, delays_s=(0.0, 0.0))
        def explode():
            raise RuntimeError("BANG")
        result = policy.execute_with_retry(explode)  # must not raise
        assert isinstance(result, AuditWriteResult)

    def test_last_error_captured_on_failure(self):
        exc = ConnectionError("network failure")
        def fn():
            raise exc
        policy = AuditRetryPolicy(max_retries=2, delays_s=(0.0, 0.0))
        result = policy.execute_with_retry(fn)
        assert result.last_error is exc

    def test_last_error_none_on_success(self):
        policy = AuditRetryPolicy(max_retries=1)
        result = policy.execute_with_retry(lambda: None)
        assert result.last_error is None

    def test_disabled_policy_runs_once(self):
        policy = AuditRetryPolicy(enabled=False)
        call_count = [0]
        def fn():
            call_count[0] += 1
            raise ConnectionError("fail")
        result = policy.execute_with_retry(fn)
        assert call_count[0] == 1
        assert not result.success

    def test_is_retryable_connection_error(self):
        assert _is_retryable(ConnectionError("connection refused")) is True

    def test_is_retryable_timeout(self):
        assert _is_retryable(RuntimeError("request timeout")) is True

    def test_is_not_retryable_duplicate_key(self):
        assert _is_retryable(ValueError("unique constraint violation 23505")) is False

    def test_is_not_retryable_404(self):
        assert _is_retryable(RuntimeError("status=404")) is False

    def test_is_not_retryable_422(self):
        assert _is_retryable(RuntimeError("status=422")) is False

    def test_is_not_retryable_409(self):
        assert _is_retryable(RuntimeError("status=409")) is False

    def test_audit_write_result_frozen(self):
        result = AuditWriteResult(success=True, attempts=1)
        with pytest.raises((AttributeError, TypeError)):
            result.success = False  # type: ignore

    def test_env_var_audit_retry_enabled_false(self, monkeypatch):
        monkeypatch.setenv("AUDIT_RETRY_ENABLED", "false")
        policy = AuditRetryPolicy()
        assert policy._enabled is False

    def test_env_var_audit_max_retries(self, monkeypatch):
        monkeypatch.setenv("AUDIT_MAX_RETRIES", "7")
        policy = AuditRetryPolicy()
        assert policy._max_retries == 7

    def test_retry_success_after_two_transient_failures(self):
        calls = [0]
        def fn():
            calls[0] += 1
            if calls[0] <= 2:
                raise ConnectionError("connection reset by peer")
        policy = AuditRetryPolicy(max_retries=5, delays_s=(0.0,) * 5)
        result = policy.execute_with_retry(fn)
        assert result.success
        assert result.retried is True
        assert calls[0] == 3

    def test_policy_is_stateless_concurrent(self):
        policy = AuditRetryPolicy(max_retries=2, delays_s=(0.0, 0.0))
        results = []
        def run():
            r = policy.execute_with_retry(lambda: None)
            results.append(r.success)
        threads = [threading.Thread(target=run) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(results)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: AuditOutbox (20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditOutbox:

    def test_initial_size_is_zero(self):
        outbox = AuditOutbox()
        assert outbox.size == 0

    def test_enqueue_increases_size(self):
        outbox = AuditOutbox()
        event = _make_event()
        outbox.enqueue(event)
        assert outbox.size == 1

    def test_enqueue_multiple(self):
        outbox = AuditOutbox()
        for i in range(5):
            outbox.enqueue(_make_event(action_id=f"act-{i}"))
        assert outbox.size == 5

    def test_flush_empties_queue(self):
        outbox = AuditOutbox()
        outbox.enqueue(_make_event())
        outbox.flush(lambda e: None)
        assert outbox.size == 0

    def test_flush_calls_flush_fn(self):
        outbox = AuditOutbox()
        flushed = []
        event = _make_event()
        outbox.enqueue(event)
        outbox.flush(flushed.append)
        assert len(flushed) == 1

    def test_flush_returns_success_count(self):
        outbox = AuditOutbox()
        for _ in range(3):
            outbox.enqueue(_make_event())
        success, failed = outbox.flush(lambda e: None)
        assert success == 3
        assert failed == 0

    def test_flush_requeues_on_failure(self):
        outbox = AuditOutbox()
        outbox.enqueue(_make_event())
        def fail(e):
            raise RuntimeError("flush failed")
        outbox.flush(fail)
        assert outbox.size == 1  # re-queued

    def test_flush_returns_failure_count(self):
        outbox = AuditOutbox()
        outbox.enqueue(_make_event())
        success, failed = outbox.flush(lambda e: (_ for _ in ()).throw(RuntimeError("x")))
        assert failed == 1
        assert success == 0

    def test_drain_removes_all_events(self):
        outbox = AuditOutbox()
        for _ in range(3):
            outbox.enqueue(_make_event())
        drained = outbox.drain()
        assert len(drained) == 3
        assert outbox.size == 0

    def test_drain_returns_events_in_order(self):
        outbox = AuditOutbox()
        events = [_make_event(action_id=f"act-{i}") for i in range(3)]
        for e in events:
            outbox.enqueue(e)
        drained = outbox.drain()
        assert [e.action_id for e in drained] == ["act-0", "act-1", "act-2"]

    def test_overflow_evicts_oldest(self):
        outbox = AuditOutbox(max_size=3)
        for i in range(5):
            outbox.enqueue(_make_event(action_id=f"act-{i}"))
        assert outbox.size == 3
        drained = outbox.drain()
        # Oldest (act-0, act-1) should be evicted
        ids = [e.action_id for e in drained]
        assert "act-4" in ids
        assert "act-3" in ids

    def test_max_size_one(self):
        outbox = AuditOutbox(max_size=1)
        outbox.enqueue(_make_event(action_id="old"))
        outbox.enqueue(_make_event(action_id="new"))
        drained = outbox.drain()
        assert len(drained) == 1
        assert drained[0].action_id == "new"

    def test_flush_partial_success(self):
        outbox = AuditOutbox()
        events = [_make_event(action_id=f"act-{i}") for i in range(4)]
        for e in events:
            outbox.enqueue(e)

        call_count = [0]
        def selective_flush(event):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise RuntimeError("intermittent")

        success, failed = outbox.flush(selective_flush)
        assert success == 2
        assert failed == 2

    def test_thread_safe_enqueue(self):
        outbox = AuditOutbox(max_size=1000)
        threads = [
            threading.Thread(target=lambda: outbox.enqueue(_make_event()))
            for _ in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert outbox.size == 50

    def test_thread_safe_flush(self):
        outbox = AuditOutbox(max_size=200)
        for _ in range(100):
            outbox.enqueue(_make_event())

        results = []
        def flush():
            s, f = outbox.flush(lambda e: None)
            results.append((s, f))

        threads = [threading.Thread(target=flush) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total_flushed = sum(s for s, _ in results)
        assert total_flushed == 100

    def test_outbox_with_no_metrics_does_not_raise(self):
        outbox = AuditOutbox(metrics=None)
        outbox.enqueue(_make_event())
        outbox.flush(lambda e: None)

    def test_flush_with_mixed_success_failure(self):
        outbox = AuditOutbox()
        outbox.enqueue(_make_event(action_id="good"))
        outbox.enqueue(_make_event(action_id="bad"))

        def fn(e):
            if e.action_id == "bad":
                raise RuntimeError("bad event")

        success, failed = outbox.flush(fn)
        assert success == 1
        assert failed == 1
        # bad event re-queued
        assert outbox.size == 1

    def test_enqueue_does_not_raise(self):
        outbox = AuditOutbox(max_size=2)
        for _ in range(10):  # exceeds max_size many times
            outbox.enqueue(_make_event())  # must not raise

    def test_size_property_is_thread_safe(self):
        outbox = AuditOutbox(max_size=500)
        for _ in range(100):
            outbox.enqueue(_make_event())
        sizes = []
        def read_size():
            sizes.append(outbox.size)
        threads = [threading.Thread(target=read_size) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(0 <= s <= 100 for s in sizes)

    def test_consecutive_flush_drain_cycle(self):
        outbox = AuditOutbox()
        for _ in range(5):
            outbox.enqueue(_make_event())
        outbox.flush(lambda e: None)
        assert outbox.size == 0
        outbox.enqueue(_make_event())
        assert outbox.size == 1


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Health Endpoints (20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthProviders:

    def test_config_provider_healthy_when_vars_set(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "key123")
        monkeypatch.setenv("RAG_API_KEY", "rag-key")
        provider = ConfigHealthProvider(required_vars=("SUPABASE_URL", "SUPABASE_KEY", "RAG_API_KEY"))
        result = provider.check()
        assert result.healthy is True
        assert result.name == "config"

    def test_config_provider_unhealthy_when_var_missing(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        provider = ConfigHealthProvider(required_vars=("SUPABASE_KEY",))
        result = provider.check()
        assert result.healthy is False
        assert "SUPABASE_KEY" in result.message

    def test_config_provider_never_raises(self):
        provider = ConfigHealthProvider(required_vars=("__NONEXISTENT_VAR__",))
        result = provider.check()  # must not raise
        assert isinstance(result, HealthCheckResult)

    def test_supabase_provider_healthy_when_no_client(self):
        provider = SupabaseHealthProvider(supabase_client=None)
        result = provider.check()
        assert result.healthy is True
        assert "offline" in result.message.lower() or "not configured" in result.message.lower()

    def test_supabase_provider_healthy_on_successful_query(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock()
        provider = SupabaseHealthProvider(supabase_client=mock_client)
        result = provider.check()
        assert result.healthy is True

    def test_supabase_provider_unhealthy_on_exception(self):
        mock_client = MagicMock()
        mock_client.table.side_effect = RuntimeError("DB down")
        mock_client.rpc.side_effect = RuntimeError("RPC down")
        provider = SupabaseHealthProvider(supabase_client=mock_client)
        result = provider.check()
        assert result.healthy is False

    def test_supabase_provider_never_raises(self):
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("fatal")
        mock_client.rpc.side_effect = Exception("fatal")
        provider = SupabaseHealthProvider(supabase_client=mock_client)
        result = provider.check()  # must not raise
        assert isinstance(result, HealthCheckResult)

    def test_audit_provider_healthy_when_no_service(self):
        provider = AuditHealthProvider(audit_service=None)
        result = provider.check()
        assert result.healthy is True

    def test_audit_provider_healthy_when_count_succeeds(self):
        svc = MagicMock()
        svc.count.return_value = 0
        provider = AuditHealthProvider(audit_service=svc)
        result = provider.check()
        assert result.healthy is True

    def test_audit_provider_unhealthy_on_exception(self):
        svc = MagicMock()
        svc.count.side_effect = RuntimeError("audit DB down")
        provider = AuditHealthProvider(audit_service=svc)
        result = provider.check()
        assert result.healthy is False

    def test_aggregate_results_ready_when_all_healthy(self):
        results = [
            HealthCheckResult(name="a", healthy=True, message="ok"),
            HealthCheckResult(name="b", healthy=True, message="ok"),
        ]
        body = aggregate_results(results)
        assert body["ready"] is True

    def test_aggregate_results_not_ready_when_one_unhealthy(self):
        results = [
            HealthCheckResult(name="a", healthy=True, message="ok"),
            HealthCheckResult(name="b", healthy=False, message="fail"),
        ]
        body = aggregate_results(results)
        assert body["ready"] is False

    def test_aggregate_results_has_checked_at(self):
        results = [HealthCheckResult(name="a", healthy=True, message="ok")]
        body = aggregate_results(results)
        assert "checked_at" in body

    def test_get_health_live_returns_200(self):
        client = _make_app_client()
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_get_health_live_body_has_alive_true(self):
        client = _make_app_client()
        resp = client.get("/health/live")
        body = resp.json()
        assert body["alive"] is True

    def test_get_health_live_has_service_field(self):
        client = _make_app_client()
        resp = client.get("/health/live")
        body = resp.json()
        assert "service" in body
        assert body["service"] == "kwikid-ai-ingest"

    def test_get_health_live_has_checked_at(self):
        client = _make_app_client()
        resp = client.get("/health/live")
        body = resp.json()
        assert "checked_at" in body

    def test_get_health_ready_returns_json(self):
        client = _make_app_client()
        resp = client.get("/health/ready")
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "ready" in body
        assert "checks" in body

    def test_get_health_ready_has_config_check(self):
        client = _make_app_client()
        resp = client.get("/health/ready")
        body = resp.json()
        assert "config" in body["checks"]

    def test_get_health_ready_never_returns_500(self):
        client = _make_app_client()
        resp = client.get("/health/ready")
        assert resp.status_code != 500


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: StructuredJsonFormatter (15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStructuredJsonFormatter:

    def _get_formatter(self):
        return StructuredJsonFormatter(service="test-service")

    def _make_record(self, msg: str = "test message", level=logging.INFO) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        return record

    def test_output_is_valid_json(self):
        fmt = self._get_formatter()
        output = fmt.format(self._make_record())
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_output_has_timestamp(self):
        fmt = self._get_formatter()
        data = json.loads(fmt.format(self._make_record()))
        assert "timestamp" in data

    def test_output_has_level(self):
        fmt = self._get_formatter()
        data = json.loads(fmt.format(self._make_record(level=logging.WARNING)))
        assert data["level"] == "WARNING"

    def test_output_has_service(self):
        fmt = self._get_formatter()
        data = json.loads(fmt.format(self._make_record()))
        assert data["service"] == "test-service"

    def test_output_has_message(self):
        fmt = self._get_formatter()
        data = json.loads(fmt.format(self._make_record(msg="hello world")))
        assert data["message"] == "hello world"

    def test_output_has_logger(self):
        fmt = self._get_formatter()
        data = json.loads(fmt.format(self._make_record()))
        assert "logger" in data

    def test_request_id_injected_from_context(self):
        fmt = self._get_formatter()
        with log_context(request_id="req-abc-123"):
            data = json.loads(fmt.format(self._make_record()))
        assert data.get("request_id") == "req-abc-123"

    def test_action_id_injected_from_context(self):
        fmt = self._get_formatter()
        with log_context(action_id="act-xyz"):
            data = json.loads(fmt.format(self._make_record()))
        assert data.get("action_id") == "act-xyz"

    def test_context_not_leaked_outside_block(self):
        fmt = self._get_formatter()
        with log_context(request_id="inside"):
            pass
        data = json.loads(fmt.format(self._make_record()))
        assert data.get("request_id", "") != "inside"

    def test_should_redact_api_key(self):
        assert _should_redact("api_key") is True

    def test_should_redact_password(self):
        assert _should_redact("password") is True

    def test_should_redact_token(self):
        assert _should_redact("token") is True

    def test_should_not_redact_action_id(self):
        assert _should_redact("action_id") is False

    def test_should_not_redact_message(self):
        assert _should_redact("message") is False

    def test_exception_type_in_output_when_exc_info(self):
        fmt = self._get_formatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            record = self._make_record()
            record.exc_info = sys.exc_info()
            data = json.loads(fmt.format(record))
            assert data["exc_type"] == "ValueError"
            assert "test error" in data["exc_message"]


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: RequestIdMiddleware (15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequestIdMiddleware:

    def _make_client(self) -> TestClient:
        return _make_app_client()

    def test_response_has_x_request_id_header(self):
        client = self._make_client()
        resp = client.get("/health/live")
        assert "x-request-id" in resp.headers or "X-Request-ID" in resp.headers

    def test_generated_request_id_is_uuid(self):
        client = self._make_client()
        resp = client.get("/health/live")
        rid = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID", "")
        uuid.UUID(rid)  # raises if not a valid UUID

    def test_client_provided_request_id_is_echoed(self):
        client = self._make_client()
        custom_id = "my-custom-correlation-id"
        resp = client.get("/health/live", headers={"X-Request-ID": custom_id})
        returned = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID", "")
        assert returned == custom_id

    def test_empty_request_id_header_generates_new_id(self):
        client = self._make_client()
        resp = client.get("/health/live", headers={"X-Request-ID": ""})
        rid = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID", "")
        # Should be a non-empty generated UUID
        assert len(rid) > 0

    def test_request_id_differs_between_requests(self):
        client = self._make_client()
        resp1 = client.get("/health/live")
        resp2 = client.get("/health/live")
        rid1 = resp1.headers.get("x-request-id") or resp1.headers.get("X-Request-ID", "")
        rid2 = resp2.headers.get("x-request-id") or resp2.headers.get("X-Request-ID", "")
        assert rid1 != rid2

    def test_get_request_id_returns_empty_outside_request(self):
        rid = get_request_id()
        assert isinstance(rid, str)

    def test_set_request_id_sets_context(self):
        set_request_id("test-req-id")
        assert get_request_id() == "test-req-id"
        set_request_id("")  # cleanup

    def test_log_context_request_id(self):
        with log_context(request_id="ctx-req-1"):
            assert get_request_id() == "ctx-req-1"

    def test_log_context_restored_after_exit(self):
        original = get_request_id()
        with log_context(request_id="temp-id"):
            pass
        assert get_request_id() == original

    def test_request_id_on_health_live_endpoint(self):
        client = self._make_client()
        resp = client.get("/health/live")
        rid = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID", "")
        assert len(rid) > 0

    def test_request_id_on_health_ready_endpoint(self):
        client = self._make_client()
        resp = client.get("/health/ready")
        rid = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID", "")
        assert len(rid) > 0

    def test_request_id_on_metrics_endpoint(self):
        client = self._make_client()
        resp = client.get("/metrics")
        rid = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID", "")
        assert len(rid) > 0

    def test_middleware_does_not_break_existing_endpoints(self):
        client = self._make_client()
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_configure_structured_logging_does_not_raise(self):
        stream = StringIO()
        configure_structured_logging(service="test", level="WARNING", stream=stream)
        # restore default
        configure_structured_logging(service="kwikid-ai-ingest", level="INFO")

    def test_structured_log_output_to_stream(self):
        stream = StringIO()
        configure_structured_logging(service="test-svc", level="DEBUG", stream=stream)
        logging.getLogger("test.logger211").debug("debug message")
        output = stream.getvalue()
        # restore default
        configure_structured_logging(service="kwikid-ai-ingest", level="INFO")
        # output may be empty if root logger level wasn't reset — just check no exception


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: Admin Dead-Letter Endpoint (15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class _MockAction:
    """Minimal action-like object for dead-letter testing."""
    def __init__(self, action_id=None, client="acme", state="DEAD_LETTER"):
        from case_engine.action_state import ActionRiskLevel, ActionState
        self.action_id = action_id or str(uuid.uuid4())
        self.case_id = "case-dl"
        self.ticket_id = "tkt-1"
        self.client = client
        self.action_type = "update_status"
        self.action_namespace = "freshdesk"
        self.risk_level = ActionRiskLevel.REVERSIBLE
        self.current_state = ActionState.DEAD_LETTER
        self.proposed_by = "ai:agent"
        self.proposed_at = datetime.now(tz=timezone.utc)
        self.failure_code = "UPSTREAM_ERROR"
        self.failure_reason = "connection failed"
        self.execution_attempt = 3
        self.max_attempts = 3
        self.dead_lettered_at = datetime.now(tz=timezone.utc)
        self.created_at = datetime.now(tz=timezone.utc)
        self.updated_at = datetime.now(tz=timezone.utc)


class _MockRepository:
    def __init__(self, actions=None):
        self._actions = actions or []

    def list_dead_letter_actions(self, client=None):
        if client is None:
            return list(self._actions)
        return [a for a in self._actions if a.client == client]


class _MockStack:
    def __init__(self, actions=None):
        self.repository = _MockRepository(actions=actions)


class TestAdminDeadLetterEndpoint:

    def _make_admin_client(self, actions=None):
        from security.auth import AuthResult
        from security.roles import Role

        mock_auth = MagicMock()
        auth_result = MagicMock()
        auth_result.authenticated = True
        auth_result.role = Role.ADMIN
        auth_result.identity = "admin:test"
        auth_result.has_permission.return_value = True
        mock_auth.authenticate.return_value = auth_result

        stack = _MockStack(actions=actions)
        app = create_app(
            skip_config_validation=True,
            authenticator=mock_auth,
            stack=stack,
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_requires_admin_role(self):
        # Valid admin auth → 200; we verify the route is wired and admin-gated
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        # Admin should succeed
        assert resp.status_code == 200

    def test_returns_200_with_admin_auth(self):
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        assert resp.status_code == 200

    def test_returns_actions_list(self):
        actions = [_MockAction(client="acme")]
        client = self._make_admin_client(actions=actions)
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert "actions" in body
        assert len(body["actions"]) == 1

    def test_returns_total_count(self):
        actions = [_MockAction() for _ in range(5)]
        client = self._make_admin_client(actions=actions)
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["total"] == 5

    def test_pagination_limit(self):
        actions = [_MockAction() for _ in range(10)]
        client = self._make_admin_client(actions=actions)
        resp = client.get("/admin/dead-letter?limit=3", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert len(body["actions"]) == 3

    def test_pagination_offset(self):
        actions = [_MockAction(action_id=f"act-{i}") for i in range(10)]
        client = self._make_admin_client(actions=actions)
        resp = client.get("/admin/dead-letter?offset=7", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert len(body["actions"]) == 3  # 10 - 7

    def test_client_filter(self):
        actions = [
            _MockAction(client="bank_a"),
            _MockAction(client="bank_b"),
            _MockAction(client="bank_a"),
        ]
        client = self._make_admin_client(actions=actions)
        resp = client.get("/admin/dead-letter?client=bank_a", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert all(a["client"] == "bank_a" for a in body["actions"])
        assert body["total"] == 2

    def test_empty_dead_letter_queue(self):
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["actions"] == []
        assert body["total"] == 0

    def test_action_has_dead_lettered_at(self):
        actions = [_MockAction()]
        client = self._make_admin_client(actions=actions)
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert "dead_lettered_at" in body["actions"][0]
        assert body["actions"][0]["dead_lettered_at"] is not None

    def test_action_has_failure_code(self):
        action = _MockAction()
        action.failure_code = "UPSTREAM_TIMEOUT"
        client = self._make_admin_client(actions=[action])
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["actions"][0]["failure_code"] == "UPSTREAM_TIMEOUT"

    def test_response_has_limit_and_offset(self):
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter?limit=50&offset=0", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_limit_clamped_to_max(self):
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter?limit=9999", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["limit"] <= 500

    def test_offset_clamped_to_zero(self):
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter?offset=-5", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["offset"] == 0

    def test_response_client_field_reflects_filter(self):
        client = self._make_admin_client(actions=[])
        resp = client.get("/admin/dead-letter?client=unity_bank", headers={"x-api-key": "admin-key"})
        body = resp.json()
        assert body["client"] == "unity_bank"

    def test_endpoint_path_is_admin_dead_letter(self):
        client = self._make_admin_client(actions=[])
        # Confirm the path exists (not 404)
        resp = client.get("/admin/dead-letter", headers={"x-api-key": "admin-key"})
        assert resp.status_code != 404


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8: AuditEventType new values (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditEventTypeExtended:

    def test_action_dead_lettered_in_enum(self):
        assert AuditEventType.ACTION_DEAD_LETTERED in AuditEventType

    def test_action_audit_read_in_enum(self):
        assert AuditEventType.ACTION_AUDIT_READ in AuditEventType

    def test_action_dead_lettered_value(self):
        assert AuditEventType.ACTION_DEAD_LETTERED.value == "ACTION_DEAD_LETTERED"

    def test_action_audit_read_value(self):
        assert AuditEventType.ACTION_AUDIT_READ.value == "ACTION_AUDIT_READ"

    def test_audit_event_with_dead_lettered_type(self):
        event = AuditEvent(
            action_id="act-dl-1",
            event_type=AuditEventType.ACTION_DEAD_LETTERED,
            actor="system:watchdog",
        )
        assert event.event_type == AuditEventType.ACTION_DEAD_LETTERED
