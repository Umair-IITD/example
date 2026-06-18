"""
case_engine/tenant/resolver.py

Sprint 2.27.9: ClientResolver — email domain → TenantContext.

Per SUPPORT_OPERATIONS_BLUEPRINT Layer 1.5 Resolution Process:
  1. Receive Freshdesk ticket
  2. Extract sender email
  3. Extract email domain
  4. Lookup Tenant Registry
  5. Resolve tenant
  6. Build Tenant Context
  7. Attach Tenant Context to Case
  8. Continue workflow

Per flow_diagram.mermaid:
  TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE

Failure path (per blueprint):
  "Automation must never continue with an unresolved tenant"
  → UnknownClientError → caller creates audit event → human escalation
"""
from __future__ import annotations

import logging
from typing import Optional

from case_engine.tenant.models import TenantConfig, TenantContext, UnknownClientError
from case_engine.tenant.registry import TenantRegistry

LOGGER = logging.getLogger(__name__)


class ClientResolver:
    """
    Resolves a Freshdesk requester email to a fully-typed TenantContext.

    The ClientResolver is stateless beyond its registry reference.
    It is safe to call from multiple coroutines/threads simultaneously.
    """

    def __init__(self, registry: TenantRegistry) -> None:
        self._registry = registry

    # ── Primary resolution API ────────────────────────────────────────────────

    def resolve(self, email: str) -> TenantContext:
        """
        Resolve requester email → TenantContext.

        Raises UnknownClientError if:
          - email is empty or malformed (no @ sign)
          - domain is not registered in TenantRegistry
          - tenant is disabled (enabled=False)

        The caller MUST handle UnknownClientError by:
          1. Emitting a CLIENT_RESOLUTION_FAILED audit event
          2. Escalating the ticket to human review
          3. NOT proceeding with any investigation or tool calls
        """
        domain = self.extract_domain(email)
        if not domain:
            LOGGER.warning("client_resolver.resolve malformed_email")
            raise UnknownClientError(domain="", email=email)

        config = self._registry.lookup_by_domain(domain)
        if config is None:
            LOGGER.warning("client_resolver.resolve unknown_domain domain=%s", domain)
            raise UnknownClientError(domain=domain, email=email)

        if not config.enabled:
            LOGGER.warning(
                "client_resolver.resolve tenant_disabled client_id=%s domain=%s",
                config.client_id, domain,
            )
            raise UnknownClientError(domain=domain, email=email)

        ctx = self._build_context(config, domain)
        LOGGER.info(
            "client_resolver.resolve success client_id=%s client_name=%s domain=%s",
            ctx.client_id, ctx.client_name, domain,
        )
        return ctx

    def resolve_or_none(self, email: str) -> TenantContext | None:
        """
        Resolve email → TenantContext. Returns None instead of raising.

        Use when resolution failure should not interrupt the caller's flow.
        The caller is responsible for handling a None return value.
        """
        try:
            return self.resolve(email)
        except UnknownClientError:
            return None

    def resolve_by_client_id(self, client_id: str) -> TenantContext | None:
        """
        Resolve by client_id directly (no email required).

        Used when the client is already known from TicketContext.client.
        Returns None if client_id is not registered or tenant is disabled.
        """
        config = self._registry.lookup_by_client_id(client_id)
        if config is None or not config.enabled:
            return None
        primary_domain = config.domains[0] if config.domains else ""
        return self._build_context(config, primary_domain)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def extract_domain(email: str) -> str | None:
        """
        Extract the domain part from an email address.

        Examples:
          "mrunali.gaikwad@unitybank.co.in" → "unitybank.co.in"
          "agent@bobbank.in"                → "bobbank.in"
          "invalid"                         → None
          ""                                → None

        Returns lowercase domain string, or None if email is malformed.
        """
        if not email or "@" not in email:
            return None
        parts = email.rsplit("@", 1)
        if len(parts) != 2 or not parts[1].strip():
            return None
        return parts[1].lower().strip()

    @staticmethod
    def _build_context(config: TenantConfig, domain: str) -> TenantContext:
        """Build an immutable TenantContext from a TenantConfig and resolved domain."""
        return TenantContext(
            client_id=config.client_id,
            client_name=config.client_name,
            domain=domain,
            tenant_type=config.tenant_type,
            environment=config.environment,
            enabled_tools=config.tool_config.enabled_tools,
            credentials_ref=config.auth_config.credentials_ref,
            workflow_overrides=dict(config.workflow_overrides),
            portal_base_url=config.portal_base_url,
        )


def build_client_resolver(registry: TenantRegistry | None = None) -> ClientResolver:
    """
    Build a ClientResolver.

    If registry is None, builds and uses the default TenantRegistry
    (Unity Bank pre-configured).
    """
    if registry is None:
        from case_engine.tenant.registry import build_default_tenant_registry  # noqa: PLC0415
        registry = build_default_tenant_registry()
    return ClientResolver(registry)
