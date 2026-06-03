"""
executors/_base.py

Sprint 2.5: Internal base class for ProviderRouter-backed executors.

_BaseProviderExecutor is NOT part of the public API.
It exists solely to eliminate 6+ identical catch blocks that each executor
would otherwise duplicate across execute() and rollback().

Responsibilities:
  - Owns the ProviderRouter dependency (injected at construction).
  - Implements health_check() once for all subclasses.
  - Provides _route() — route for forward execution with error translation.
  - Provides _route_for_rollback() — route for compensation with error translation.
  - Provides _build_request() — constructs ProviderRequest from ExecutionContext.
  - Provides _check_deadline() — guard that raises RetryableExecutionError if past deadline.

Error translation contract (A6 from architecture audit):
  UnknownProviderError(KeyError) — NOT a ProviderError — → PermanentExecutionError
  ProviderTransientError         → RetryableExecutionError
  ProviderPermanentError         → PermanentExecutionError
  ProviderError (base)           → RetryableExecutionError (conservative)
  Any error during rollback      → RollbackError

Dependency direction: executors → case_engine (never the reverse).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from case_engine.action_executor import (
    ActionExecutor,
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RetryableExecutionError,
    RollbackError,
)
from case_engine.provider_exceptions import (
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)
from case_engine.provider_models import ProviderRequest, ProviderResponse
from case_engine.provider_registry import UnknownProviderError
from case_engine.provider_router import ProviderRouter

LOGGER = logging.getLogger(__name__)


class _BaseProviderExecutor(ActionExecutor):
    """
    Abstract base for all ProviderRouter-backed executors.

    Subclasses must still declare action_type, action_namespace and implement
    execute() and rollback() — they remain abstract from ActionExecutor.

    Constructor args:
        router:        Shared ProviderRouter. Thread-safe; multiple executors
                       may share a single instance.
        provider_name: Registry key for the target provider (default "freshdesk").
    """

    def __init__(
        self,
        router: ProviderRouter,
        *,
        provider_name: str = "freshdesk",
    ) -> None:
        self._router = router
        self._provider_name = provider_name

    # ── ActionExecutor ABC ─────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """
        Delegate to ProviderRouter.provider_is_healthy().

        Never raises — ProviderRouter.provider_is_healthy() guarantees this.
        Returns False if the provider is unregistered, unhealthy, or raises.
        """
        return self._router.provider_is_healthy(self._provider_name)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _check_deadline(self, context: ExecutionContext) -> None:
        """
        Raise RetryableExecutionError if the execution deadline has passed.

        Call this before making any provider request. The runtime will treat
        the retryable error as a signal to re-schedule the action, allowing
        a fresh worker to pick it up with a new deadline.
        """
        if context.is_past_deadline():
            raise RetryableExecutionError(
                f"{self.action_namespace}/{self.action_type}: execution deadline exceeded",
                failure_code="DEADLINE_EXCEEDED",
            )

    def _build_request(
        self,
        context: ExecutionContext,
        operation: str,
        payload: dict[str, Any],
    ) -> ProviderRequest:
        """
        Build a ProviderRequest from an ExecutionContext.

        Propagates: request_id (stable idempotency key), trace_id (per-attempt
        tracing), action_id, case_id, ticket_id, client, and a derived
        timeout_seconds (from context.remaining_ms, floor 1.0 s).
        """
        remaining_s = context.remaining_ms() / 1000.0
        timeout_s = max(1.0, remaining_s)
        return ProviderRequest(
            request_id=context.request_id,
            trace_id=context.trace_id,
            action_id=context.action_id,
            case_id=context.case_id,
            ticket_id=context.ticket_id,
            client=context.client,
            operation=operation,
            payload=payload,
            timeout_seconds=timeout_s,
        )

    def _route(self, request: ProviderRequest) -> ProviderResponse:
        """
        Route a forward execution request through ProviderRouter.

        Translates all provider-layer exceptions to execution-layer exceptions
        so the ActionRuntime only ever sees ActionExecutionError subclasses.

        Translation:
          UnknownProviderError  → PermanentExecutionError (PROVIDER_NOT_REGISTERED)
          ProviderTransientError → RetryableExecutionError (error_code preserved)
          ProviderPermanentError → PermanentExecutionError (error_code preserved)
          ProviderError (base)   → RetryableExecutionError (conservative)
        """
        try:
            return self._router.route(self._provider_name, request)
        except UnknownProviderError as exc:
            raise PermanentExecutionError(
                f"Provider {self._provider_name!r} is not registered: {exc}",
                failure_code="PROVIDER_NOT_REGISTERED",
            ) from exc
        except ProviderTransientError as exc:
            raise RetryableExecutionError(str(exc), failure_code=exc.error_code) from exc
        except ProviderPermanentError as exc:
            raise PermanentExecutionError(str(exc), failure_code=exc.error_code) from exc
        except ProviderError as exc:
            raise RetryableExecutionError(str(exc), failure_code=exc.error_code) from exc

    def _route_for_rollback(self, request: ProviderRequest) -> ProviderResponse:
        """
        Route a compensation request through ProviderRouter.

        Translates ALL errors to RollbackError so the runtime records the
        compensation as permanently failed and escalates to a human operator.
        Rollback is a last-resort path; retrying a broken rollback is unsafe.
        """
        try:
            return self._router.route(self._provider_name, request)
        except UnknownProviderError as exc:
            raise RollbackError(
                f"Provider {self._provider_name!r} not registered during rollback: {exc}",
                failure_code="ROLLBACK_PROVIDER_ERROR",
            ) from exc
        except ProviderError as exc:
            raise RollbackError(
                f"Provider error during rollback: {exc}",
                failure_code="ROLLBACK_PROVIDER_ERROR",
            ) from exc
