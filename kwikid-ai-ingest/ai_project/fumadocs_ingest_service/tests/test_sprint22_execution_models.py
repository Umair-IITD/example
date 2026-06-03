"""
tests/test_sprint22_execution_models.py

Sprint 2.2 — Execution model contract tests.

Covers:
  ExecutionContext
    - Frozen dataclass (immutable)
    - is_past_deadline() returns correct value
    - remaining_ms() is non-negative

  ExecutionResult
    - Frozen dataclass
    - __post_init__ rejects negative latency_ms
    - __post_init__ rejects success=True with error_code
    - __post_init__ rejects success=True with error_message
    - success=False with error fields is valid
    - default values are correct

  Exception hierarchy
    - ActionExecutionError carries failure_code
    - RetryableExecutionError and PermanentExecutionError subclass ActionExecutionError
    - RollbackError subclasses ActionExecutionError
    - Default failure codes are correct
    - Custom failure codes are accepted
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from case_engine.action_executor import (
    ActionExecutionError,
    PermanentExecutionError,
    RetryableExecutionError,
    RollbackError,
    ExecutionContext,
    ExecutionResult,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _future(seconds: int = 60) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)


def _past(seconds: int = 60) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)


def _context(**overrides) -> ExecutionContext:
    defaults = dict(
        case_id="case-1",
        ticket_id="ticket-1",
        client="acme",
        action_id="action-1",
        action_type="reset_otp",
        action_namespace="identity",
        trace_id=str(uuid.uuid4()),
        request_id="action-1",
        deadline=_future(900),
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


# ── ExecutionContext ───────────────────────────────────────────────────────────


class TestExecutionContext:
    def test_is_frozen(self) -> None:
        ctx = _context()
        with pytest.raises((AttributeError, TypeError)):
            ctx.case_id = "other"  # type: ignore[misc]

    def test_is_past_deadline_future(self) -> None:
        ctx = _context(deadline=_future(300))
        assert ctx.is_past_deadline() is False

    def test_is_past_deadline_past(self) -> None:
        ctx = _context(deadline=_past(1))
        assert ctx.is_past_deadline() is True

    def test_remaining_ms_positive_when_future(self) -> None:
        ctx = _context(deadline=_future(300))
        assert ctx.remaining_ms() > 0

    def test_remaining_ms_zero_when_past(self) -> None:
        ctx = _context(deadline=_past(1))
        assert ctx.remaining_ms() == 0

    def test_remaining_ms_never_negative(self) -> None:
        ctx = _context(deadline=_past(3600))
        assert ctx.remaining_ms() >= 0

    def test_request_id_is_stable(self) -> None:
        ctx = _context(action_id="action-abc", request_id="action-abc")
        assert ctx.request_id == ctx.action_id

    def test_trace_id_is_valid_uuid(self) -> None:
        trace_id = str(uuid.uuid4())
        ctx = _context(trace_id=trace_id)
        uuid.UUID(ctx.trace_id)

    def test_all_fields_accessible(self) -> None:
        ctx = _context()
        assert ctx.case_id == "case-1"
        assert ctx.ticket_id == "ticket-1"
        assert ctx.client == "acme"
        assert ctx.action_id == "action-1"
        assert ctx.action_type == "reset_otp"
        assert ctx.action_namespace == "identity"


# ── ExecutionResult ────────────────────────────────────────────────────────────


class TestExecutionResult:
    def test_success_result_minimal(self) -> None:
        r = ExecutionResult(success=True)
        assert r.success is True
        assert r.latency_ms == 0
        assert r.provider_reference is None
        assert r.payload == {}
        assert r.error_code is None
        assert r.error_message is None
        assert r.retryable is False

    def test_failure_result_with_error_fields(self) -> None:
        r = ExecutionResult(
            success=False,
            error_code="TIMEOUT",
            error_message="Provider timed out",
            retryable=True,
        )
        assert r.success is False
        assert r.error_code == "TIMEOUT"
        assert r.error_message == "Provider timed out"
        assert r.retryable is True

    def test_is_frozen(self) -> None:
        r = ExecutionResult(success=True)
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_negative_latency_raises(self) -> None:
        with pytest.raises(ValueError, match="latency_ms"):
            ExecutionResult(success=True, latency_ms=-1)

    def test_success_with_error_code_raises(self) -> None:
        with pytest.raises(ValueError, match="error_code"):
            ExecutionResult(success=True, error_code="SOME_CODE")

    def test_success_with_error_message_raises(self) -> None:
        with pytest.raises(ValueError, match="error_message"):
            ExecutionResult(success=True, error_message="Something failed")

    def test_success_with_zero_latency_is_valid(self) -> None:
        r = ExecutionResult(success=True, latency_ms=0)
        assert r.latency_ms == 0

    def test_success_with_payload(self) -> None:
        r = ExecutionResult(success=True, payload={"ref": "TKT-123"}, latency_ms=42)
        assert r.payload == {"ref": "TKT-123"}
        assert r.latency_ms == 42

    def test_success_with_provider_reference(self) -> None:
        r = ExecutionResult(success=True, provider_reference="FD-99999")
        assert r.provider_reference == "FD-99999"

    def test_failure_with_both_error_fields(self) -> None:
        r = ExecutionResult(
            success=False,
            error_code="PERMANENT_ERROR",
            error_message="Account not found",
            latency_ms=10,
        )
        assert r.error_code == "PERMANENT_ERROR"
        assert r.error_message == "Account not found"

    def test_payload_default_is_empty_dict(self) -> None:
        r1 = ExecutionResult(success=True)
        r2 = ExecutionResult(success=True)
        r1.payload["key"] = "value"  # type: ignore[index]
        assert r2.payload == {}


# ── Exception hierarchy ────────────────────────────────────────────────────────


class TestExceptionHierarchy:
    def test_action_execution_error_is_runtime_error(self) -> None:
        exc = ActionExecutionError("test")
        assert isinstance(exc, RuntimeError)

    def test_action_execution_error_default_code(self) -> None:
        exc = ActionExecutionError("test")
        assert exc.failure_code == "EXECUTION_ERROR"

    def test_action_execution_error_custom_code(self) -> None:
        exc = ActionExecutionError("test", failure_code="MY_CODE")
        assert exc.failure_code == "MY_CODE"

    def test_retryable_is_subclass_of_action_execution_error(self) -> None:
        exc = RetryableExecutionError("timeout")
        assert isinstance(exc, ActionExecutionError)

    def test_retryable_default_code(self) -> None:
        exc = RetryableExecutionError("timeout")
        assert exc.failure_code == "RETRYABLE_ERROR"

    def test_retryable_custom_code(self) -> None:
        exc = RetryableExecutionError("rate limited", failure_code="RATE_LIMIT")
        assert exc.failure_code == "RATE_LIMIT"

    def test_permanent_is_subclass_of_action_execution_error(self) -> None:
        exc = PermanentExecutionError("not found")
        assert isinstance(exc, ActionExecutionError)

    def test_permanent_default_code(self) -> None:
        exc = PermanentExecutionError("not found")
        assert exc.failure_code == "PERMANENT_ERROR"

    def test_permanent_custom_code(self) -> None:
        exc = PermanentExecutionError("auth failed", failure_code="AUTH_FAILURE")
        assert exc.failure_code == "AUTH_FAILURE"

    def test_rollback_error_is_subclass_of_action_execution_error(self) -> None:
        exc = RollbackError("compensation failed")
        assert isinstance(exc, ActionExecutionError)

    def test_rollback_error_default_code(self) -> None:
        exc = RollbackError("compensation failed")
        assert exc.failure_code == "ROLLBACK_ERROR"

    def test_rollback_error_custom_code(self) -> None:
        exc = RollbackError("provider rejected", failure_code="PROVIDER_REJECTED")
        assert exc.failure_code == "PROVIDER_REJECTED"

    def test_retryable_and_permanent_are_distinct(self) -> None:
        retryable = RetryableExecutionError("transient")
        permanent = PermanentExecutionError("permanent")
        assert not isinstance(retryable, PermanentExecutionError)
        assert not isinstance(permanent, RetryableExecutionError)

    def test_retryable_is_catchable_as_action_execution_error(self) -> None:
        raised = False
        try:
            raise RetryableExecutionError("timeout")
        except ActionExecutionError:
            raised = True
        assert raised

    def test_permanent_is_catchable_as_action_execution_error(self) -> None:
        raised = False
        try:
            raise PermanentExecutionError("not found")
        except ActionExecutionError:
            raised = True
        assert raised

    def test_rollback_is_catchable_as_action_execution_error(self) -> None:
        raised = False
        try:
            raise RollbackError("failed")
        except ActionExecutionError:
            raised = True
        assert raised

    def test_message_is_preserved(self) -> None:
        exc = RetryableExecutionError("connection refused")
        assert "connection refused" in str(exc)
