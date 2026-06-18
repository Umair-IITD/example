"""
case_engine/tenant/tool_registry.py

Sprint 2.27.9: TenantAwareToolRegistry — per-client tool set management.

Per flow_diagram.mermaid:
  TENANTCTX → TENANTROUTER → UNITYADMIN → ADMINSVC → [tools]
  TOOLS -.-> GetUserDetails, GetSessionDetails, GetFailureReason, ...

Each client has a configured set of enabled tools. Tool selection is always
client-aware: the system MUST check is_tool_enabled() before executing
any tool call.

Unity Bank tools:
  GetUserDetails, GetSessionDetails, GetFailureReason, GetCaseHistory,
  GetOnboardingStatus, SessionLogsTool, SessionSummaryTool, SessionVideoTool,
  MetricsTool, ServerStatusTool

Future BOB tools (example):
  VideoCropTool, GetUserDetails

Adding tools for a new client: update TenantConfig.tool_config.enabled_tools
No code changes here.
"""
from __future__ import annotations

from case_engine.tenant.models import TenantContext
from case_engine.tenant.registry import TenantRegistry


class TenantAwareToolRegistry:
    """
    Per-tenant tool registry.

    Tool availability is determined by the tenant's TenantConfig.tool_config.
    Tools registered at runtime (via register_tool_for_client) are additive
    — they extend but cannot override the tenant's base configuration.

    Usage:
        registry = TenantAwareToolRegistry(tenant_registry)
        if registry.is_tool_enabled("GetUserDetails", "unity_bank"):
            # safe to call GetUserDetails
    """

    def __init__(self, registry: TenantRegistry) -> None:
        self._tenant_registry = registry
        self._runtime_extras: dict[str, set[str]] = {}

    # ── Primary API ───────────────────────────────────────────────────────────

    def get_tools_for_client(self, client_id: str) -> tuple[str, ...]:
        """Return sorted tuple of enabled tool names for a given client_id."""
        config = self._tenant_registry.lookup_by_client_id(client_id)
        if config is None:
            return ()
        base  = set(config.tool_config.enabled_tools)
        extra = self._runtime_extras.get(client_id, set())
        return tuple(sorted(base | extra))

    def get_tools_for_context(self, ctx: TenantContext) -> tuple[str, ...]:
        """Return enabled tools for an active TenantContext."""
        return self.get_tools_for_client(ctx.client_id)

    def is_tool_enabled(self, tool_name: str, client_id: str) -> bool:
        """Return True iff the named tool is enabled for this client."""
        return tool_name in self.get_tools_for_client(client_id)

    def is_tool_enabled_for_context(self, tool_name: str, ctx: TenantContext) -> bool:
        """Return True iff the named tool is enabled for this TenantContext."""
        return self.is_tool_enabled(tool_name, ctx.client_id)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def register_tool_for_client(self, tool_name: str, client_id: str) -> None:
        """
        Dynamically register an additional tool for a client at runtime.

        This adds to the client's tool set; it does not replace it.
        Primarily used in tests and for future admin-driven tool configuration.
        """
        self._runtime_extras.setdefault(client_id, set()).add(tool_name)

    # ── Introspection ─────────────────────────────────────────────────────────

    def all_registered_clients(self) -> list[str]:
        """Return client_ids of all tenants that have tool configurations."""
        return [c.client_id for c in self._tenant_registry.all_tenants()]

    def tool_count_for_client(self, client_id: str) -> int:
        """Return number of enabled tools for a client."""
        return len(self.get_tools_for_client(client_id))

    def tools_diff(self, client_id_a: str, client_id_b: str) -> dict[str, list[str]]:
        """
        Compare tool sets of two clients.

        Returns:
          {"only_in_a": [...], "only_in_b": [...], "shared": [...]}
        """
        set_a = set(self.get_tools_for_client(client_id_a))
        set_b = set(self.get_tools_for_client(client_id_b))
        return {
            "only_in_a": sorted(set_a - set_b),
            "only_in_b": sorted(set_b - set_a),
            "shared":    sorted(set_a & set_b),
        }


def build_tenant_tool_registry(registry: TenantRegistry | None = None) -> TenantAwareToolRegistry:
    """Build a TenantAwareToolRegistry with the given or default TenantRegistry."""
    if registry is None:
        from case_engine.tenant.registry import build_default_tenant_registry  # noqa: PLC0415
        registry = build_default_tenant_registry()
    return TenantAwareToolRegistry(registry)
