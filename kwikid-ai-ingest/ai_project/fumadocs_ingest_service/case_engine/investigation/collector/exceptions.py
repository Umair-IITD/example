"""
case_engine/investigation/collector/exceptions.py

Sprint 2.42: Exception hierarchy for the Evidence Collector Engine.

All exceptions inherit from CollectorError so callers can catch the
entire hierarchy with a single except clause.

Dependency direction:
  This module imports ONLY from the standard library.
  It is a leaf dependency node — no case_engine imports allowed.
"""
from __future__ import annotations


class CollectorError(Exception):
    """Base class for all Evidence Collector exceptions."""


class CollectorConfigurationError(CollectorError):
    """Raised when the ProviderRegistry is not properly configured."""


class ProviderNotFoundError(CollectorError):
    """Raised when no provider is registered for the requested EvidenceKind."""

    def __init__(self, kind: str, step_id: str = "") -> None:
        self.kind = kind
        self.step_id = step_id
        detail = f" (step={step_id!r})" if step_id else ""
        super().__init__(f"No provider registered for EvidenceKind={kind!r}{detail}")


class ProviderExecutionError(CollectorError):
    """Raised when a provider raises an unexpected exception during execution."""

    def __init__(self, provider_name: str, step_id: str, cause: Exception) -> None:
        self.provider_name = provider_name
        self.step_id = step_id
        self.cause = cause
        super().__init__(
            f"Provider {provider_name!r} failed at step {step_id!r}: {cause}"
        )


class StepPreconditionError(CollectorError):
    """Raised when a step precondition is not satisfied."""

    def __init__(self, step_id: str, precondition_id: str, detail: str = "") -> None:
        self.step_id = step_id
        self.precondition_id = precondition_id
        super().__init__(
            f"Precondition {precondition_id!r} not satisfied for step {step_id!r}: {detail}"
        )


class PlanExecutionError(CollectorError):
    """Raised when plan execution cannot proceed (too many critical step failures)."""

    def __init__(self, plan_id: str, reason: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"Plan {plan_id!r} execution failed: {reason}")


class CollectorTimeoutError(CollectorError):
    """Raised when a step exceeds its allowed execution time."""

    def __init__(self, step_id: str, timeout_seconds: float) -> None:
        self.step_id = step_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Step {step_id!r} exceeded timeout of {timeout_seconds}s"
        )
