"""
case_engine/provider_registry.py

Sprint 2.3: ProviderRegistry — provider lookup table.

The registry maps provider_name → Provider instance. It is the single
source of truth for which provider handles which external system.

Design decisions
────────────────
- Duplicate registrations fail at registration time (fail-fast).
  Silent override would mask misconfiguration.
- Unregistering a provider that doesn't exist also fails (fail-fast).
- Registry is not a singleton. It is an injectable dependency shared
  via ProviderRouter and ultimately wired at application startup.
- health_check_all() never raises — it catches per-provider exceptions
  and represents them as ProviderHealth(is_healthy=False).
- Thread-safe for reads (dict lookup is GIL-protected in CPython).
  Writes (registration) happen at startup before concurrent access.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from case_engine.provider_models import ProviderCapability, ProviderHealth

if TYPE_CHECKING:
    from case_engine.provider_interface import Provider

LOGGER = logging.getLogger(__name__)


class ProviderRegistrationError(RuntimeError):
    """
    Raised when provider registration violates registry invariants.

    Causes:
      - Duplicate provider_name — one provider instance per name.
    """


class UnknownProviderError(KeyError):
    """
    Raised when the registry cannot find a provider for the given name.

    Carries provider_name as an attribute for programmatic inspection.
    """

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        super().__init__(
            f"No provider registered for name={provider_name!r}. "
            "Register a Provider implementation before routing to this provider."
        )


class ProviderRegistry:
    """
    Registry mapping provider_name → Provider instance.

    Usage
    ─────
    At application startup, register all provider implementations:

        registry = ProviderRegistry()
        registry.register(FreshdeskProvider(config))     # Sprint 2.4
        registry.register(ZendeskProvider(config))       # Sprint 2.X

    At runtime, the ProviderRouter resolves providers:

        provider = registry.get_provider("freshdesk")

    Invariants
    ──────────
    - Each provider_name maps to exactly one Provider instance.
    - Duplicate registration raises ProviderRegistrationError immediately.
    - Lookup of an unregistered name raises UnknownProviderError.
    - Unregistering an unregistered name raises UnknownProviderError.
    """

    def __init__(self) -> None:
        self._providers: dict[str, "Provider"] = {}

    def register(self, provider: "Provider") -> None:
        """
        Register a provider under its declared provider_name.

        Raises:
            ProviderRegistrationError: if a provider is already registered
                with the same name.
        """
        name = provider.provider_name
        if name in self._providers:
            existing = self._providers[name]
            raise ProviderRegistrationError(
                f"Provider already registered for name={name!r}. "
                f"Existing: {type(existing).__name__!r} v{existing.provider_version}, "
                f"Attempted: {type(provider).__name__!r} v{provider.provider_version}. "
                "Unregister the existing provider before registering a replacement."
            )
        self._providers[name] = provider
        LOGGER.info(
            "provider_registry: registered %s v%s",
            type(provider).__name__, provider.provider_version,
        )

    def unregister(self, provider_name: str) -> None:
        """
        Remove a provider from the registry.

        Raises:
            UnknownProviderError: if no provider is registered with this name.
        """
        if provider_name not in self._providers:
            raise UnknownProviderError(provider_name)
        del self._providers[provider_name]
        LOGGER.info("provider_registry: unregistered %s", provider_name)

    def get_provider(self, provider_name: str) -> "Provider":
        """
        Retrieve the provider for the given name.

        Raises:
            UnknownProviderError: if no provider is registered with this name.
        """
        provider = self._providers.get(provider_name)
        if provider is None:
            raise UnknownProviderError(provider_name)
        return provider

    def list_providers(self) -> list[str]:
        """Return the names of all registered providers, sorted alphabetically."""
        return sorted(self._providers.keys())

    def health_check_all(self) -> dict[str, ProviderHealth]:
        """
        Run health_check() on every registered provider.

        Never raises. Any exception from a provider's health_check() is caught
        and returned as ProviderHealth(is_healthy=False, message=...).

        Returns:
            dict mapping provider_name → ProviderHealth for all providers.
        """
        results: dict[str, ProviderHealth] = {}
        for name, provider in self._providers.items():
            try:
                results[name] = provider.health_check()
            except Exception as exc:
                LOGGER.warning(
                    "provider_registry.health_check_all: exception from provider=%s error=%s",
                    name, exc,
                )
                results[name] = ProviderHealth(
                    provider_name=name,
                    provider_version=provider.provider_version,
                    is_healthy=False,
                    latency_ms=0,
                    checked_at=datetime.now(tz=timezone.utc),
                    message=f"health_check raised: {exc}",
                )
        return results

    def registered_count(self) -> int:
        """Return the total number of registered providers."""
        return len(self._providers)

    def provider_exists(self, provider_name: str) -> bool:
        """Return True if a provider is registered with this name."""
        return provider_name in self._providers

    def providers_with_capability(self, capability: ProviderCapability) -> list[str]:
        """
        Return names of all providers that declare the given capability.

        Useful for failover routing: find any healthy provider with EXECUTE
        capability when the primary is unavailable.
        """
        return sorted(
            name
            for name, provider in self._providers.items()
            if capability in provider.capabilities()
        )
