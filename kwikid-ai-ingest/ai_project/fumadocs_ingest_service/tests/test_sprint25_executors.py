"""
tests/test_sprint25_executors.py

Sprint 2.5 — Production executor exhaustive tests.

Areas covered:
  1.  Executor metadata (action_type, action_namespace, ABC compliance)
  2.  health_check delegation to ProviderRouter
  3.  AddTicketNoteExecutor — execute success paths
  4.  AddTicketNoteExecutor — execute validation failures
  5.  AddTicketNoteExecutor — provider error translation
  6.  AddTicketNoteExecutor — rollback success
  7.  AddTicketNoteExecutor — rollback failure
  8.  UpdateTicketStatusExecutor — execute success paths
  9.  UpdateTicketStatusExecutor — execute validation failures
  10. UpdateTicketStatusExecutor — provider error translation
  11. UpdateTicketStatusExecutor — rollback success
  12. UpdateTicketStatusExecutor — rollback failure
  13. IdentityResetOtpExecutor — execute success paths
  14. IdentityResetOtpExecutor — execute validation failures
  15. IdentityResetOtpExecutor — provider error translation
  16. IdentityResetOtpExecutor — rollback always fails (non-reversible)
  17. ExecutionContext propagation (request_id, trace_id, client, etc.)
  18. Deadline handling
  19. ProviderRouter integration (real registry + stub provider)
  20. ExecutorRegistry integration
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from case_engine.action_executor import (
    ActionExecutor,
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RetryableExecutionError,
    RollbackError,
)
from case_engine.executor_registry import ActionExecutorRegistry
from case_engine.provider_exceptions import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderError,
    ProviderExecutionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from case_engine.provider_interface import Provider
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)
from case_engine.provider_registry import ProviderRegistry, UnknownProviderError
from case_engine.provider_router import ProviderRouter
from executors import (
    AddTicketNoteExecutor,
    IdentityResetOtpExecutor,
    UpdateTicketStatusExecutor,
)


# ══════════════════════════════════════════════════════════════════════════════
# Test infrastructure
# ══════════════════════════════════════════════════════════════════════════════


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _future(minutes: int = 15) -> datetime:
    return _now() + timedelta(minutes=minutes)


def _past(minutes: int = 1) -> datetime:
    return _now() - timedelta(minutes=minutes)


def _context(
    request_id: str = "req-1",
    trace_id: str = "trace-1",
    action_id: str = "action-1",
    case_id: str = "case-1",
    ticket_id: str = "ticket-42",
    client: str = "acme",
    action_type: str = "add_note",
    action_namespace: str = "ticket",
    deadline: datetime | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        case_id=case_id,
        ticket_id=ticket_id,
        client=client,
        action_id=action_id,
        action_type=action_type,
        action_namespace=action_namespace,
        trace_id=trace_id,
        request_id=request_id,
        deadline=deadline if deadline is not None else _future(),
    )


def _response(
    request_id: str = "req-1",
    provider_request_id: str = "FD-99",
    success: bool = True,
    result: dict[str, Any] | None = None,
    status_code: int = 201,
) -> ProviderResponse:
    return ProviderResponse(
        request_id=request_id,
        provider_request_id=provider_request_id,
        success=success,
        status_code=status_code,
        result=result or {},
    )


def _mock_router(
    *,
    healthy: bool = True,
    route_return: ProviderResponse | None = None,
    route_raises: Exception | None = None,
) -> MagicMock:
    router = MagicMock(spec=ProviderRouter)
    router.provider_is_healthy.return_value = healthy
    if route_raises is not None:
        router.route.side_effect = route_raises
    else:
        router.route.return_value = route_return or _response()
    return router


def _make_note_executor(
    healthy: bool = True,
    route_return: ProviderResponse | None = None,
    route_raises: Exception | None = None,
) -> tuple[AddTicketNoteExecutor, MagicMock]:
    router = _mock_router(
        healthy=healthy,
        route_return=route_return,
        route_raises=route_raises,
    )
    return AddTicketNoteExecutor(router), router


def _make_status_executor(
    healthy: bool = True,
    route_return: ProviderResponse | None = None,
    route_raises: Exception | None = None,
) -> tuple[UpdateTicketStatusExecutor, MagicMock]:
    router = _mock_router(
        healthy=healthy,
        route_return=route_return,
        route_raises=route_raises,
    )
    return UpdateTicketStatusExecutor(router), router


def _make_otp_executor(
    healthy: bool = True,
    route_return: ProviderResponse | None = None,
    route_raises: Exception | None = None,
) -> tuple[IdentityResetOtpExecutor, MagicMock]:
    router = _mock_router(
        healthy=healthy,
        route_return=route_return,
        route_raises=route_raises,
    )
    return IdentityResetOtpExecutor(router), router


# ── Stub Provider for integration tests ───────────────────────────────────────


class _StubProvider(Provider):
    """Minimal in-memory Provider for ProviderRouter integration tests."""

    def __init__(
        self,
        name: str = "freshdesk",
        *,
        healthy: bool = True,
        execute_response: ProviderResponse | None = None,
        execute_raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._healthy = healthy
        self._execute_response = execute_response
        self._execute_raises = execute_raises

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def provider_version(self) -> str:
        return "1.0.0"

    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.EXECUTE, ProviderCapability.HEALTH_CHECK})

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self._name,
            provider_version="1.0.0",
            is_healthy=self._healthy,
            latency_ms=1,
            checked_at=_now(),
            message=None if self._healthy else "stub unhealthy",
        )

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        if self._execute_raises is not None:
            raise self._execute_raises
        if self._execute_response is not None:
            return self._execute_response
        return ProviderResponse(
            request_id=request.request_id,
            provider_request_id="stub-note-1",
            success=True,
            result={"note_id": "stub-note-1"},
        )


def _real_router(provider: _StubProvider) -> ProviderRouter:
    registry = ProviderRegistry()
    registry.register(provider)
    return ProviderRouter(registry)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Executor metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutorMetadata:
    def test_add_note_action_type(self) -> None:
        executor, _ = _make_note_executor()
        assert executor.action_type == "add_note"

    def test_add_note_action_namespace(self) -> None:
        executor, _ = _make_note_executor()
        assert executor.action_namespace == "ticket"

    def test_update_status_action_type(self) -> None:
        executor, _ = _make_status_executor()
        assert executor.action_type == "update_status"

    def test_update_status_action_namespace(self) -> None:
        executor, _ = _make_status_executor()
        assert executor.action_namespace == "ticket"

    def test_otp_action_type(self) -> None:
        executor, _ = _make_otp_executor()
        assert executor.action_type == "reset_otp"

    def test_otp_action_namespace(self) -> None:
        executor, _ = _make_otp_executor()
        assert executor.action_namespace == "identity"

    def test_add_note_is_action_executor(self) -> None:
        executor, _ = _make_note_executor()
        assert isinstance(executor, ActionExecutor)

    def test_update_status_is_action_executor(self) -> None:
        executor, _ = _make_status_executor()
        assert isinstance(executor, ActionExecutor)

    def test_otp_is_action_executor(self) -> None:
        executor, _ = _make_otp_executor()
        assert isinstance(executor, ActionExecutor)

    def test_all_namespaces_distinct(self) -> None:
        note, _ = _make_note_executor()
        status, _ = _make_status_executor()
        otp, _ = _make_otp_executor()
        keys = {(e.action_namespace, e.action_type) for e in [note, status, otp]}
        assert len(keys) == 3  # no duplicates


# ══════════════════════════════════════════════════════════════════════════════
# 2. health_check delegation
# ══════════════════════════════════════════════════════════════════════════════


class TestHealthCheck:
    def test_add_note_healthy(self) -> None:
        executor, router = _make_note_executor(healthy=True)
        assert executor.health_check() is True
        router.provider_is_healthy.assert_called_once_with("freshdesk")

    def test_add_note_unhealthy(self) -> None:
        executor, _ = _make_note_executor(healthy=False)
        assert executor.health_check() is False

    def test_update_status_healthy(self) -> None:
        executor, router = _make_status_executor(healthy=True)
        assert executor.health_check() is True

    def test_otp_healthy(self) -> None:
        executor, _ = _make_otp_executor(healthy=True)
        assert executor.health_check() is True

    def test_health_check_never_raises(self) -> None:
        router = MagicMock(spec=ProviderRouter)
        router.provider_is_healthy.side_effect = RuntimeError("unexpected")
        executor = AddTicketNoteExecutor(router)
        # provider_is_healthy itself should not raise; if the mock raises,
        # the executor just propagates. We test that the executor doesn't
        # add its own exception-swallowing layer (that's router's job).
        with pytest.raises(RuntimeError):
            executor.health_check()


# ══════════════════════════════════════════════════════════════════════════════
# 3. AddTicketNoteExecutor — execute success
# ══════════════════════════════════════════════════════════════════════════════


class TestAddNoteExecuteSuccess:
    def test_returns_execution_result(self) -> None:
        resp = _response(result={"note_id": "42"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.execute(_context(), {"body": "Test note"})
        assert isinstance(result, ExecutionResult)

    def test_success_is_true(self) -> None:
        resp = _response(result={"note_id": "42"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.execute(_context(), {"body": "Test note"})
        assert result.success is True

    def test_provider_reference_from_result(self) -> None:
        resp = _response(result={"note_id": "99"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.execute(_context(), {"body": "Test note"})
        assert result.provider_reference == "99"

    def test_provider_reference_fallback_to_provider_request_id(self) -> None:
        resp = _response(provider_request_id="FD-777", result={})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.execute(_context(), {"body": "Test note"})
        assert result.provider_reference == "FD-777"

    def test_uses_add_note_operation(self) -> None:
        executor, router = _make_note_executor()
        executor.execute(_context(), {"body": "Hello"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.operation == "add_note"

    def test_body_forwarded(self) -> None:
        executor, router = _make_note_executor()
        executor.execute(_context(), {"body": "Custom body text"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["body"] == "Custom body text"

    def test_private_true_by_default(self) -> None:
        executor, router = _make_note_executor()
        executor.execute(_context(), {"body": "Note"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["private"] is True

    def test_private_false_when_specified(self) -> None:
        executor, router = _make_note_executor()
        executor.execute(_context(), {"body": "Note", "private": False})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["private"] is False

    def test_payload_contains_note_id(self) -> None:
        resp = _response(result={"note_id": "55"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.execute(_context(), {"body": "Note"})
        assert result.payload["note_id"] == "55"

    def test_payload_contains_ticket_id(self) -> None:
        executor, _ = _make_note_executor()
        result = executor.execute(_context(ticket_id="TKT-123"), {"body": "Note"})
        assert result.payload["ticket_id"] == "TKT-123"


# ══════════════════════════════════════════════════════════════════════════════
# 4. AddTicketNoteExecutor — validation failures
# ══════════════════════════════════════════════════════════════════════════════


class TestAddNoteValidation:
    def test_missing_body_raises_permanent(self) -> None:
        executor, _ = _make_note_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {})

    def test_empty_body_raises_permanent(self) -> None:
        executor, _ = _make_note_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"body": ""})

    def test_whitespace_body_raises_permanent(self) -> None:
        executor, _ = _make_note_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"body": "   "})

    def test_validation_failure_code(self) -> None:
        executor, _ = _make_note_executor()
        try:
            executor.execute(_context(), {})
        except PermanentExecutionError as exc:
            assert exc.failure_code == "MISSING_REQUIRED_FIELD"
        else:
            pytest.fail("Expected PermanentExecutionError")

    def test_validation_before_provider_call(self) -> None:
        executor, router = _make_note_executor()
        try:
            executor.execute(_context(), {"body": ""})
        except PermanentExecutionError:
            pass
        router.route.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 5. AddTicketNoteExecutor — provider error translation
# ══════════════════════════════════════════════════════════════════════════════


class TestAddNoteErrorTranslation:
    def test_transient_rate_limit_to_retryable(self) -> None:
        exc = ProviderRateLimitError("rate limit")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_transient_timeout_to_retryable(self) -> None:
        exc = ProviderTimeoutError("timeout")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_transient_unavailable_to_retryable(self) -> None:
        exc = ProviderUnavailableError("down")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_permanent_auth_to_permanent(self) -> None:
        exc = ProviderAuthenticationError("auth failed")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_permanent_execution_error_to_permanent(self) -> None:
        exc = ProviderExecutionError("not found")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_base_provider_error_to_retryable(self) -> None:
        exc = ProviderError("unexpected")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_unknown_provider_to_permanent(self) -> None:
        exc = UnknownProviderError("freshdesk")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(PermanentExecutionError) as exc_info:
            executor.execute(_context(), {"body": "Note"})
        assert exc_info.value.failure_code == "PROVIDER_NOT_REGISTERED"

    def test_transient_error_code_preserved(self) -> None:
        exc = ProviderRateLimitError("rate limit", error_code="PROVIDER_RATE_LIMIT")
        executor, _ = _make_note_executor(route_raises=exc)
        try:
            executor.execute(_context(), {"body": "Note"})
        except RetryableExecutionError as e:
            assert e.failure_code == "PROVIDER_RATE_LIMIT"
        else:
            pytest.fail("Expected RetryableExecutionError")

    def test_permanent_error_code_preserved(self) -> None:
        exc = ProviderAuthenticationError("auth", error_code="PROVIDER_AUTH_FAILED")
        executor, _ = _make_note_executor(route_raises=exc)
        try:
            executor.execute(_context(), {"body": "Note"})
        except PermanentExecutionError as e:
            assert e.failure_code == "PROVIDER_AUTH_FAILED"
        else:
            pytest.fail("Expected PermanentExecutionError")


# ══════════════════════════════════════════════════════════════════════════════
# 6. AddTicketNoteExecutor — rollback success
# ══════════════════════════════════════════════════════════════════════════════


class TestAddNoteRollback:
    def test_rollback_returns_execution_result(self) -> None:
        resp = _response(result={"note_id": "comp-1"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.rollback(_context(), {"compensation_note": "Correction"})
        assert isinstance(result, ExecutionResult)

    def test_rollback_success_is_true(self) -> None:
        resp = _response(result={"note_id": "comp-1"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.rollback(_context(), {"compensation_note": "Correction"})
        assert result.success is True

    def test_rollback_uses_compensation_note(self) -> None:
        executor, router = _make_note_executor()
        executor.rollback(_context(), {"compensation_note": "This was an error."})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert "This was an error." in request_arg.payload["body"]

    def test_rollback_default_note_when_absent(self) -> None:
        executor, router = _make_note_executor()
        executor.rollback(_context(), {})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert len(request_arg.payload["body"]) > 0

    def test_rollback_always_private(self) -> None:
        executor, router = _make_note_executor()
        executor.rollback(_context(), {"compensation_note": "Fix"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["private"] is True

    def test_rollback_uses_add_note_operation(self) -> None:
        executor, router = _make_note_executor()
        executor.rollback(_context(), {"compensation_note": "Fix"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.operation == "add_note"

    def test_rollback_provider_reference_from_result(self) -> None:
        resp = _response(result={"note_id": "comp-99"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.rollback(_context(), {})
        assert result.provider_reference == "comp-99"

    def test_rollback_payload_contains_compensation_note_id(self) -> None:
        resp = _response(result={"note_id": "comp-77"})
        executor, _ = _make_note_executor(route_return=resp)
        result = executor.rollback(_context(), {})
        assert result.payload["compensation_note_id"] == "comp-77"


# ══════════════════════════════════════════════════════════════════════════════
# 7. AddTicketNoteExecutor — rollback failure
# ══════════════════════════════════════════════════════════════════════════════


class TestAddNoteRollbackFailure:
    def test_provider_transient_error_raises_rollback_error(self) -> None:
        exc = ProviderRateLimitError("rate limit")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RollbackError):
            executor.rollback(_context(), {})

    def test_provider_permanent_error_raises_rollback_error(self) -> None:
        exc = ProviderAuthenticationError("auth")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RollbackError):
            executor.rollback(_context(), {})

    def test_unknown_provider_raises_rollback_error(self) -> None:
        exc = UnknownProviderError("freshdesk")
        executor, _ = _make_note_executor(route_raises=exc)
        with pytest.raises(RollbackError):
            executor.rollback(_context(), {})

    def test_rollback_error_failure_code(self) -> None:
        exc = ProviderUnavailableError("down")
        executor, _ = _make_note_executor(route_raises=exc)
        try:
            executor.rollback(_context(), {})
        except RollbackError as e:
            assert e.failure_code == "ROLLBACK_PROVIDER_ERROR"
        else:
            pytest.fail("Expected RollbackError")


# ══════════════════════════════════════════════════════════════════════════════
# 8. UpdateTicketStatusExecutor — execute success
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateStatusExecuteSuccess:
    def test_returns_execution_result(self) -> None:
        resp = _response(provider_request_id="ticket-42", status_code=200)
        executor, _ = _make_status_executor(route_return=resp)
        result = executor.execute(_context(), {"status": 4})
        assert isinstance(result, ExecutionResult)

    def test_success_is_true(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.execute(_context(), {"status": 4})
        assert result.success is True

    def test_provider_reference_is_ticket_id(self) -> None:
        resp = _response(provider_request_id="ticket-42", status_code=200)
        executor, _ = _make_status_executor(route_return=resp)
        result = executor.execute(_context(ticket_id="ticket-42"), {"status": 4})
        assert "ticket-42" in (result.provider_reference or "")

    def test_uses_update_ticket_operation(self) -> None:
        executor, router = _make_status_executor()
        executor.execute(_context(), {"status": 3})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.operation == "update_ticket"

    def test_status_forwarded_in_payload(self) -> None:
        executor, router = _make_status_executor()
        executor.execute(_context(), {"status": 3})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["status"] == 3

    def test_result_payload_contains_status(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.execute(_context(), {"status": 5})
        assert result.payload["status"] == 5

    def test_result_payload_contains_ticket_id(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.execute(_context(ticket_id="TKT-999"), {"status": 2})
        assert result.payload["ticket_id"] == "TKT-999"

    def test_custom_status_accepted(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.execute(_context(), {"status": 10})
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
# 9. UpdateTicketStatusExecutor — validation failures
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateStatusValidation:
    def test_missing_status_raises_permanent(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {})

    def test_missing_status_failure_code(self) -> None:
        executor, _ = _make_status_executor()
        try:
            executor.execute(_context(), {})
        except PermanentExecutionError as exc:
            assert exc.failure_code == "MISSING_REQUIRED_FIELD"
        else:
            pytest.fail("Expected PermanentExecutionError")

    def test_string_status_raises_permanent(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"status": "resolved"})

    def test_zero_status_raises_permanent(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"status": 0})

    def test_negative_status_raises_permanent(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"status": -1})

    def test_float_status_raises_permanent(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"status": 3.0})

    def test_validation_before_provider_call(self) -> None:
        executor, router = _make_status_executor()
        try:
            executor.execute(_context(), {"status": -5})
        except PermanentExecutionError:
            pass
        router.route.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 10. UpdateTicketStatusExecutor — provider error translation
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateStatusErrorTranslation:
    def test_transient_error_to_retryable(self) -> None:
        executor, _ = _make_status_executor(route_raises=ProviderTimeoutError("timeout"))
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"status": 4})

    def test_permanent_error_to_permanent(self) -> None:
        executor, _ = _make_status_executor(
            route_raises=ProviderAuthenticationError("auth")
        )
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"status": 4})

    def test_unknown_provider_to_permanent(self) -> None:
        executor, _ = _make_status_executor(
            route_raises=UnknownProviderError("freshdesk")
        )
        with pytest.raises(PermanentExecutionError) as exc_info:
            executor.execute(_context(), {"status": 4})
        assert exc_info.value.failure_code == "PROVIDER_NOT_REGISTERED"

    def test_base_provider_error_to_retryable(self) -> None:
        executor, _ = _make_status_executor(route_raises=ProviderError("base"))
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"status": 4})


# ══════════════════════════════════════════════════════════════════════════════
# 11. UpdateTicketStatusExecutor — rollback success
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateStatusRollback:
    def test_rollback_success_is_true(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.rollback(_context(), {"status": 2})
        assert result.success is True

    def test_rollback_uses_previous_status(self) -> None:
        executor, router = _make_status_executor()
        executor.rollback(_context(), {"status": 2})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["status"] == 2

    def test_rollback_uses_update_ticket_operation(self) -> None:
        executor, router = _make_status_executor()
        executor.rollback(_context(), {"status": 3})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.operation == "update_ticket"

    def test_rollback_payload_contains_restored_status(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.rollback(_context(), {"status": 2})
        assert result.payload["restored_status"] == 2

    def test_rollback_payload_contains_ticket_id(self) -> None:
        executor, _ = _make_status_executor()
        result = executor.rollback(_context(ticket_id="TKT-42"), {"status": 2})
        assert result.payload["ticket_id"] == "TKT-42"


# ══════════════════════════════════════════════════════════════════════════════
# 12. UpdateTicketStatusExecutor — rollback failure
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateStatusRollbackFailure:
    def test_missing_status_raises_rollback_error(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(RollbackError) as exc_info:
            executor.rollback(_context(), {})
        assert exc_info.value.failure_code == "ROLLBACK_MISSING_PARAMS"

    def test_invalid_status_raises_rollback_error(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(RollbackError) as exc_info:
            executor.rollback(_context(), {"status": -1})
        assert exc_info.value.failure_code == "ROLLBACK_INVALID_PARAMS"

    def test_provider_error_raises_rollback_error(self) -> None:
        executor, _ = _make_status_executor(
            route_raises=ProviderUnavailableError("down")
        )
        with pytest.raises(RollbackError):
            executor.rollback(_context(), {"status": 2})

    def test_rollback_provider_error_code(self) -> None:
        executor, _ = _make_status_executor(
            route_raises=ProviderAuthenticationError("auth")
        )
        try:
            executor.rollback(_context(), {"status": 2})
        except RollbackError as e:
            assert e.failure_code == "ROLLBACK_PROVIDER_ERROR"
        else:
            pytest.fail("Expected RollbackError")

    def test_zero_status_rollback_raises(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(RollbackError):
            executor.rollback(_context(), {"status": 0})


# ══════════════════════════════════════════════════════════════════════════════
# 13. IdentityResetOtpExecutor — execute success
# ══════════════════════════════════════════════════════════════════════════════


class TestOtpExecuteSuccess:
    def test_returns_execution_result(self) -> None:
        resp = _response(result={"note_id": "otp-note-1"})
        executor, _ = _make_otp_executor(route_return=resp)
        result = executor.execute(_context(), {"account_id": "ACC-123"})
        assert isinstance(result, ExecutionResult)

    def test_success_is_true(self) -> None:
        executor, _ = _make_otp_executor()
        result = executor.execute(_context(), {"account_id": "ACC-123"})
        assert result.success is True

    def test_uses_add_note_operation(self) -> None:
        executor, router = _make_otp_executor()
        executor.execute(_context(), {"account_id": "ACC-123"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.operation == "add_note"

    def test_note_is_private(self) -> None:
        executor, router = _make_otp_executor()
        executor.execute(_context(), {"account_id": "ACC-123"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert request_arg.payload["private"] is True

    def test_note_contains_account_id(self) -> None:
        executor, router = _make_otp_executor()
        executor.execute(_context(), {"account_id": "ACC-SPECIAL-99"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert "ACC-SPECIAL-99" in request_arg.payload["body"]

    def test_note_contains_action_id(self) -> None:
        executor, router = _make_otp_executor()
        executor.execute(_context(action_id="action-XYZ"), {"account_id": "ACC-1"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert "action-XYZ" in request_arg.payload["body"]

    def test_provider_reference_from_result(self) -> None:
        resp = _response(result={"note_id": "otp-77"})
        executor, _ = _make_otp_executor(route_return=resp)
        result = executor.execute(_context(), {"account_id": "ACC-1"})
        assert result.provider_reference == "otp-77"

    def test_payload_contains_account_id(self) -> None:
        executor, _ = _make_otp_executor()
        result = executor.execute(_context(), {"account_id": "ACC-42"})
        assert result.payload["account_id"] == "ACC-42"

    def test_optional_reason_included(self) -> None:
        executor, router = _make_otp_executor()
        executor.execute(_context(), {"account_id": "ACC-1", "reason": "Customer locked out"})
        request_arg: ProviderRequest = router.route.call_args[0][1]
        assert "Customer locked out" in request_arg.payload["body"]


# ══════════════════════════════════════════════════════════════════════════════
# 14. IdentityResetOtpExecutor — validation failures
# ══════════════════════════════════════════════════════════════════════════════


class TestOtpValidation:
    def test_missing_account_id_raises_permanent(self) -> None:
        executor, _ = _make_otp_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {})

    def test_empty_account_id_raises_permanent(self) -> None:
        executor, _ = _make_otp_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"account_id": ""})

    def test_whitespace_account_id_raises_permanent(self) -> None:
        executor, _ = _make_otp_executor()
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"account_id": "   "})

    def test_validation_failure_code(self) -> None:
        executor, _ = _make_otp_executor()
        try:
            executor.execute(_context(), {})
        except PermanentExecutionError as exc:
            assert exc.failure_code == "MISSING_REQUIRED_FIELD"
        else:
            pytest.fail("Expected PermanentExecutionError")

    def test_validation_before_provider_call(self) -> None:
        executor, router = _make_otp_executor()
        try:
            executor.execute(_context(), {"account_id": ""})
        except PermanentExecutionError:
            pass
        router.route.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 15. IdentityResetOtpExecutor — provider error translation
# ══════════════════════════════════════════════════════════════════════════════


class TestOtpErrorTranslation:
    def test_transient_error_to_retryable(self) -> None:
        executor, _ = _make_otp_executor(route_raises=ProviderRateLimitError("rate"))
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"account_id": "ACC-1"})

    def test_permanent_error_to_permanent(self) -> None:
        executor, _ = _make_otp_executor(
            route_raises=ProviderAuthenticationError("auth")
        )
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"account_id": "ACC-1"})

    def test_unknown_provider_to_permanent(self) -> None:
        executor, _ = _make_otp_executor(route_raises=UnknownProviderError("freshdesk"))
        with pytest.raises(PermanentExecutionError) as exc_info:
            executor.execute(_context(), {"account_id": "ACC-1"})
        assert exc_info.value.failure_code == "PROVIDER_NOT_REGISTERED"


# ══════════════════════════════════════════════════════════════════════════════
# 16. IdentityResetOtpExecutor — rollback not supported
# ══════════════════════════════════════════════════════════════════════════════


class TestOtpRollback:
    def test_rollback_raises_rollback_error(self) -> None:
        executor, _ = _make_otp_executor()
        with pytest.raises(RollbackError):
            executor.rollback(_context(), {})

    def test_rollback_failure_code_not_supported(self) -> None:
        executor, _ = _make_otp_executor()
        try:
            executor.rollback(_context(), {})
        except RollbackError as exc:
            assert exc.failure_code == "ROLLBACK_NOT_SUPPORTED"
        else:
            pytest.fail("Expected RollbackError")

    def test_rollback_does_not_call_router(self) -> None:
        executor, router = _make_otp_executor()
        try:
            executor.rollback(_context(), {})
        except RollbackError:
            pass
        router.route.assert_not_called()

    def test_rollback_error_regardless_of_payload(self) -> None:
        executor, _ = _make_otp_executor()
        for payload in [{}, {"status": 2}, {"anything": True}]:
            with pytest.raises(RollbackError):
                executor.rollback(_context(), payload)


# ══════════════════════════════════════════════════════════════════════════════
# 17. ExecutionContext propagation
# ══════════════════════════════════════════════════════════════════════════════


class TestContextPropagation:
    def _get_request_arg(self, executor, ctx, payload) -> ProviderRequest:
        router = executor._router
        executor.execute(ctx, payload)
        return router.route.call_args[0][1]

    def test_request_id_propagated(self) -> None:
        executor, router = _make_note_executor()
        ctx = _context(request_id="stable-req-id")
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.request_id == "stable-req-id"

    def test_trace_id_propagated(self) -> None:
        executor, router = _make_note_executor()
        ctx = _context(trace_id="trace-XYZ")
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.trace_id == "trace-XYZ"

    def test_action_id_propagated(self) -> None:
        executor, router = _make_note_executor()
        ctx = _context(action_id="action-ABC")
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.action_id == "action-ABC"

    def test_case_id_propagated(self) -> None:
        executor, router = _make_note_executor()
        ctx = _context(case_id="case-DEF")
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.case_id == "case-DEF"

    def test_ticket_id_propagated(self) -> None:
        executor, router = _make_note_executor()
        ctx = _context(ticket_id="ticket-999")
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.ticket_id == "ticket-999"

    def test_client_propagated(self) -> None:
        executor, router = _make_note_executor()
        ctx = _context(client="bigcorp")
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.client == "bigcorp"

    def test_timeout_derived_from_remaining_ms(self) -> None:
        executor, router = _make_note_executor()
        # 5-minute deadline → remaining ≈ 300s → timeout = 300+
        ctx = _context(deadline=_now() + timedelta(minutes=5))
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.timeout_seconds >= 1.0

    def test_timeout_minimum_is_one_second(self) -> None:
        executor, router = _make_note_executor()
        # deadline very close (1ms remaining)
        ctx = _context(deadline=_now() + timedelta(milliseconds=1))
        executor.execute(ctx, {"body": "Note"})
        req: ProviderRequest = router.route.call_args[0][1]
        assert req.timeout_seconds >= 1.0

    def test_request_id_stable_across_calls(self) -> None:
        # Same request_id used every time the same action is executed
        executor, router = _make_note_executor()
        ctx = _context(request_id="idempotency-key")
        executor.execute(ctx, {"body": "Note 1"})
        executor.execute(ctx, {"body": "Note 2"})
        calls = router.route.call_args_list
        assert calls[0][0][1].request_id == calls[1][0][1].request_id == "idempotency-key"


# ══════════════════════════════════════════════════════════════════════════════
# 18. Deadline handling
# ══════════════════════════════════════════════════════════════════════════════


class TestDeadlineHandling:
    def test_expired_context_raises_retryable(self) -> None:
        executor, _ = _make_note_executor()
        expired_ctx = _context(deadline=_past())
        with pytest.raises(RetryableExecutionError) as exc_info:
            executor.execute(expired_ctx, {"body": "Note"})
        assert exc_info.value.failure_code == "DEADLINE_EXCEEDED"

    def test_expired_context_does_not_call_router(self) -> None:
        executor, router = _make_note_executor()
        expired_ctx = _context(deadline=_past())
        try:
            executor.execute(expired_ctx, {"body": "Note"})
        except RetryableExecutionError:
            pass
        router.route.assert_not_called()

    def test_update_status_deadline_raises_retryable(self) -> None:
        executor, _ = _make_status_executor()
        with pytest.raises(RetryableExecutionError) as exc_info:
            executor.execute(_context(deadline=_past()), {"status": 4})
        assert exc_info.value.failure_code == "DEADLINE_EXCEEDED"

    def test_otp_deadline_raises_retryable(self) -> None:
        executor, _ = _make_otp_executor()
        with pytest.raises(RetryableExecutionError) as exc_info:
            executor.execute(_context(deadline=_past()), {"account_id": "ACC-1"})
        assert exc_info.value.failure_code == "DEADLINE_EXCEEDED"

    def test_active_context_proceeds_normally(self) -> None:
        executor, _ = _make_note_executor()
        result = executor.execute(_context(deadline=_future()), {"body": "Note"})
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
# 19. ProviderRouter integration (real registry + stub provider)
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderRouterIntegration:
    def test_add_note_via_real_router(self) -> None:
        provider = _StubProvider(
            execute_response=ProviderResponse(
                request_id="req-1",
                provider_request_id="note-42",
                success=True,
                result={"note_id": "note-42"},
            )
        )
        router = _real_router(provider)
        executor = AddTicketNoteExecutor(router)
        result = executor.execute(_context(), {"body": "Integration test"})
        assert result.success is True
        assert result.provider_reference == "note-42"

    def test_update_status_via_real_router(self) -> None:
        provider = _StubProvider(
            execute_response=ProviderResponse(
                request_id="req-1",
                provider_request_id="ticket-42",
                success=True,
                result={"ticket_id": "ticket-42"},
            )
        )
        router = _real_router(provider)
        executor = UpdateTicketStatusExecutor(router)
        result = executor.execute(_context(), {"status": 4})
        assert result.success is True

    def test_otp_via_real_router(self) -> None:
        provider = _StubProvider(
            execute_response=ProviderResponse(
                request_id="req-1",
                provider_request_id="otp-note-1",
                success=True,
                result={"note_id": "otp-note-1"},
            )
        )
        router = _real_router(provider)
        executor = IdentityResetOtpExecutor(router)
        result = executor.execute(_context(), {"account_id": "ACC-1"})
        assert result.success is True

    def test_unhealthy_provider_health_check_false(self) -> None:
        provider = _StubProvider(healthy=False)
        router = _real_router(provider)
        executor = AddTicketNoteExecutor(router)
        assert executor.health_check() is False

    def test_healthy_provider_health_check_true(self) -> None:
        provider = _StubProvider(healthy=True)
        router = _real_router(provider)
        executor = AddTicketNoteExecutor(router)
        assert executor.health_check() is True

    def test_transient_error_from_stub_provider(self) -> None:
        provider = _StubProvider(execute_raises=ProviderRateLimitError("rate"))
        router = _real_router(provider)
        executor = AddTicketNoteExecutor(router)
        with pytest.raises(RetryableExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_permanent_error_from_stub_provider(self) -> None:
        provider = _StubProvider(execute_raises=ProviderAuthenticationError("auth"))
        router = _real_router(provider)
        executor = AddTicketNoteExecutor(router)
        with pytest.raises(PermanentExecutionError):
            executor.execute(_context(), {"body": "Note"})

    def test_unregistered_provider_raises_permanent(self) -> None:
        registry = ProviderRegistry()  # empty — no provider registered
        router = ProviderRouter(registry)
        executor = AddTicketNoteExecutor(router)
        with pytest.raises(PermanentExecutionError) as exc_info:
            executor.execute(_context(), {"body": "Note"})
        assert exc_info.value.failure_code == "PROVIDER_NOT_REGISTERED"


# ══════════════════════════════════════════════════════════════════════════════
# 20. ExecutorRegistry integration
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutorRegistryIntegration:
    def _make_registry_with_all(self) -> ActionExecutorRegistry:
        router = _mock_router()
        registry = ActionExecutorRegistry()
        registry.register_executor(AddTicketNoteExecutor(router))
        registry.register_executor(UpdateTicketStatusExecutor(router))
        registry.register_executor(IdentityResetOtpExecutor(router))
        return registry

    def test_all_executors_registered(self) -> None:
        registry = self._make_registry_with_all()
        assert registry.registered_count() == 3

    def test_add_note_retrievable(self) -> None:
        registry = self._make_registry_with_all()
        executor = registry.get_executor("ticket", "add_note")
        assert isinstance(executor, AddTicketNoteExecutor)

    def test_update_status_retrievable(self) -> None:
        registry = self._make_registry_with_all()
        executor = registry.get_executor("ticket", "update_status")
        assert isinstance(executor, UpdateTicketStatusExecutor)

    def test_otp_retrievable(self) -> None:
        registry = self._make_registry_with_all()
        executor = registry.get_executor("identity", "reset_otp")
        assert isinstance(executor, IdentityResetOtpExecutor)

    def test_duplicate_registration_raises(self) -> None:
        from case_engine.executor_registry import ExecutorRegistrationError
        router = _mock_router()
        registry = ActionExecutorRegistry()
        registry.register_executor(AddTicketNoteExecutor(router))
        with pytest.raises(ExecutorRegistrationError):
            registry.register_executor(AddTicketNoteExecutor(router))

    def test_all_keys_are_distinct(self) -> None:
        registry = self._make_registry_with_all()
        keys = registry.registered_keys()
        assert len(keys) == len(set(keys))
