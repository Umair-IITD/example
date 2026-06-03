"""
case_engine/provider_interface.py

Sprint 2.3: Provider abstract base class.

This module contains ONLY the contract. No HTTP clients, no API keys,
no provider-specific logic. It is the stable interface that all future
provider implementations (Sprint 2.4+) must satisfy.

Naming convention for implementations:
  <Name>Provider
  e.g. FreshdeskProvider, ZendeskProvider, SalesforceProvider

All implementations live in their own modules (sprint_2_4+) and import from
this module. This module NEVER imports from any implementation module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from case_engine.provider_models import (
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderRequest,
    ProviderResponse,
)


class Provider(ABC):
    """
    Abstract base class all provider implementations must satisfy.

    Contract
    ────────
    1. Declare provider_name — unique registry key (e.g. "freshdesk").
    2. Declare provider_version — semantic version (e.g. "1.0.0").
    3. Implement capabilities() — declare which ProviderCapability values
       this provider supports. The router validates this before routing.
    4. Implement health_check() — self-report availability. MUST NOT raise;
       return ProviderHealth(is_healthy=False, ...) instead.
    5. Implement execute() — perform the operation against the external system.

    Provider Invariants
    ───────────────────
    STATELESS: Providers must not store per-request state on self.
    All per-request state travels via ProviderRequest.

    IDEMPOTENT: execute() must be idempotent when called with the same
    request.request_id. Use request.request_id as the provider-side
    idempotency key (not request.trace_id, which changes each attempt).

    NO PERSISTENCE: Providers must not access ActionRepository, ActionGateway,
    or any database. They are leaf nodes in the dependency graph.

    NO PII IN LOGS: The payload in ProviderRequest has already been sanitized
    by the executor layer. Do not log raw API responses.

    DEADLINE AWARENESS: Respect request.timeout_seconds. Raise
    ProviderTimeoutError rather than blocking indefinitely.

    Error Handling
    ──────────────
    Raise ProviderTransientError subclasses for recoverable failures.
    Raise ProviderPermanentError subclasses for non-recoverable failures.
    Executors translate these to ActionExecutionError subclasses.
    Never raise ActionExecutionError directly from a provider.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Unique name for this provider.

        Used as the registry routing key. Must be lowercase, stable across
        restarts and deployments, and globally unique within a registry.

        Examples: "freshdesk", "zendesk", "salesforce_crm"
        """

    @property
    @abstractmethod
    def provider_version(self) -> str:
        """
        Semantic version of this provider implementation.

        Allows multiple provider versions to coexist during migrations
        (e.g. freshdesk_v1 and freshdesk_v2 registered with different names).

        Format: "MAJOR.MINOR.PATCH" (e.g. "1.0.0", "2.3.1")
        """

    @abstractmethod
    def capabilities(self) -> frozenset[ProviderCapability]:
        """
        Declare which operations this provider supports.

        The ProviderRouter validates that the required capability is in this
        set before routing the request. An empty set means the provider
        supports no operations (useful for stub providers in tests).

        Must return a frozenset to prevent accidental mutation.

        Returns:
            frozenset of ProviderCapability values this provider supports.
        """

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """
        Self-report provider reachability and readiness.

        Called by ProviderRouter before routing each request. Must complete
        quickly (< 5 seconds). MUST NOT raise — catch all exceptions and
        return ProviderHealth(is_healthy=False, message=str(exc)) instead.

        Returns:
            ProviderHealth with is_healthy=True if ready for requests.
            ProviderHealth with is_healthy=False if unavailable.
        """

    @abstractmethod
    def execute(self, request: ProviderRequest) -> ProviderResponse:
        """
        Execute the requested operation against the external system.

        Use request.request_id as the idempotency key on the external system.
        Use request.trace_id for distributed tracing spans.
        Respect request.timeout_seconds.

        Args:
            request: Immutable operation request with sanitized payload.

        Returns:
            ProviderResponse with success=True on provider confirmation.
            ProviderResponse with success=False for business-logic failures
            where the provider was reachable but could not apply the operation.

        Raises:
            ProviderTransientError: recoverable failure — executor will retry.
            ProviderPermanentError: non-recoverable — executor must not retry.
            Do NOT raise ActionExecutionError from a provider.
        """

    def metadata(self) -> ProviderMetadata:
        """
        Return static metadata about this provider.

        Default implementation builds ProviderMetadata from abstract properties.
        Override to include additional description or metadata fields.
        """
        return ProviderMetadata(
            provider_name=self.provider_name,
            provider_version=self.provider_version,
            capabilities=self.capabilities(),
        )
