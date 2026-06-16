"""
case_engine/action_runtime.py

Sprint 2.2: ActionRuntime — provider-agnostic execution orchestrator.

Responsibilities
────────────────
1. Load an APPROVED action from the repository.
2. Validate its state and deadline.
3. Resolve the correct executor from the registry.
4. Check executor health before claiming the action.
5. Build an ExecutionContext and claim the action (begin_execution).
6. Call executor.execute() and measure wall-clock latency.
7. Persist the outcome via gateway (record_success / record_failure).
8. Orchestrate rollback for ROLLING_BACK actions.
9. Enforce retry semantics based on executor error type.

What ActionRuntime is NOT
──────────────────────────
- It is not a task queue worker (no Celery, no Temporal activity).
- It is not an HTTP client.
- It does not know about Freshdesk or any other provider.
- It does not schedule work — it executes one action per call.
  The caller (worker loop, Temporal activity) is responsible for scheduling.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from audit.models import AuditEvent, AuditEventType
from case_engine.action_executor import (
    ActionExecutor,
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RetryableExecutionError,
)
from case_engine.action_gateway import ActionGateway, ActionGatewayError, build_action_gateway
from case_engine.action_models import ActionRequest
from case_engine.action_repository import ActionRepository
from case_engine.action_state import ActionState
from case_engine.executor_registry import ActionExecutorRegistry, UnknownExecutorError

LOGGER = logging.getLogger(__name__)

EXECUTION_TIMEOUT_DEFAULT = timedelta(minutes=15)


class ActionRuntime:
    """
    Orchestrates execution of a single APPROVED action end-to-end.

    Usage
    ─────
    Build via the factory at startup, then call from your worker loop:

        runtime = build_action_runtime(supabase_client=client, registry=registry)
        result  = runtime.execute_action(action_id, executor_id="worker-1")
        result  = runtime.execute_rollback(original_id, executor_id="worker-1")

    Thread safety
    ─────────────
    ActionRuntime is stateless — all state is in the gateway/repository.
    Multiple workers can share a single runtime instance.

    Design invariant
    ────────────────
    The runtime NEVER mutates action state without first calling a gateway method.
    All state transitions flow through ActionGateway so the audit trail is complete.
    """

    def __init__(
        self,
        gateway: ActionGateway,
        repository: ActionRepository,
        registry: ActionExecutorRegistry,
        execution_timeout: timedelta = EXECUTION_TIMEOUT_DEFAULT,
        audit_service: Any = None,
        metrics_service: Any = None,
    ) -> None:
        self._gateway = gateway
        self._repo    = repository
        self._registry = registry
        self._timeout  = execution_timeout
        self._audit    = audit_service
        self._metrics  = metrics_service

    # ── Forward execution ──────────────────────────────────────────────────────

    def execute_action(
        self,
        action_id: str,
        *,
        executor_id: str,
    ) -> ExecutionResult:
        """
        Execute an APPROVED action end-to-end.

        Steps:
          1.  Load action — must be APPROVED.
          2.  Check expires_at — return failure if expired (action not claimed).
          3.  Resolve executor from registry.
          4.  health_check() — return failure if unhealthy (action not claimed).
          5.  begin_execution() — transition APPROVED → EXECUTING (action claimed).
          6.  Build ExecutionContext with fresh trace_id and deadline.
          7.  Call executor.execute(), measure wall-clock latency.
          8.  On success: record_success(), return ExecutionResult.
          9.  On RetryableExecutionError: record_failure(permanent=False).
          10. On PermanentExecutionError: record_failure(permanent=True).
          11. On any other exception: treat as retryable, record and return failure.

        Steps 1–4 abort without touching action state, allowing another worker
        to pick up the action later. Steps 5+ have already claimed the action.

        Returns:
            ExecutionResult with success=True on provider confirmation.
            ExecutionResult with success=False on any failure — the gateway
            has already been updated; callers need not call record_failure().
        """
        action = self._load_approved(action_id)
        if action is None:
            LOGGER.warning(
                "action_runtime.execute_action: action_id=%s not found or not APPROVED",
                action_id,
            )
            return ExecutionResult(
                success=False,
                error_code="ACTION_NOT_FOUND_OR_NOT_APPROVED",
                error_message=f"Action {action_id!r} not found or not in APPROVED state.",
            )

        if self._is_expired(action):
            LOGGER.warning(
                "action_runtime.execute_action: action_id=%s expired at %s — skipping",
                action_id, action.expires_at,
            )
            return ExecutionResult(
                success=False,
                error_code="ACTION_EXPIRED",
                error_message=f"Action {action_id!r} has passed its expires_at deadline.",
            )

        try:
            executor = self._registry.get_executor(
                action.action_namespace, action.action_type
            )
        except UnknownExecutorError as exc:
            LOGGER.error(
                "action_runtime.execute_action: no executor action_id=%s ns=%s type=%s",
                action_id, action.action_namespace, action.action_type,
            )
            return ExecutionResult(
                success=False,
                error_code="EXECUTOR_NOT_REGISTERED",
                error_message=str(exc),
            )

        if not self._health_check(executor, action):
            LOGGER.warning(
                "action_runtime.execute_action: unhealthy executor action_id=%s executor=%s",
                action_id, type(executor).__name__,
            )
            return ExecutionResult(
                success=False,
                error_code="EXECUTOR_UNHEALTHY",
                error_message=(
                    f"Health check failed for {action.action_namespace}/{action.action_type}."
                ),
                retryable=True,
            )

        try:
            action = self._gateway.begin_execution(action, executor_id=executor_id)
        except ActionGatewayError as exc:
            LOGGER.error(
                "action_runtime.execute_action: begin_execution failed action_id=%s error=%s",
                action_id, exc,
            )
            return ExecutionResult(
                success=False,
                error_code="BEGIN_EXECUTION_FAILED",
                error_message=str(exc),
                retryable=True,
            )

        context = self._build_context(action)
        self._emit(AuditEvent(
            action_id=action.action_id,
            case_id=action.case_id,
            client=action.client,
            event_type=AuditEventType.ACTION_EXECUTION_STARTED,
            actor=f"executor:{executor_id}",
            metadata={
                "action_type": action.action_type,
                "action_namespace": action.action_namespace,
                "attempt": action.execution_attempt,
            },
        ))
        return self._run_executor(executor, action, context)

    # ── Rollback execution ─────────────────────────────────────────────────────

    def execute_rollback(
        self,
        original_action_id: str,
        *,
        executor_id: str,
    ) -> ExecutionResult:
        """
        Execute compensation for a ROLLING_BACK action.

        Preconditions:
          - The original action is in ROLLING_BACK state.
          - original_action.rollback_action_id points to a compensation action.
          - The compensation action is in APPROVED state.

        Steps:
          1.  Load original action — must be ROLLING_BACK.
          2.  Load compensation action via rollback_action_id — must be APPROVED.
          3.  Resolve executor for compensation action's namespace/type.
          4.  health_check() on executor.
          5.  begin_execution() on the compensation action.
          6.  Build ExecutionContext for the compensation action.
          7.  Call executor.rollback() with original action's rollback_params.
          8.  On success: record_success(compensation) + record_rollback_success(original).
          9.  On any failure: record_failure(compensation, permanent=True)
              + record_rollback_failure(original, reason=...).

        Returns:
            ExecutionResult from executor.rollback() on success.
            ExecutionResult(success=False, ...) on failure.
        """
        original = self._repo.get_action(original_action_id)
        if original is None:
            return ExecutionResult(
                success=False,
                error_code="ORIGINAL_ACTION_NOT_FOUND",
                error_message=f"Original action {original_action_id!r} not found.",
            )
        if original.current_state != ActionState.ROLLING_BACK:
            return ExecutionResult(
                success=False,
                error_code="ORIGINAL_NOT_ROLLING_BACK",
                error_message=(
                    f"Expected ROLLING_BACK, got {original.current_state.value} "
                    f"for action {original_action_id!r}."
                ),
            )
        if not original.rollback_action_id:
            return ExecutionResult(
                success=False,
                error_code="NO_COMPENSATION_LINKED",
                error_message=f"Action {original_action_id!r} has no rollback_action_id.",
            )

        compensation = self._load_approved(original.rollback_action_id)
        if compensation is None:
            return ExecutionResult(
                success=False,
                error_code="COMPENSATION_NOT_APPROVED",
                error_message=(
                    f"Compensation action {original.rollback_action_id!r} not found "
                    "or not in APPROVED state."
                ),
            )

        try:
            executor = self._registry.get_executor(
                compensation.action_namespace, compensation.action_type
            )
        except UnknownExecutorError as exc:
            return ExecutionResult(
                success=False,
                error_code="EXECUTOR_NOT_REGISTERED",
                error_message=str(exc),
            )

        if not self._health_check(executor, compensation):
            return ExecutionResult(
                success=False,
                error_code="EXECUTOR_UNHEALTHY",
                error_message=(
                    f"Health check failed for rollback executor "
                    f"{compensation.action_namespace}/{compensation.action_type}."
                ),
                retryable=True,
            )

        try:
            compensation = self._gateway.begin_execution(compensation, executor_id=executor_id)
        except ActionGatewayError as exc:
            return ExecutionResult(
                success=False,
                error_code="BEGIN_EXECUTION_FAILED",
                error_message=str(exc),
                retryable=True,
            )

        context = self._build_context(compensation)
        rollback_payload = original.rollback_params or {}
        return self._run_rollback(executor, original, compensation, context, rollback_payload)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _emit(self, event: "AuditEvent") -> None:
        """Fire-and-forget audit emission. Never raises."""
        if self._audit is not None:
            self._audit.emit(event)

    def _record_metric(self, method_name: str, **kwargs: Any) -> None:
        """Fire-and-forget metrics recording. Never raises."""
        if self._metrics is not None:
            try:
                getattr(self._metrics, method_name)(**kwargs)
            except Exception:
                pass

    def _build_context(self, action: ActionRequest) -> ExecutionContext:
        """Build an ExecutionContext for a single execution attempt."""
        return ExecutionContext(
            case_id=action.case_id,
            ticket_id=action.ticket_id,
            client=action.client,
            action_id=action.action_id,
            action_type=action.action_type,
            action_namespace=action.action_namespace,
            trace_id=str(uuid.uuid4()),        # fresh per attempt — use for spans
            request_id=action.action_id,       # stable across retries — use for idempotency
            deadline=datetime.now(tz=timezone.utc) + self._timeout,
        )

    def _load_approved(self, action_id: str) -> ActionRequest | None:
        """Load an action only if it is in APPROVED state; return None otherwise."""
        action = self._repo.get_action(action_id)
        if action is None or action.current_state != ActionState.APPROVED:
            return None
        return action

    def _is_expired(self, action: ActionRequest) -> bool:
        """Return True if the action's expires_at has passed."""
        if action.expires_at is None:
            return False
        return datetime.now(tz=timezone.utc) >= action.expires_at

    def _health_check(self, executor: ActionExecutor, action: ActionRequest) -> bool:
        """Call executor.health_check(), catching any exception as False."""
        try:
            result = executor.health_check()
            if not result:
                LOGGER.warning(
                    "action_runtime.health_check: unhealthy "
                    "action_id=%s executor=%s provider_name=%s health_check_result=False",
                    action.action_id,
                    type(executor).__name__,
                    getattr(executor, "_provider_name", "unknown"),
                )
            return result
        except Exception as exc:
            LOGGER.warning(
                "action_runtime.health_check: exception executor=%s action_id=%s error=%s",
                type(executor).__name__, action.action_id, exc,
            )
            return False

    def _run_executor(
        self,
        executor: ActionExecutor,
        action: ActionRequest,
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Invoke executor.execute(), record the outcome, return ExecutionResult."""
        start = time.monotonic()
        try:
            result = executor.execute(context, action.action_payload or {})
            latency_ms = int((time.monotonic() - start) * 1000)
            if result.latency_ms == 0 and latency_ms > 0:
                result = ExecutionResult(
                    success=result.success,
                    provider_reference=result.provider_reference,
                    latency_ms=latency_ms,
                    payload=result.payload,
                )
            self._gateway.record_success(
                action,
                result={
                    "provider_reference": result.provider_reference,
                    **result.payload,
                },
            )
            LOGGER.info(
                "action_runtime: succeeded action_id=%s attempt=%d latency_ms=%d",
                action.action_id, action.execution_attempt, latency_ms,
            )
            self._emit(AuditEvent(
                action_id=action.action_id,
                case_id=action.case_id,
                client=action.client,
                event_type=AuditEventType.ACTION_EXECUTED,
                actor=f"executor:{action.executor_id or 'unknown'}",
                metadata={
                    "action_type": action.action_type,
                    "attempt": action.execution_attempt,
                    "latency_ms": latency_ms,
                },
            ))
            self._record_metric("record_action_executed", latency_ms=float(latency_ms))
            return result

        except PermanentExecutionError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            LOGGER.error(
                "action_runtime: permanent failure action_id=%s code=%s error=%s",
                action.action_id, exc.failure_code, exc,
            )
            self._gateway.record_failure(
                action,
                failure_code=exc.failure_code,
                reason=str(exc),
                permanent=True,
            )
            self._emit(AuditEvent(
                action_id=action.action_id,
                case_id=action.case_id,
                client=action.client,
                event_type=AuditEventType.ACTION_FAILED,
                actor=f"executor:{action.executor_id or 'unknown'}",
                metadata={
                    "action_type": action.action_type,
                    "failure_code": exc.failure_code,
                    "permanent": True,
                    "attempt": action.execution_attempt,
                },
            ))
            self._record_metric("record_action_failed")
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                error_code=exc.failure_code,
                error_message=str(exc),
                retryable=False,
            )

        except RetryableExecutionError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            LOGGER.warning(
                "action_runtime: retryable failure action_id=%s code=%s error=%s",
                action.action_id, exc.failure_code, exc,
            )
            self._gateway.record_failure(
                action,
                failure_code=exc.failure_code,
                reason=str(exc),
                permanent=False,
            )
            self._emit(AuditEvent(
                action_id=action.action_id,
                case_id=action.case_id,
                client=action.client,
                event_type=AuditEventType.ACTION_FAILED,
                actor=f"executor:{action.executor_id or 'unknown'}",
                metadata={
                    "action_type": action.action_type,
                    "failure_code": exc.failure_code,
                    "permanent": False,
                    "attempt": action.execution_attempt,
                },
            ))
            self._record_metric("record_action_failed")
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                error_code=exc.failure_code,
                error_message=str(exc),
                retryable=True,
            )

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            LOGGER.exception(
                "action_runtime: unexpected exception action_id=%s error=%s",
                action.action_id, exc,
            )
            self._gateway.record_failure(
                action,
                failure_code="UNEXPECTED_ERROR",
                reason=str(exc),
                permanent=False,
            )
            self._emit(AuditEvent(
                action_id=action.action_id,
                case_id=action.case_id,
                client=action.client,
                event_type=AuditEventType.ACTION_FAILED,
                actor=f"executor:{action.executor_id or 'unknown'}",
                metadata={
                    "action_type": action.action_type,
                    "failure_code": "UNEXPECTED_ERROR",
                    "permanent": False,
                    "attempt": action.execution_attempt,
                },
            ))
            self._record_metric("record_action_failed")
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                error_code="UNEXPECTED_ERROR",
                error_message=str(exc),
                retryable=True,
            )

    def _run_rollback(
        self,
        executor: ActionExecutor,
        original: ActionRequest,
        compensation: ActionRequest,
        context: ExecutionContext,
        rollback_payload: dict[str, Any],
    ) -> ExecutionResult:
        """Invoke executor.rollback(), record the outcome on both actions."""
        start = time.monotonic()
        try:
            result = executor.rollback(context, rollback_payload)
            latency_ms = int((time.monotonic() - start) * 1000)
            if result.latency_ms == 0 and latency_ms > 0:
                result = ExecutionResult(
                    success=result.success,
                    provider_reference=result.provider_reference,
                    latency_ms=latency_ms,
                    payload=result.payload,
                )
            self._gateway.record_success(
                compensation,
                result={
                    "provider_reference": result.provider_reference,
                    **result.payload,
                },
            )
            self._gateway.record_rollback_success(original)
            LOGGER.info(
                "action_runtime: rollback succeeded original=%s compensation=%s latency_ms=%d",
                original.action_id, compensation.action_id, latency_ms,
            )
            self._emit(AuditEvent(
                action_id=original.action_id,
                case_id=original.case_id,
                client=original.client,
                event_type=AuditEventType.ACTION_ROLLED_BACK,
                actor=f"executor:{compensation.executor_id or 'unknown'}",
                metadata={
                    "action_type": original.action_type,
                    "compensation_action_id": compensation.action_id,
                    "latency_ms": latency_ms,
                },
            ))
            self._record_metric("record_action_rolled_back", latency_ms=float(latency_ms))
            return result

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            failure_code = getattr(exc, "failure_code", "ROLLBACK_ERROR")
            reason = str(exc)
            LOGGER.error(
                "action_runtime: rollback failed original=%s compensation=%s code=%s error=%s",
                original.action_id, compensation.action_id, failure_code, exc,
            )
            self._gateway.record_failure(
                compensation,
                failure_code=failure_code,
                reason=reason,
                permanent=True,
            )
            self._gateway.record_rollback_failure(original, reason=reason)
            self._emit(AuditEvent(
                action_id=original.action_id,
                case_id=original.case_id,
                client=original.client,
                event_type=AuditEventType.ACTION_ROLLBACK_FAILED,
                actor=f"executor:{compensation.executor_id or 'unknown'}",
                metadata={
                    "action_type": original.action_type,
                    "failure_code": failure_code,
                    "reason": reason,
                    "compensation_action_id": compensation.action_id,
                },
            ))
            self._record_metric("record_action_rollback_failed")
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                error_code=failure_code,
                error_message=reason,
                retryable=False,
            )


# ── Factory ────────────────────────────────────────────────────────────────────

def build_action_runtime(
    supabase_client: Any = None,
    registry: ActionExecutorRegistry | None = None,
    execution_timeout: timedelta = EXECUTION_TIMEOUT_DEFAULT,
) -> ActionRuntime:
    """
    Factory function for constructing a fully-wired ActionRuntime.

    supabase_client=None → offline/test mode (in-memory only).
    registry=None → constructs an empty registry; callers must register executors.
    """
    repo    = ActionRepository(supabase_client=supabase_client)
    gateway = ActionGateway(repository=repo)
    if registry is None:
        registry = ActionExecutorRegistry()
    return ActionRuntime(
        gateway=gateway,
        repository=repo,
        registry=registry,
        execution_timeout=execution_timeout,
    )
