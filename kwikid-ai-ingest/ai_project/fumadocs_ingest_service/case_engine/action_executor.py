"""
case_engine/action_executor.py

Sprint 2.2: Provider-agnostic execution abstractions.

This module defines the contracts that all provider implementations must satisfy.
It contains no Freshdesk logic, no HTTP clients, no queue integrations.

Components
──────────
  ActionExecutionError      — base exception for the execution layer
  RetryableExecutionError   — transient failure; safe to retry
  PermanentExecutionError   — non-recoverable; do not retry
  RollbackError             — compensation execution failed

  ExecutionContext           — immutable per-execution metadata carrier
  ExecutionResult           — structured execution outcome

  ActionExecutor            — ABC all provider executors must implement

Naming convention for implementations:
  <Namespace><ActionType>Executor
  e.g.  IdentityResetOtpExecutor
        AccountsFreezeAccountExecutor
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════


class ActionExecutionError(RuntimeError):
    """
    Base class for all errors raised by ActionExecutor implementations.

    Carries a machine-readable failure_code that the runtime writes into
    ActionRequest.failure_code for operator alerting and SLA reporting.
    """

    def __init__(self, message: str, *, failure_code: str = "EXECUTION_ERROR") -> None:
        super().__init__(message)
        self.failure_code = failure_code


class RetryableExecutionError(ActionExecutionError):
    """
    Transient execution failure — safe to retry.

    Raise this when:
      - Network timeout reaching the provider
      - Provider returned HTTP 429 (rate limit) or 503 (unavailable)
      - Distributed lock could not be acquired
      - Any failure where re-running the action has a reasonable chance of success

    The ActionRuntime will call ActionGateway.record_failure() and — if
    execution_attempt < max_attempts — re-approve the action for the next worker.
    """

    def __init__(self, message: str, *, failure_code: str = "RETRYABLE_ERROR") -> None:
        super().__init__(message, failure_code=failure_code)


class PermanentExecutionError(ActionExecutionError):
    """
    Non-recoverable execution failure — do not retry.

    Raise this when:
      - Invalid action_payload (missing required fields, wrong types)
      - Authentication failure (API key revoked, insufficient scope)
      - Target resource not found (ticket deleted, user doesn't exist)
      - Business rule violation (account already closed)
      - Any failure where retrying would produce the same result

    The ActionRuntime will call ActionGateway.record_failure(permanent=True),
    leaving the action in FAILED state regardless of remaining retry budget.
    """

    def __init__(self, message: str, *, failure_code: str = "PERMANENT_ERROR") -> None:
        super().__init__(message, failure_code=failure_code)


class RollbackError(ActionExecutionError):
    """
    Compensation execution failed.

    Raise from ActionExecutor.rollback() when the provider cannot undo
    a previously executed action.

    The ActionRuntime will call ActionGateway.record_rollback_failure() on the
    original action, leaving it in ROLLBACK_FAILED (terminal — human escalation
    required). Do not raise RetryableExecutionError from rollback() — the retry
    semantics for compensation are handled by the runtime, not the executor.
    """

    def __init__(self, message: str, *, failure_code: str = "ROLLBACK_ERROR") -> None:
        super().__init__(message, failure_code=failure_code)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExecutionContext:
    """
    Immutable per-execution metadata carrier.

    Constructed by ActionRuntime and passed to every executor call.
    Executors use it for:
      - Distributed tracing   (trace_id)
      - Provider deduplication (request_id — stable across retries)
      - Deadline enforcement   (deadline)
      - Audit correlation      (action_id, case_id)

    ExecutionContext is frozen: executors must never mutate it.
    A fresh trace_id is generated for each execution attempt; request_id
    (= action_id) remains stable, enabling provider-side idempotency keys.
    """

    # Case / ticket correlation
    case_id:          str
    ticket_id:        str
    client:           str

    # Action identity (executor routing and audit)
    action_id:        str
    action_type:      str
    action_namespace: str

    # Distributed tracing
    trace_id:         str      # fresh UUID per attempt — use for spans/logs
    request_id:       str      # action_id — stable across retries — use for idempotency keys

    # Deadline — wall-clock cutoff for this execution attempt
    deadline:         datetime

    def is_past_deadline(self) -> bool:
        """Return True if the deadline has already passed."""
        return datetime.now(tz=timezone.utc) > self.deadline

    def remaining_ms(self) -> int:
        """Return milliseconds remaining until deadline (0 if already past)."""
        delta = self.deadline - datetime.now(tz=timezone.utc)
        return max(0, int(delta.total_seconds() * 1000))


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION RESULT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExecutionResult:
    """
    Structured execution outcome returned by ActionExecutor.execute() or rollback().

    Always produced by the executor — never synthesised inside the runtime
    except when the executor itself could not be called (registry miss, health
    check failure). In those cases the runtime builds an ExecutionResult with
    success=False to surface the failure uniformly to callers.

    Validation (enforced in __post_init__):
      - latency_ms must be >= 0
      - A successful result must not carry error_code or error_message
    """

    success:            bool
    provider_reference: str | None         = None
    latency_ms:         int                = 0
    payload:            dict[str, Any]     = field(default_factory=dict)
    error_code:         str | None         = None
    error_message:      str | None         = None
    retryable:          bool               = False

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {self.latency_ms}")
        if self.success and self.error_code is not None:
            raise ValueError(
                "Successful ExecutionResult must not carry error_code. "
                f"Got error_code={self.error_code!r}"
            )
        if self.success and self.error_message is not None:
            raise ValueError(
                "Successful ExecutionResult must not carry error_message. "
                f"Got error_message={self.error_message!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ACTION EXECUTOR (ABC)
# ═══════════════════════════════════════════════════════════════════════════════


class ActionExecutor(ABC):
    """
    Abstract base class all provider-specific executors must implement.

    Contract
    ────────
    1.  Declare action_type and action_namespace — the registry uses these as
        the routing key (namespace, action_type) → executor.

    2.  Implement execute() for the forward operation.

    3.  Implement rollback() for compensation (REVERSIBLE actions only).
        Implementations for IRREVERSIBLE namespaces may raise NotImplementedError.

    4.  Implement health_check() so the runtime can verify provider reachability
        before claiming an APPROVED action.

    Executor Invariants
    ───────────────────
    - Executors are STATELESS. Per-request state travels via ExecutionContext
      and the payload argument. Never store request-scoped data on self.

    - execute() MUST be idempotent. Use context.request_id as the provider-side
      idempotency key. Calling execute() twice with the same context must not
      double-apply the operation.

    - Executors MUST NOT access ActionRepository, ActionGateway, or the DB.
      All persistence is ActionRuntime's responsibility.

    - Executors MUST NOT log PII. The payload passed to them has already been
      sanitized by the gateway layer.

    Error Handling
    ──────────────
    - Raise RetryableExecutionError for transient failures.
    - Raise PermanentExecutionError for non-recoverable failures.
    - Raise RollbackError from rollback() when compensation fails.
    - Any other exception is treated by the runtime as RetryableExecutionError.
    """

    @property
    @abstractmethod
    def action_type(self) -> str:
        """The action_type this executor handles (e.g. 'reset_otp')."""

    @property
    @abstractmethod
    def action_namespace(self) -> str:
        """The namespace this executor belongs to (e.g. 'identity')."""

    @abstractmethod
    def execute(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Perform the action against the target provider.

        Args:
            context: Immutable execution metadata (trace_id, deadline, etc.).
            payload: Sanitized action_payload from the ActionRequest.

        Returns:
            ExecutionResult with success=True on provider confirmation.

        Raises:
            RetryableExecutionError: transient failure — runtime will retry.
            PermanentExecutionError: non-recoverable — runtime will not retry.
        """

    @abstractmethod
    def rollback(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Perform compensation for a previously executed REVERSIBLE action.

        Called by ActionRuntime.execute_rollback() when a ROLLING_BACK
        compensation action is in APPROVED state.

        The payload is the rollback_params from the original ActionRequest
        (captured at proposal time and stored in the compensation action's
        action_payload).

        Args:
            context: Execution metadata for the compensation action.
            payload: rollback_params from the original action.

        Returns:
            ExecutionResult with success=True if compensation confirmed.

        Raises:
            RollbackError: compensation failed — runtime calls record_rollback_failure().
        """

    @abstractmethod
    def health_check(self) -> bool:
        """
        Return True if the provider system is reachable and accepting requests.

        Called by the runtime before claiming an APPROVED action. A False return
        or any exception causes the runtime to abort without changing action state,
        allowing another worker to retry pickup later.

        Must complete quickly (< 5 seconds). Must not raise; return False instead.
        """
