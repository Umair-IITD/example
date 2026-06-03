"""
case_engine/provider_models.py

Sprint 2.3: Provider layer data models.

These models are the typed contracts between the Executor layer and the
Provider layer. They replace the raw dict[str, Any] that would otherwise
leak between the two layers.

Immutability
────────────
All models are frozen dataclasses. dict fields (payload, metadata, result)
are shallowly immutable — the dict reference cannot be reassigned, but dict
contents are not deep-frozen. Callers must not mutate these fields after
construction. This matches the pattern established by ExecutionResult.payload
in Sprint 2.2.

Naming conventions for ProviderRequest.operation
─────────────────────────────────────────────────
Use snake_case names that match the ActionExecutor.action_type:
  "reset_otp"       — matches IdentityResetOtpExecutor.action_type
  "freeze_account"  — matches AccountsFreezeAccountExecutor.action_type
  "undo_reset_otp"  — rollback operation

This convention ensures operation names are meaningful in provider logs
without leaking business logic into provider implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ProviderCapability(str, Enum):
    """
    Declares what a Provider can do.

    Used by ProviderRouter to validate that the resolved provider supports
    the required operation before executing.

    Providers declare their capabilities via capabilities() → frozenset[ProviderCapability].
    Executors specify required_capability when calling ProviderRouter.route().

    EXECUTE      — provider can execute forward operations
    ROLLBACK     — provider can execute compensation/undo operations
    IDEMPOTENT   — provider guarantees idempotency when given the same request_id
    HEALTH_CHECK — provider can self-report health status
    """
    EXECUTE      = "execute"
    ROLLBACK     = "rollback"
    IDEMPOTENT   = "idempotent"
    HEALTH_CHECK = "health_check"


@dataclass(frozen=True)
class ProviderRequest:
    """
    Immutable carrier for a single provider operation.

    Constructed by an executor and passed to ProviderRouter.route(), which
    forwards it to Provider.execute().

    Idempotency contract
    ────────────────────
    request_id is stable across retries (= action_id from ExecutionContext).
    Providers MUST use request_id as their idempotency key to prevent
    duplicate operations on retry.

    trace_id is fresh per attempt (new UUID each call). Use it for
    distributed tracing spans — never as an idempotency key.

    Timeout contract
    ────────────────
    timeout_seconds is derived from ExecutionContext.remaining_ms() at the
    time the executor constructs the request. Providers MUST respect this
    deadline and raise ProviderTimeoutError if exceeded.
    """

    request_id:       str              # stable across retries — provider idempotency key
    trace_id:         str              # fresh per attempt — distributed tracing
    action_id:        str
    case_id:          str
    ticket_id:        str
    client:           str
    operation:        str              # provider operation name (e.g. "reset_otp")
    payload:          dict[str, Any]   # sanitized action payload (no PII)
    metadata:         dict[str, Any]   = field(default_factory=dict)
    timeout_seconds:  float            = 30.0
    created_at:       datetime         = field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be > 0, got {self.timeout_seconds}"
            )


@dataclass(frozen=True)
class ProviderResponse:
    """
    Immutable carrier for a provider operation result.

    success=True means the provider confirmed the operation was applied.
    success=False means the provider reported a business-logic failure
    (e.g. account not found, target already in desired state). Both cases
    assume the provider was successfully contacted.

    Network-level failures are raised as ProviderError subclasses rather
    than returned as ProviderResponse(success=False).

    provider_request_id is the provider's own reference for the operation
    (e.g. a Freshdesk ticket ID, a Zendesk incident reference). Use it
    as the ExecutionResult.provider_reference for audit correlation.
    """

    request_id:           str               # echoes ProviderRequest.request_id
    provider_request_id:  str | None        # provider's own reference (may be None)
    success:              bool
    status_code:          int | None        = None
    result:               dict[str, Any]    = field(default_factory=dict)
    metadata:             dict[str, Any]    = field(default_factory=dict)
    executed_at:          datetime          = field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )


@dataclass(frozen=True)
class ProviderHealth:
    """
    Structured health report returned by Provider.health_check().

    Providers MUST return this — never raise from health_check().
    The latency_ms measures the health check call itself, enabling
    detection of provider degradation before full unavailability.

    The message field carries human-readable detail about any failure,
    suitable for operator alerting and logging.
    """

    provider_name:    str
    provider_version: str
    is_healthy:       bool
    latency_ms:       int
    checked_at:       datetime
    message:          str | None = None

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError(
                f"latency_ms must be >= 0, got {self.latency_ms}"
            )


@dataclass(frozen=True)
class ProviderMetadata:
    """
    Static metadata about a registered Provider.

    Used for discovery, capability matching, observability, and operator
    dashboards. Returned by Provider.metadata() and ProviderRegistry.

    capabilities is a frozenset to prevent accidental mutation and to
    support set operations (e.g. registry.providers_with_capability(cap)).
    """

    provider_name:    str
    provider_version: str
    capabilities:     frozenset[ProviderCapability]
    description:      str = ""
