"""
tests/test_sprint22_runtime.py

Sprint 2.2 — ActionRuntime integration tests.

Uses:
  FakeRepository   — in-memory ActionRepository (no Supabase)
  SpyExecutor      — configurable ActionExecutor that records calls
  Real ActionGateway wired to FakeRepository
  Real ActionRuntime wired to the above

Covers:
  execute_action:
    - Not found / wrong state → failure result (action state unchanged)
    - Expired action → failure result
    - Executor not registered → failure result
    - Unhealthy executor → failure result (no state change)
    - Health check exception → treated as unhealthy
    - Successful execution → EXECUTED state, success result
    - RetryableExecutionError with retries remaining → re-APPROVED
    - RetryableExecutionError at max attempts → FAILED
    - PermanentExecutionError → FAILED (no retry regardless of budget)
    - Unknown exception → treated as retryable

  execute_rollback:
    - Original not found → failure result
    - Original not ROLLING_BACK → failure result
    - No rollback_action_id → failure result
    - Compensation not APPROVED → failure result
    - Successful rollback → compensation EXECUTED, original ROLLED_BACK
    - Rollback executor raises → compensation FAILED, original ROLLBACK_FAILED

  _build_context:
    - All fields populated correctly
    - trace_id is a fresh UUID
    - request_id equals action_id
    - deadline is approximately now + EXECUTION_TIMEOUT_DEFAULT

  build_action_runtime:
    - Returns ActionRuntime
    - Accepts custom registry
    - Creates empty registry if none provided
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from case_engine.action_executor import (
    ActionExecutor,
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RetryableExecutionError,
    RollbackError,
)
from case_engine.action_gateway import ActionGateway
from case_engine.action_models import ActionRequest
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import (
    EXECUTION_TIMEOUT_DEFAULT,
    ActionRuntime,
    build_action_runtime,
)
from case_engine.action_state import ActionRiskLevel, ActionState
from case_engine.executor_registry import ActionExecutorRegistry


# ══════════════════════════════════════════════════════════════════════════════
# Test Infrastructure
# ══════════════════════════════════════════════════════════════════════════════


class FakeRepository(ActionRepository):
    """In-memory ActionRepository for testing. No Supabase required."""

    def __init__(self) -> None:
        super().__init__(supabase_client=None)
        self._store: dict[str, ActionRequest] = {}
        self._transitions: list[Any] = []

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._store.get(action_id)

    def insert_action(self, action: ActionRequest) -> ActionRequest:
        self._store[action.action_id] = action
        return action

    def update_action(self, action: ActionRequest) -> bool:
        self._store[action.action_id] = action
        return True

    def record_transition(self, record: Any) -> bool:
        self._transitions.append(record)
        return True

    def append_transition(self, record: Any) -> bool:
        return self.record_transition(record)

    def put(self, action: ActionRequest) -> None:
        """Directly insert an action in any state (bypasses gateway validation)."""
        self._store[action.action_id] = action


class SpyExecutor(ActionExecutor):
    """
    Configurable executor for testing.

    mode:
      "success"           — execute() returns success
      "retryable"         — execute() raises RetryableExecutionError
      "permanent"         — execute() raises PermanentExecutionError
      "exception"         — execute() raises plain RuntimeError
      "rollback_success"  — rollback() returns success
      "rollback_failure"  — rollback() raises RollbackError
      "unhealthy"         — health_check() returns False
      "health_exception"  — health_check() raises RuntimeError
    """

    def __init__(
        self,
        namespace: str = "identity",
        action_type: str = "reset_otp",
        mode: str = "success",
    ) -> None:
        self._namespace  = namespace
        self._type       = action_type
        self.mode        = mode
        self.execute_calls: list[tuple[ExecutionContext, dict]] = []
        self.rollback_calls: list[tuple[ExecutionContext, dict]] = []
        self.health_check_count = 0

    @property
    def action_namespace(self) -> str:
        return self._namespace

    @property
    def action_type(self) -> str:
        return self._type

    def health_check(self) -> bool:
        self.health_check_count += 1
        if self.mode == "unhealthy":
            return False
        if self.mode == "health_exception":
            raise RuntimeError("provider unreachable")
        return True

    def execute(self, context: ExecutionContext, payload: dict) -> ExecutionResult:
        self.execute_calls.append((context, payload))
        if self.mode == "retryable":
            raise RetryableExecutionError("transient error", failure_code="TIMEOUT")
        if self.mode == "permanent":
            raise PermanentExecutionError("not found", failure_code="NOT_FOUND")
        if self.mode == "exception":
            raise RuntimeError("unexpected crash")
        return ExecutionResult(success=True, provider_reference="FD-12345", latency_ms=50)

    def rollback(self, context: ExecutionContext, payload: dict) -> ExecutionResult:
        self.rollback_calls.append((context, payload))
        if self.mode == "rollback_failure":
            raise RollbackError("compensation failed", failure_code="ROLLBACK_REJECTED")
        return ExecutionResult(success=True, provider_reference="FD-ROLLBACK-1", latency_ms=30)


def _make_approved_action(
    namespace: str = "identity",
    action_type: str = "reset_otp",
    max_attempts: int = 3,
    expires_at: datetime | None = None,
    rollback_action_id: str | None = None,
    rollback_params: dict | None = None,
) -> ActionRequest:
    """Build an ActionRequest already in APPROVED state."""
    return ActionRequest(
        case_id="case-001",
        ticket_id="ticket-001",
        client="acme",
        action_type=action_type,
        action_namespace=namespace,
        risk_level=ActionRiskLevel.SAFE,
        current_state=ActionState.APPROVED,
        proposed_by="ai_agent",
        action_payload={"account_id": "ACC-123"},
        max_attempts=max_attempts,
        execution_attempt=0,
        idempotency_key=str(uuid.uuid4()),
        expires_at=expires_at,
        rollback_action_id=rollback_action_id,
        rollback_params=rollback_params,
        approval_required=False,
        approver="auto_approval",
        approved_at=datetime.now(tz=timezone.utc),
    )


def _make_runtime(
    executor: ActionExecutor | None = None,
    namespace: str = "identity",
    action_type: str = "reset_otp",
    executor_mode: str = "success",
) -> tuple[ActionRuntime, FakeRepository, SpyExecutor]:
    """Build a wired (runtime, repo, executor) triple for tests."""
    repo     = FakeRepository()
    gateway  = ActionGateway(repository=repo)
    registry = ActionExecutorRegistry()
    spy      = executor or SpyExecutor(namespace, action_type, executor_mode)
    if isinstance(spy, SpyExecutor):
        registry.register_executor(spy)
    runtime  = ActionRuntime(gateway=gateway, repository=repo, registry=registry)
    return runtime, repo, spy  # type: ignore[return-value]


# ══════════════════════════════════════════════════════════════════════════════
# execute_action — Guard Rails (action not yet claimed)
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteActionGuardRails:
    def test_action_not_found_returns_failure(self) -> None:
        runtime, _, _ = _make_runtime()
        result = runtime.execute_action("nonexistent-id", executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "ACTION_NOT_FOUND_OR_NOT_APPROVED"

    def test_action_in_wrong_state_returns_failure(self) -> None:
        runtime, repo, _ = _make_runtime()
        action = _make_approved_action()
        action.current_state = ActionState.AWAITING_APPROVAL
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "ACTION_NOT_FOUND_OR_NOT_APPROVED"

    def test_action_in_wrong_state_not_mutated(self) -> None:
        runtime, repo, _ = _make_runtime()
        action = _make_approved_action()
        action.current_state = ActionState.AWAITING_APPROVAL
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        assert repo.get_action(action.action_id).current_state == ActionState.AWAITING_APPROVAL

    def test_expired_action_returns_failure(self) -> None:
        runtime, repo, _ = _make_runtime()
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        action = _make_approved_action(expires_at=past)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "ACTION_EXPIRED"

    def test_expired_action_not_claimed(self) -> None:
        runtime, repo, _ = _make_runtime()
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        action = _make_approved_action(expires_at=past)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        assert repo.get_action(action.action_id).current_state == ActionState.APPROVED

    def test_no_expires_at_not_considered_expired(self) -> None:
        runtime, repo, spy = _make_runtime(executor_mode="success")
        action = _make_approved_action(expires_at=None)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is True

    def test_executor_not_registered_returns_failure(self) -> None:
        repo    = FakeRepository()
        gateway = ActionGateway(repository=repo)
        # Empty registry — no executor registered
        runtime = ActionRuntime(gateway=gateway, repository=repo, registry=ActionExecutorRegistry())
        action  = _make_approved_action()
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "EXECUTOR_NOT_REGISTERED"

    def test_executor_not_registered_action_not_mutated(self) -> None:
        repo    = FakeRepository()
        gateway = ActionGateway(repository=repo)
        runtime = ActionRuntime(gateway=gateway, repository=repo, registry=ActionExecutorRegistry())
        action  = _make_approved_action()
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        assert repo.get_action(action.action_id).current_state == ActionState.APPROVED

    def test_unhealthy_executor_returns_failure(self) -> None:
        runtime, repo, spy = _make_runtime(executor_mode="unhealthy")
        action = _make_approved_action()
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "EXECUTOR_UNHEALTHY"
        assert result.retryable is True

    def test_unhealthy_executor_action_not_claimed(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="unhealthy")
        action = _make_approved_action()
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        assert repo.get_action(action.action_id).current_state == ActionState.APPROVED

    def test_health_check_exception_treated_as_unhealthy(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="health_exception")
        action = _make_approved_action()
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "EXECUTOR_UNHEALTHY"


# ══════════════════════════════════════════════════════════════════════════════
# execute_action — Successful Execution
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteActionSuccess:
    def test_successful_result(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="success")
        action = _make_approved_action()
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is True

    def test_action_transitions_to_executed(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="success")
        action = _make_approved_action()
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.current_state == ActionState.EXECUTED

    def test_executor_receives_action_payload(self) -> None:
        runtime, repo, spy = _make_runtime(executor_mode="success")
        action = _make_approved_action()
        action.action_payload = {"account_id": "ACC-999"}
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        _, payload = spy.execute_calls[0]
        assert payload == {"account_id": "ACC-999"}

    def test_executor_receives_correct_context(self) -> None:
        runtime, repo, spy = _make_runtime(executor_mode="success")
        action = _make_approved_action()
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        ctx, _ = spy.execute_calls[0]
        assert ctx.action_id == action.action_id
        assert ctx.case_id   == action.case_id
        assert ctx.client    == action.client
        assert ctx.request_id == action.action_id

    def test_trace_id_is_valid_uuid(self) -> None:
        runtime, repo, spy = _make_runtime(executor_mode="success")
        action = _make_approved_action()
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        ctx, _ = spy.execute_calls[0]
        uuid.UUID(ctx.trace_id)

    def test_health_check_called_before_execute(self) -> None:
        runtime, repo, spy = _make_runtime(executor_mode="success")
        action = _make_approved_action()
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        assert spy.health_check_count == 1
        assert len(spy.execute_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# execute_action — Retryable Failures
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteActionRetryableFailure:
    def test_result_is_failure(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="retryable")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False

    def test_result_is_retryable(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="retryable")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.retryable is True

    def test_error_code_from_executor(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="retryable")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.error_code == "TIMEOUT"

    def test_action_re_approved_when_retries_remain(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="retryable")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.current_state == ActionState.APPROVED

    def test_action_stays_failed_at_max_attempts(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="retryable")
        action = _make_approved_action(max_attempts=1)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.current_state == ActionState.FAILED

    def test_failure_code_persisted(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="retryable")
        action = _make_approved_action(max_attempts=1)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.failure_code == "TIMEOUT"

    def test_unexpected_exception_treated_as_retryable(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="exception")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.retryable is True
        assert result.error_code == "UNEXPECTED_ERROR"

    def test_unexpected_exception_re_approves_when_retries_remain(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="exception")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.current_state == ActionState.APPROVED


# ══════════════════════════════════════════════════════════════════════════════
# execute_action — Permanent Failures
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteActionPermanentFailure:
    def test_result_is_failure(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="permanent")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.success is False

    def test_result_is_not_retryable(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="permanent")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.retryable is False

    def test_action_stays_failed_regardless_of_budget(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="permanent")
        action = _make_approved_action(max_attempts=5)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.current_state == ActionState.FAILED

    def test_error_code_from_executor(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="permanent")
        action = _make_approved_action(max_attempts=3)
        repo.put(action)
        result = runtime.execute_action(action.action_id, executor_id="worker-1")
        assert result.error_code == "NOT_FOUND"

    def test_failure_code_persisted(self) -> None:
        runtime, repo, _ = _make_runtime(executor_mode="permanent")
        action = _make_approved_action(max_attempts=5)
        repo.put(action)
        runtime.execute_action(action.action_id, executor_id="worker-1")
        stored = repo.get_action(action.action_id)
        assert stored.failure_code == "NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# execute_rollback — Guard Rails
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteRollbackGuardRails:
    def test_original_not_found(self) -> None:
        runtime, _, _ = _make_runtime()
        result = runtime.execute_rollback("nonexistent", executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "ORIGINAL_ACTION_NOT_FOUND"

    def test_original_not_rolling_back(self) -> None:
        runtime, repo, _ = _make_runtime()
        action = _make_approved_action()
        repo.put(action)
        result = runtime.execute_rollback(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "ORIGINAL_NOT_ROLLING_BACK"

    def test_no_rollback_action_id(self) -> None:
        runtime, repo, _ = _make_runtime()
        action = _make_approved_action()
        action.current_state = ActionState.ROLLING_BACK
        action.rollback_action_id = None
        repo.put(action)
        result = runtime.execute_rollback(action.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "NO_COMPENSATION_LINKED"

    def test_compensation_not_approved(self) -> None:
        runtime, repo, _ = _make_runtime()
        comp_id = str(uuid.uuid4())
        original = _make_approved_action()
        original.current_state = ActionState.ROLLING_BACK
        original.rollback_action_id = comp_id
        repo.put(original)
        # Compensation not in repo → not found
        result = runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "COMPENSATION_NOT_APPROVED"

    def test_compensation_in_wrong_state(self) -> None:
        runtime, repo, _ = _make_runtime()
        comp = _make_approved_action()
        comp.current_state = ActionState.EXECUTED
        repo.put(comp)
        original = _make_approved_action()
        original.current_state = ActionState.ROLLING_BACK
        original.rollback_action_id = comp.action_id
        repo.put(original)
        result = runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert result.success is False
        assert result.error_code == "COMPENSATION_NOT_APPROVED"


# ══════════════════════════════════════════════════════════════════════════════
# execute_rollback — Successful Rollback
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteRollbackSuccess:
    def _setup(
        self,
        rollback_params: dict | None = None,
    ) -> tuple[ActionRuntime, FakeRepository, SpyExecutor, ActionRequest, ActionRequest]:
        spy     = SpyExecutor("identity", "undo_reset_otp", mode="rollback_success")
        repo    = FakeRepository()
        gateway = ActionGateway(repository=repo)
        registry = ActionExecutorRegistry()
        registry.register_executor(spy)
        runtime = ActionRuntime(gateway=gateway, repository=repo, registry=registry)

        comp = _make_approved_action(namespace="identity", action_type="undo_reset_otp")
        repo.put(comp)

        original = _make_approved_action()
        original.current_state = ActionState.ROLLING_BACK
        original.rollback_action_id = comp.action_id
        original.rollback_params = rollback_params or {"account_id": "ACC-123"}
        repo.put(original)

        return runtime, repo, spy, original, comp

    def test_result_is_success(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        result = runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert result.success is True

    def test_compensation_transitions_to_executed(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert repo.get_action(comp.action_id).current_state == ActionState.EXECUTED

    def test_original_transitions_to_rolled_back(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert repo.get_action(original.action_id).current_state == ActionState.ROLLED_BACK

    def test_rollback_params_passed_to_executor(self) -> None:
        runtime, repo, spy, original, comp = self._setup(rollback_params={"target": "user-99"})
        runtime.execute_rollback(original.action_id, executor_id="worker-1")
        _, payload = spy.rollback_calls[0]
        assert payload == {"target": "user-99"}

    def test_original_is_rolled_back_flag(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        runtime.execute_rollback(original.action_id, executor_id="worker-1")
        stored = repo.get_action(original.action_id)
        assert stored.is_rolled_back is True


# ══════════════════════════════════════════════════════════════════════════════
# execute_rollback — Failed Rollback
# ══════════════════════════════════════════════════════════════════════════════


class TestExecuteRollbackFailure:
    def _setup(self) -> tuple[ActionRuntime, FakeRepository, SpyExecutor, ActionRequest, ActionRequest]:
        spy     = SpyExecutor("identity", "undo_reset_otp", mode="rollback_failure")
        repo    = FakeRepository()
        gateway = ActionGateway(repository=repo)
        registry = ActionExecutorRegistry()
        registry.register_executor(spy)
        runtime = ActionRuntime(gateway=gateway, repository=repo, registry=registry)

        comp = _make_approved_action(namespace="identity", action_type="undo_reset_otp")
        repo.put(comp)

        original = _make_approved_action()
        original.current_state = ActionState.ROLLING_BACK
        original.rollback_action_id = comp.action_id
        original.rollback_params = {"account_id": "ACC-123"}
        repo.put(original)

        return runtime, repo, spy, original, comp

    def test_result_is_failure(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        result = runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert result.success is False

    def test_result_is_not_retryable(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        result = runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert result.retryable is False

    def test_compensation_transitions_to_failed(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert repo.get_action(comp.action_id).current_state == ActionState.FAILED

    def test_original_transitions_to_rollback_failed(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert repo.get_action(original.action_id).current_state == ActionState.ROLLBACK_FAILED

    def test_error_code_from_executor(self) -> None:
        runtime, repo, spy, original, comp = self._setup()
        result = runtime.execute_rollback(original.action_id, executor_id="worker-1")
        assert result.error_code == "ROLLBACK_REJECTED"


# ══════════════════════════════════════════════════════════════════════════════
# _build_context
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildContext:
    def _runtime(self) -> ActionRuntime:
        repo    = FakeRepository()
        gateway = ActionGateway(repository=repo)
        return ActionRuntime(
            gateway=gateway,
            repository=repo,
            registry=ActionExecutorRegistry(),
        )

    def _action(self) -> ActionRequest:
        return _make_approved_action()

    def test_case_id(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx = rt._build_context(action)
        assert ctx.case_id == action.case_id

    def test_ticket_id(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx = rt._build_context(action)
        assert ctx.ticket_id == action.ticket_id

    def test_client(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx = rt._build_context(action)
        assert ctx.client == action.client

    def test_action_id(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx = rt._build_context(action)
        assert ctx.action_id == action.action_id

    def test_request_id_equals_action_id(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx = rt._build_context(action)
        assert ctx.request_id == action.action_id

    def test_trace_id_is_valid_uuid(self) -> None:
        rt = self._runtime()
        ctx = rt._build_context(self._action())
        uuid.UUID(ctx.trace_id)

    def test_trace_id_is_fresh_each_call(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx1 = rt._build_context(action)
        ctx2 = rt._build_context(action)
        assert ctx1.trace_id != ctx2.trace_id

    def test_deadline_approximately_15min_from_now(self) -> None:
        rt = self._runtime()
        before = datetime.now(tz=timezone.utc)
        ctx    = rt._build_context(self._action())
        after  = datetime.now(tz=timezone.utc)
        expected_min = before + EXECUTION_TIMEOUT_DEFAULT
        expected_max = after  + EXECUTION_TIMEOUT_DEFAULT
        assert expected_min <= ctx.deadline <= expected_max

    def test_action_namespace_and_type(self) -> None:
        rt = self._runtime()
        action = self._action()
        ctx = rt._build_context(action)
        assert ctx.action_namespace == action.action_namespace
        assert ctx.action_type == action.action_type


# ══════════════════════════════════════════════════════════════════════════════
# build_action_runtime
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildActionRuntime:
    def test_returns_action_runtime_instance(self) -> None:
        rt = build_action_runtime()
        assert isinstance(rt, ActionRuntime)

    def test_accepts_custom_registry(self) -> None:
        registry = ActionExecutorRegistry()
        registry.register_executor(SpyExecutor())
        rt = build_action_runtime(registry=registry)
        assert rt._registry is registry

    def test_creates_empty_registry_when_none(self) -> None:
        rt = build_action_runtime()
        assert rt._registry.registered_count() == 0

    def test_accepts_custom_timeout(self) -> None:
        custom = timedelta(minutes=5)
        rt = build_action_runtime(execution_timeout=custom)
        assert rt._timeout == custom

    def test_offline_mode_no_supabase(self) -> None:
        rt = build_action_runtime(supabase_client=None)
        assert rt is not None
