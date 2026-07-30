"""
case_engine/tenant/registry.py

Sprint 2.27.9: TenantRegistry — data-driven tenant configuration store.

Per SUPPORT_OPERATIONS_BLUEPRINT Layer 1.5:
  "Lookup Tenant Registry → Resolve tenant → Build Tenant Context"

Design:
  - NO if/else chains — all tenant resolution is dict-based lookups
  - Adding a new tenant = adding a TenantConfig entry to _DEFAULT_TENANTS
  - Supports lookup by domain AND by client_id
  - Supports runtime registration of new tenants
  - Validates for uniqueness constraints

Current rollout: UNITY_BANK only (unitybank.co.in)
Future: BOB, CBI, additional clients via config — zero code changes
"""
from __future__ import annotations

from case_engine.tenant.models import (
    TenantAuthConfig,
    TenantConfig,
    TenantEnvironment,
    TenantToolConfig,
    TenantType,
)

# ── Unity Bank tool set (per flow_diagram.mermaid ADMINSVC subtree) ──────────

_UNITY_BANK_TOOLS: tuple[str, ...] = (
    "GetUserDetails",
    "GetSessionDetails",
    "GetFailureReason",
    "GetCaseHistory",
    "GetOnboardingStatus",
    "GetSessionLogsTool",
    "SessionSummaryTool",
    "SessionVideoTool",
    "MetricsTool",
    "ServerStatusTool",
)

# ── Default registered tenants — data-driven, no code changes needed for new tenants ─

_DEFAULT_TENANTS: dict[str, TenantConfig] = {
    "unity_bank": TenantConfig(
        client_id="unity_bank",
        client_name="Unity Bank",
        domains=("unitybank.co.in",),
        tenant_type=TenantType.BANK,
        environment=TenantEnvironment.UAT,
        tool_config=TenantToolConfig(enabled_tools=_UNITY_BANK_TOOLS),
        auth_config=TenantAuthConfig(credentials_ref="unity_bank_api_credentials"),
        workflow_overrides={},
        portal_base_url="",
        log_datasource_ref="saas",
        enabled=True,
    ),
    # Future tenants — add here, zero code changes anywhere else:
    # "bank_of_baroda": TenantConfig(
    #     client_id="bank_of_baroda",
    #     client_name="Bank of Baroda",
    #     domains=("bobbank.in",),
    #     ...
    # ),
    # "central_bank_india": TenantConfig(
    #     client_id="central_bank_india",
    #     client_name="Central Bank of India",
    #     domains=("centralbank.co.in",),
    #     ...
    # ),
}


class TenantRegistry:
    """
    Central registry of all registered clients/tenants.

    Backed by two dictionaries (no if/else chains):
      _by_client_id: client_id → TenantConfig
      _by_domain:    domain    → TenantConfig

    Adding a new client:
      1. Create a TenantConfig (with domains, tools, auth config)
      2. Add to _DEFAULT_TENANTS or call registry.register(config)
      That's it — no other code changes.
    """

    def __init__(self, tenants: dict[str, TenantConfig] | None = None) -> None:
        self._by_client_id: dict[str, TenantConfig] = {}
        self._by_domain:    dict[str, TenantConfig] = {}
        configs = tenants if tenants is not None else _DEFAULT_TENANTS
        for config in configs.values():
            self._register_config(config)

    def _register_config(self, config: TenantConfig) -> None:
        self._by_client_id[config.client_id] = config
        for domain in config.domains:
            self._by_domain[domain.lower()] = config

    # ── Lookups ───────────────────────────────────────────────────────────────

    def lookup_by_domain(self, domain: str) -> TenantConfig | None:
        """Return TenantConfig for the given email domain, or None."""
        return self._by_domain.get(domain.lower())

    def lookup_by_client_id(self, client_id: str) -> TenantConfig | None:
        """Return TenantConfig for the given client_id, or None."""
        return self._by_client_id.get(client_id)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def register(self, config: TenantConfig) -> None:
        """
        Register a new tenant at runtime.

        Raises ValueError if client_id or any domain is already registered.
        This prevents accidental overwrites.
        """
        if config.client_id in self._by_client_id:
            raise ValueError(
                f"client_id {config.client_id!r} is already registered. "
                "Use a unique client_id or deregister first."
            )
        for domain in config.domains:
            if domain.lower() in self._by_domain:
                existing = self._by_domain[domain.lower()].client_id
                raise ValueError(
                    f"Domain {domain!r} is already registered under {existing!r}. "
                    "Each domain must map to exactly one tenant."
                )
        self._register_config(config)

    # ── Introspection ─────────────────────────────────────────────────────────

    def all_tenants(self) -> list[TenantConfig]:
        """Return all registered TenantConfig instances."""
        return list(self._by_client_id.values())

    def all_client_ids(self) -> list[str]:
        """Return all registered client_ids."""
        return list(self._by_client_id.keys())

    def all_domains(self) -> list[str]:
        """Return all registered domains."""
        return list(self._by_domain.keys())

    def count(self) -> int:
        """Return number of registered tenants."""
        return len(self._by_client_id)

    def domain_count(self) -> int:
        """Return total number of registered domains across all tenants."""
        return len(self._by_domain)

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """
        Validate registry integrity.

        Returns a list of error messages (empty list = valid).

        Checks:
        - At least one tenant registered
        - No empty domains
        - Enabled tenants have at least one tool
        """
        errors: list[str] = []
        if len(self._by_client_id) == 0:
            errors.append("TenantRegistry has no registered tenants — at least one required")
            return errors

        for domain in self._by_domain:
            if not domain:
                errors.append("Empty domain string found in registry")

        for client_id, config in self._by_client_id.items():
            if config.enabled and not config.tool_config.enabled_tools:
                errors.append(
                    f"Tenant {client_id!r} is enabled but has no tools configured"
                )
            if not config.domains:
                errors.append(
                    f"Tenant {client_id!r} has no email domains registered"
                )

        return errors


def build_default_tenant_registry() -> TenantRegistry:
    """Build and return the default TenantRegistry with all configured tenants."""
    return TenantRegistry()
