"""
case_engine/investigation/collector/registry.py

Sprint 2.42: ProviderRegistry — registers and resolves EvidenceProviders by EvidenceKind.

Thread-safe via threading.Lock (write-once-at-startup, read-heavy pattern).

Public API:
  ProviderRegistry      — register/resolve/list/fallback
  build_default_registry() — factory with NullProvider as universal fallback

Dependency direction:
  registry.py → collector/contracts.py (EvidenceProvider, CollectionContext, CollectionResult)
  registry.py → collector/exceptions.py (ProviderNotFoundError)
  registry.py → investigation/planner/models.py (EvidenceKind)
  registry.py → stdlib only
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from case_engine.investigation.collector.contracts import (
    CollectionContext,
    CollectionResult,
    EvidenceProvider,
)
from case_engine.investigation.collector.exceptions import ProviderNotFoundError
from case_engine.investigation.planner.models import EvidenceKind

LOGGER = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Thread-safe registry mapping EvidenceKind → EvidenceProvider.

    One provider per kind — later registrations overwrite earlier ones.
    A fallback provider is used when no specific provider is registered.

    Thread safety: uses threading.Lock. Not reentrant — do not call
    register() from within an EvidenceProvider.execute() callback.
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._providers: dict[str, EvidenceProvider] = {}
        self._fallback: EvidenceProvider | None = None

    def register(self, kind: EvidenceKind, provider: EvidenceProvider) -> None:
        """Register a provider for the given EvidenceKind (overwrites any prior registration)."""
        with self._lock:
            self._providers[kind.value] = provider
        LOGGER.debug(
            "provider_registry.registered kind=%s provider=%s",
            kind.value, provider.provider_name,
        )

    def set_fallback(self, provider: EvidenceProvider) -> None:
        """Set the fallback provider used when no specific provider is registered."""
        with self._lock:
            self._fallback = provider
        LOGGER.debug(
            "provider_registry.fallback_set provider=%s", provider.provider_name
        )

    def resolve(self, kind: EvidenceKind) -> EvidenceProvider | None:
        """
        Return the specifically registered provider for kind, or None.

        Never uses the fallback. Never raises.
        """
        with self._lock:
            return self._providers.get(kind.value)

    def resolve_with_fallback(self, kind: EvidenceKind) -> EvidenceProvider:
        """
        Return the registered provider for kind, or the fallback if not registered.

        Raises ProviderNotFoundError if neither specific nor fallback is available.
        """
        with self._lock:
            provider = self._providers.get(kind.value)
            if provider is not None:
                return provider
            if self._fallback is not None:
                LOGGER.debug(
                    "provider_registry.using_fallback kind=%s fallback=%s",
                    kind.value, self._fallback.provider_name,
                )
                return self._fallback
        raise ProviderNotFoundError(kind=kind.value)

    def is_registered(self, kind: EvidenceKind) -> bool:
        """Return True if a specific provider is registered for kind."""
        with self._lock:
            return kind.value in self._providers

    def has_fallback(self) -> bool:
        """Return True if a fallback provider is set."""
        with self._lock:
            return self._fallback is not None

    def list_kinds(self) -> list[EvidenceKind]:
        """Return all EvidenceKind values that have a specific provider registered."""
        with self._lock:
            return [EvidenceKind(k) for k in self._providers]

    def provider_count(self) -> int:
        """Return the number of specifically registered providers."""
        with self._lock:
            return len(self._providers)

    def reset(self) -> None:
        """Clear all registrations. Intended for test isolation only."""
        with self._lock:
            self._providers.clear()
            self._fallback = None
        LOGGER.debug("provider_registry.reset")


# ── Null provider (universal fallback) ────────────────────────────────────────

class _NullProvider:
    """
    Fallback provider that records a structured failure for any step.

    Used when no specific provider is registered for an EvidenceKind.
    Never raises — returns a failed CollectionResult with a clear error code.
    """

    @property
    def provider_name(self) -> str:
        return "NullProvider"

    def can_handle(self, evidence_kind: str) -> bool:
        return True  # handles all kinds as a last resort

    def execute(
        self,
        step_id: str,
        evidence_kind: str,
        context: CollectionContext,
        requirement_metadata: dict[str, Any],
    ) -> CollectionResult:
        return CollectionResult.fail(
            provider_name=self.provider_name,
            step_id=step_id,
            evidence_kind=evidence_kind,
            error_code="NO_PROVIDER_REGISTERED",
            error_message=(
                f"No evidence provider is registered for EvidenceKind={evidence_kind!r}. "
                f"Register a provider via ProviderRegistry.register() to enable this step."
            ),
        )


def build_default_registry() -> ProviderRegistry:
    """
    Build a ProviderRegistry with a NullProvider as universal fallback.

    The NullProvider ensures that missing providers produce structured failures
    rather than uncaught exceptions.
    """
    registry = ProviderRegistry()
    registry.set_fallback(_NullProvider())
    return registry
