"""
case_engine/provider_exceptions.py

Sprint 2.3: Provider exception hierarchy.

Hierarchy
─────────
ProviderError (base)
├── ProviderTransientError          retryable=True
│   ├── ProviderUnavailableError    provider is down / health check failed
│   ├── ProviderTimeoutError        operation exceeded timeout_seconds
│   └── ProviderRateLimitError      provider rate limit hit
└── ProviderPermanentError          retryable=False
    ├── ProviderAuthenticationError credential invalid / expired
    ├── ProviderAuthorizationError  insufficient permissions
    ├── ProviderValidationError     payload or request is invalid
    │   └── ProviderCapabilityError provider does not support the operation
    └── ProviderExecutionError      non-recoverable provider execution failure

Executor translation contract
──────────────────────────────
Executors MUST translate ProviderError to ActionExecutionError:

    try:
        response = router.route(provider_name, request)
    except ProviderTransientError as exc:
        raise RetryableExecutionError(str(exc), failure_code=exc.error_code) from exc
    except ProviderPermanentError as exc:
        raise PermanentExecutionError(str(exc), failure_code=exc.error_code) from exc

This translation keeps the runtime completely decoupled from provider errors.
The runtime only ever sees ActionExecutionError subclasses.
"""
from __future__ import annotations


class ProviderError(RuntimeError):
    """
    Base class for all provider-layer errors.

    Attributes:
        error_code    — machine-readable code, propagated to ActionRequest.failure_code
        provider_name — which provider raised this (None if raised before resolution)

    Retry semantics are declared via the retryable property. Subclasses
    override this; the base class defaults to False (conservative).
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_ERROR",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code    = error_code
        self.provider_name = provider_name

    @property
    def retryable(self) -> bool:
        """Conservative default — subclasses override."""
        return False


# ── Transient errors (retryable=True) ─────────────────────────────────────────


class ProviderTransientError(ProviderError):
    """
    Recoverable failure — safe to retry after a delay.

    Executors translate this to RetryableExecutionError.
    Includes: network timeouts, rate limits, temporary unavailability.
    """

    @property
    def retryable(self) -> bool:
        return True


class ProviderUnavailableError(ProviderTransientError):
    """
    Provider is unreachable or not responding to health checks.

    Raised by ProviderRouter when:
      - health_check() returns is_healthy=False
      - health_check() raises any exception

    The executor should schedule a retry after a backoff period.
    The action remains in APPROVED state (not FAILED) until max_attempts.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_UNAVAILABLE",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


class ProviderTimeoutError(ProviderTransientError):
    """
    Provider call exceeded ProviderRequest.timeout_seconds.

    Providers MUST raise this (not block indefinitely) when the timeout
    elapses. The executor treats this as a retryable failure.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_TIMEOUT",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


class ProviderRateLimitError(ProviderTransientError):
    """
    Provider rejected the request due to rate limiting.

    Executors should implement exponential backoff before retrying.
    The retry delay hint may be available in the exception message.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_RATE_LIMIT",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


# ── Permanent errors (retryable=False) ────────────────────────────────────────


class ProviderPermanentError(ProviderError):
    """
    Non-recoverable failure — do not retry.

    Executors translate this to PermanentExecutionError.
    Includes: auth failures, payload validation failures, business rule violations.
    """

    @property
    def retryable(self) -> bool:
        return False


class ProviderAuthenticationError(ProviderPermanentError):
    """
    Provider rejected the request because credentials are invalid or expired.

    Requires operator action (credential rotation) before any retry.
    The action should be moved to FAILED state immediately.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_AUTH_FAILED",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


class ProviderAuthorizationError(ProviderPermanentError):
    """
    Provider rejected the request because credentials lack required permissions.

    The API key is valid but has insufficient scope for this operation.
    Requires operator action (permission grant) before any retry.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_UNAUTHORIZED",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


class ProviderValidationError(ProviderPermanentError):
    """
    Provider rejected the request because the payload is structurally invalid.

    Retrying with the same payload would produce the same error.
    Requires code changes or payload correction.

    Also raised by ProviderRouter for capability mismatches (see ProviderCapabilityError).
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_VALIDATION_FAILED",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


class ProviderCapabilityError(ProviderValidationError):
    """
    Provider does not support the requested capability or operation.

    Raised by ProviderRouter.route() when the resolved provider's
    capabilities() set does not contain the required_capability.

    This is a routing/configuration error — the wrong provider was registered
    for this executor, or the executor is routing to the wrong provider name.
    Fix by registering a provider that declares the required capability.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_CAPABILITY_UNSUPPORTED",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)


class ProviderExecutionError(ProviderPermanentError):
    """
    Provider attempted the operation but encountered a non-recoverable failure.

    Use for business-logic failures where the provider itself is available
    but the requested operation cannot be completed:
      - Target resource not found (account ID invalid)
      - Operation already completed (OTP already reset)
      - Business rule violation (account locked by compliance)

    These represent the provider's definitive "cannot do this" response.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "PROVIDER_EXECUTION_FAILED",
        provider_name: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, provider_name=provider_name)
