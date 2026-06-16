"""
tests/test_sprint223_executor.py

Sprint 2.23: ActionExecutor + MockExecutionAdapter tests.

Coverage:
  - MockExecutionAdapter always-success actions
  - MockExecutionAdapter always-fail actions
  - MockExecutionAdapter default happy path
  - ActionExecutor.execute() success path
  - ActionExecutor.execute() failure path
  - ActionExecutor.execute() adapter_name exposed
  - ActionExecutor never raises on bad adapter
  - build_action_executor factory
  - ExecutionAdapter interface (abstract)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.execution.executor import (
    ActionExecutor,
    ExecutionAdapter,
    MockExecutionAdapter,
    build_action_executor,
    _ALWAYS_FAIL_ACTIONS,
)
from case_engine.execution.models import ExecutionResult, ExecutionStatus


# ── MockExecutionAdapter ──────────────────────────────────────────────────────

class TestMockExecutionAdapterName:
    def test_adapter_name(self):
        assert MockExecutionAdapter().adapter_name == "mock"


class TestMockExecutionAdapterSuccess:
    def _run(self, action_type: str) -> dict:
        return MockExecutionAdapter().run(action_type, {}, "case-001")

    def test_resend_otp_success(self):
        r = self._run("resend_otp")
        assert r["success"] is True

    def test_retry_ocr_success(self):
        r = self._run("retry_ocr")
        assert r["success"] is True

    def test_refresh_session_success(self):
        r = self._run("refresh_session")
        assert r["success"] is True

    def test_ping_callback_success(self):
        r = self._run("ping_callback")
        assert r["success"] is True

    def test_unknown_action_success(self):
        r = self._run("unknown_action_xyz")
        assert r["success"] is True

    def test_success_has_response_data(self):
        r = self._run("resend_otp")
        assert isinstance(r["response_data"], dict)
        assert r["response_data"].get("mock") is True

    def test_success_error_code_none(self):
        assert self._run("resend_otp")["error_code"] is None

    def test_success_error_message_none(self):
        assert self._run("resend_otp")["error_message"] is None


class TestMockExecutionAdapterFailure:
    def _run(self, action_type: str) -> dict:
        return MockExecutionAdapter().run(action_type, {}, "case-001")

    def test_simulate_failure_fails(self):
        r = self._run("simulate_failure")
        assert r["success"] is False

    def test_fail_for_test_fails(self):
        r = self._run("fail_for_test")
        assert r["success"] is False

    def test_failure_has_error_code(self):
        r = self._run("simulate_failure")
        assert r["error_code"] == "MOCK_FORCED_FAILURE"

    def test_failure_has_error_message(self):
        r = self._run("simulate_failure")
        assert r["error_message"] is not None

    def test_failure_empty_response_data(self):
        r = self._run("simulate_failure")
        assert r["response_data"] == {}


# ── ActionExecutor ────────────────────────────────────────────────────────────

class TestActionExecutorFactory:
    def test_build_returns_executor(self):
        e = build_action_executor()
        assert isinstance(e, ActionExecutor)

    def test_build_with_adapter(self):
        adapter = MockExecutionAdapter()
        e = build_action_executor(adapter)
        assert e.adapter_name == "mock"

    def test_default_adapter_is_mock(self):
        e = ActionExecutor()
        assert e.adapter_name == "mock"


class TestActionExecutorSuccess:
    def _executor(self) -> ActionExecutor:
        return build_action_executor()

    def test_execute_resend_otp_success(self):
        r = self._executor().execute("resend_otp", {}, "case-001")
        assert r.success is True
        assert r.status == ExecutionStatus.SUCCESS

    def test_execute_returns_execution_result(self):
        r = self._executor().execute("resend_otp", {})
        assert isinstance(r, ExecutionResult)

    def test_execute_result_id_set(self):
        r = self._executor().execute("resend_otp", {})
        assert r.result_id != ""

    def test_execute_adapter_name(self):
        r = self._executor().execute("resend_otp", {})
        assert r.adapter_name == "mock"

    def test_execute_action_type_stored(self):
        r = self._executor().execute("retry_ocr", {"doc": "x"})
        assert r.action_type == "retry_ocr"

    def test_execute_params_stored(self):
        params = {"session_id": "s-123"}
        r = self._executor().execute("reset_session", params)
        assert r.action_params == params

    def test_execute_duration_ms_non_negative(self):
        r = self._executor().execute("resend_otp", {})
        assert r.duration_ms >= 0

    def test_execute_executed_at_set(self):
        r = self._executor().execute("resend_otp", {})
        assert r.executed_at != ""


class TestActionExecutorFailure:
    def _executor(self) -> ActionExecutor:
        return build_action_executor()

    def test_execute_simulate_failure(self):
        r = self._executor().execute("simulate_failure", {})
        assert r.success is False
        assert r.status == ExecutionStatus.FAILED

    def test_execute_failure_has_error_code(self):
        r = self._executor().execute("simulate_failure", {})
        assert r.error_code is not None

    def test_execute_failure_has_error_message(self):
        r = self._executor().execute("simulate_failure", {})
        assert r.error_message is not None


class TestActionExecutorNeverRaises:
    def test_exception_in_adapter_does_not_raise(self):
        bad_adapter = MagicMock()
        bad_adapter.adapter_name = "bad"
        bad_adapter.run.side_effect = RuntimeError("boom")
        executor = ActionExecutor(adapter=bad_adapter)
        result = executor.execute("any", {})
        assert result.status == ExecutionStatus.FAILED
        assert result.error_code == "EXECUTOR_INTERNAL_ERROR"

    def test_exception_result_has_error_message(self):
        bad_adapter = MagicMock()
        bad_adapter.adapter_name = "bad"
        bad_adapter.run.side_effect = ValueError("oops")
        executor = ActionExecutor(adapter=bad_adapter)
        result = executor.execute("any", {})
        assert "ValueError" in (result.error_message or "")


# ── ExecutionAdapter interface ────────────────────────────────────────────────

class TestExecutionAdapterInterface:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            ExecutionAdapter()  # type: ignore[abstract]

    def test_mock_is_subclass(self):
        assert issubclass(MockExecutionAdapter, ExecutionAdapter)
