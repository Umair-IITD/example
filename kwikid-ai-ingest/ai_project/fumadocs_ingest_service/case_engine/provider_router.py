"""
case_engine/provider_router.py

Sprint 2.3: ProviderRouter — executor → provider dispatch.

Purpose
───────
Executors must never directly instantiate or import providers. Instead,
executors call ProviderRouter.route() and receive a ProviderResponse.

This indirection means:
  - Adding a new provider requires only a registry.register() call.
  - Switching providers requires only router reconfiguration.
  - Executors remain provider-agnostic.
  - The router is the right place for future failover, circuit-breaking,
    and per-client routing (Sprint 2.X+).

Route flow (Sprint 2.3)
───────────────────────
1. Resolve provider from registry (raises UnknownProviderError on miss).
2. Validate required_capability is in provider.capabilities()
   (raises ProviderCapabilityError if not supported).
3. Health check (raises ProviderUnavailableError if is_healthy=False).
4. Delegate to provider.execute() and return the result.

Future extensibility seam
──────────────────────────
The route() signature is designed for multi-provider failover:
  - required_capability allows capability-based routing (find ANY
    healthy provider that has EXECUTE, not just a named one).
  - registry.list_providers() + registry.providers_with_capability()
    support iteration for failover in a future ProviderFailoverRouter.
  - Override or subclass route() to add failover without changing executors.

Exception contract
──────────────────
UnknownProviderError  — provider_name not registered (config/startup error)
ProviderCapabilityError — provider doesn't support required_capability
ProviderUnavailableError — health check failed (provider is down)
Any ProviderError from provider.execute() — passed through to the executor
Unexpected non-ProviderError exceptions from execute() are wrapped as
ProviderError (error_code="PROVIDER_UNEXPECTED_ERROR") so the executor
always receives a typed exception it can translate.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from case_engine.provider_exceptions import (
    ProviderCapabilityError,
    ProviderError,
    ProviderUnavailableError,
)
from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
)
from case_engine.provider_registry import ProviderRegistry, UnknownProviderError

if TYPE_CHECKING:
    from case_engine.provider_interface import Provider

LOGGER = logging.getLogger(__name__)


class ProviderRouter:
    """
    Routes ProviderRequest to the correct Provider instance.

    Executors use the router as their sole entry point to the provider layer.
    The router enforces capability validation and health checks before every
    request, preventing executors from accidentally routing to unhealthy or
    incapable providers.

    Thread safety
    ─────────────
    ProviderRouter is stateless — all state is in the registry.
    Multiple executor workers can share a single router instance.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def route(
        self,
        provider_name: str,
        request: ProviderRequest,
        *,
        required_capability: ProviderCapability = ProviderCapability.EXECUTE,
    ) -> ProviderResponse:
        """
        Resolve, validate, health-check, and execute via the named provider.

        Args:
            provider_name:        Registry key for the target provider.
            request:              The operation request to forward.
            required_capability:  Capability the provider must declare.
                                  Defaults to ProviderCapability.EXECUTE.

        Returns:
            ProviderResponse from provider.execute().

        Raises:
            UnknownProviderError:    provider_name not in registry.
            ProviderCapabilityError: provider lacks required_capability.
            ProviderUnavailableError: health check returned is_healthy=False.
            ProviderError:           any error from provider.execute().
        """
        provider = self._registry.get_provider(provider_name)

        if required_capability not in provider.capabilities():
            raise ProviderCapabilityError(
                f"Provider {provider_name!r} does not support "
                f"capability={required_capability.value!r}. "
                f"Supported: {sorted(c.value for c in provider.capabilities())}",
                provider_name=provider_name,
            )

        health = self._safe_health_check(provider)
        if not health.is_healthy:
            raise ProviderUnavailableError(
                f"Provider {provider_name!r} health check failed"
                + (f": {health.message}" if health.message else "."),
                provider_name=provider_name,
            )

        start = time.monotonic()
        try:
            response = provider.execute(request)
            latency_ms = int((time.monotonic() - start) * 1000)
            LOGGER.debug(
                "provider_router: %s executed operation=%s latency_ms=%d",
                provider_name, request.operation, latency_ms,
            )
            return response
        except ProviderError:
            raise
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            LOGGER.exception(
                "provider_router: unexpected exception provider=%s operation=%s latency_ms=%d",
                provider_name, request.operation, latency_ms,
            )
            raise ProviderError(
                f"Unexpected error from provider {provider_name!r}: {exc}",
                error_code="PROVIDER_UNEXPECTED_ERROR",
                provider_name=provider_name,
            ) from exc

    def provider_is_healthy(self, provider_name: str) -> bool:
        """
        Return True if the named provider is registered and healthy.

        Never raises — returns False if provider is not registered or unhealthy.
        Convenience wrapper for single-provider health polling.
        """
        try:
            provider = self._registry.get_provider(provider_name)
            health = self._safe_health_check(provider)
            if not health.is_healthy:
                LOGGER.warning(
                    "provider_router.provider_is_healthy: unhealthy "
                    "provider=%s latency_ms=%d message=%s",
                    provider_name,
                    health.latency_ms,
                    health.message or "no_message",
                )
            return health.is_healthy
        except Exception:
            LOGGER.warning(
                "provider_router.provider_is_healthy: not registered provider=%s",
                provider_name,
            )
            return False

    def provider_has_capability(
        self,
        provider_name: str,
        capability: ProviderCapability,
    ) -> bool:
        """
        Return True if the named provider supports the given capability.

        Never raises — returns False if provider is not registered.
        """
        try:
            provider = self._registry.get_provider(provider_name)
            return capability in provider.capabilities()
        except Exception:
            return False

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _safe_health_check(self, provider: "Provider") -> ProviderHealth:
        """
        Call provider.health_check(), converting any exception to is_healthy=False.

        This is the defensive wrapper that ensures the router never propagates
        exceptions from health_check() to the caller.
        """
        try:
            return provider.health_check()
        except Exception as exc:
            LOGGER.warning(
                "provider_router.health_check: exception from %s error=%s",
                type(provider).__name__, exc,
            )
            return ProviderHealth(
                provider_name=provider.provider_name,
                provider_version=provider.provider_version,
                is_healthy=False,
                latency_ms=0,
                checked_at=datetime.now(tz=timezone.utc),
                message=f"health_check raised: {exc}",
            )
