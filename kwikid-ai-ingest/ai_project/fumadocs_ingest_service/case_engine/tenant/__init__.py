"""
case_engine/tenant

Sprint 2.27.9: Multi-Tenant Client Resolution Layer.

Per flow_diagram.mermaid:
  TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE
  TENANTCTX → TENANTROUTER → UNITYADMIN → ADMINSVC → [tools]

Public API:
  Models:     TenantConfig, TenantContext, TenantType, TenantEnvironment,
              TenantToolConfig, TenantAuthConfig, UnknownClientError
  Registry:   TenantRegistry, build_default_tenant_registry
  Resolver:   ClientResolver, build_client_resolver
  Tools:      TenantAwareToolRegistry, build_tenant_tool_registry
  Adapters:   TenantAdapter, UnityBankAdapter, AuthResult, HealthResult,
              ToolExecutionResult, build_adapter_for_context, build_adapter_for_client,
              registered_adapter_client_ids
"""
from __future__ import annotations

from case_engine.tenant.models import (
    TenantConfig,
    TenantContext,
    TenantType,
    TenantEnvironment,
    TenantToolConfig,
    TenantAuthConfig,
    UnknownClientError,
)
from case_engine.tenant.registry import (
    TenantRegistry,
    build_default_tenant_registry,
)
from case_engine.tenant.resolver import (
    ClientResolver,
    build_client_resolver,
)
from case_engine.tenant.tool_registry import (
    TenantAwareToolRegistry,
    build_tenant_tool_registry,
)
from case_engine.tenant.adapters import (
    TenantAdapter,
    UnityBankAdapter,
    AuthResult,
    HealthResult,
    ToolExecutionResult,
    build_adapter_for_context,
    build_adapter_for_client,
    registered_adapter_client_ids,
)

__all__ = [
    # Models
    "TenantConfig",
    "TenantContext",
    "TenantType",
    "TenantEnvironment",
    "TenantToolConfig",
    "TenantAuthConfig",
    "UnknownClientError",
    # Registry
    "TenantRegistry",
    "build_default_tenant_registry",
    # Resolver
    "ClientResolver",
    "build_client_resolver",
    # Tool registry
    "TenantAwareToolRegistry",
    "build_tenant_tool_registry",
    # Adapters
    "TenantAdapter",
    "UnityBankAdapter",
    "AuthResult",
    "HealthResult",
    "ToolExecutionResult",
    "build_adapter_for_context",
    "build_adapter_for_client",
    "registered_adapter_client_ids",
]
