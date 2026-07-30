"""
case_engine/tenant/models.py

Sprint 2.27.9: Multi-Tenant Client Resolution Layer — domain models.

Per SUPPORT_OPERATIONS_BLUEPRINT Layer 1.5 and flow_diagram.mermaid:
  TICKET → CLIENTRESOLVE → TENANTREG → TENANTCTX → CASE

Design:
  - NO imports from case_engine core (zero dependency on models.py, audit.py, etc.)
  - All models are frozen dataclasses (immutable, safe to pass through layers)
  - TenantContext is the canonical context object that travels the full pipeline
  - UnknownClientError is the controlled failure path (no automation continues)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TenantType(str, Enum):
    BANK    = "bank"
    NBFC    = "nbfc"
    FINTECH = "fintech"
    UNKNOWN = "unknown"


class TenantEnvironment(str, Enum):
    PRODUCTION = "production"
    UAT        = "uat"
    STAGING    = "staging"
    DEV        = "dev"


@dataclass(frozen=True)
class TenantToolConfig:
    """Tool configuration for a specific tenant."""
    enabled_tools:  tuple[str, ...]
    max_tool_calls: int = 10

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled_tools":  list(self.enabled_tools),
            "max_tool_calls": self.max_tool_calls,
        }


@dataclass(frozen=True)
class TenantAuthConfig:
    """
    Reference to auth credentials for a tenant's support portal.

    SECURITY: credentials_ref is a KEY NAME pointing to a secrets store.
    The actual credential value is NEVER stored here.
    """
    credentials_ref: str
    auth_type:       str = "api_key"

    def to_dict(self) -> dict[str, Any]:
        return {
            "credentials_ref": self.credentials_ref,
            "auth_type":       self.auth_type,
        }


@dataclass(frozen=True)
class TenantConfig:
    """
    Data-driven configuration for one registered tenant/client.

    Adding a new tenant (e.g., Bank of Baroda) requires ONLY a new TenantConfig
    instance added to the registry — no code changes anywhere else.

    Fields:
        client_id:          Stable internal identifier (e.g., "unity_bank")
        client_name:        Human-readable name (e.g., "Unity Bank")
        domains:            Email domains that map to this tenant
        tenant_type:        Bank, NBFC, etc.
        environment:        Production / UAT / Staging / Dev
        tool_config:        Which tools are enabled for this tenant
        auth_config:        Credentials reference (not actual secrets)
        workflow_overrides: Tenant-specific workflow customizations
        portal_base_url:    Base URL of client's support portal
        enabled:            False to disable without removing from registry
    """
    client_id:          str
    client_name:        str
    domains:            tuple[str, ...]
    tenant_type:        TenantType
    environment:        TenantEnvironment
    tool_config:        TenantToolConfig
    auth_config:        TenantAuthConfig
    workflow_overrides:  dict[str, Any] = field(default_factory=dict)
    portal_base_url:     str = ""
    log_datasource_ref:  str = ""
    enabled:             bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id":           self.client_id,
            "client_name":         self.client_name,
            "domains":             list(self.domains),
            "tenant_type":         self.tenant_type.value,
            "environment":         self.environment.value,
            "tool_config":         self.tool_config.to_dict(),
            "auth_config":         self.auth_config.to_dict(),
            "workflow_overrides":  dict(self.workflow_overrides),
            "portal_base_url":     self.portal_base_url,
            "log_datasource_ref":  self.log_datasource_ref,
            "enabled":             self.enabled,
        }


@dataclass(frozen=True)
class TenantContext:
    """
    Active tenant context resolved from a support ticket's requester email.

    Per SUPPORT_OPERATIONS_BLUEPRINT Layer 1.5:
      "Every investigation must execute within the resolved Tenant Context."

    This object MUST travel through the full pipeline:
      Ticket → Case → Workflow → Investigation → Reasoning → Execution → Audit

    Fields:
        client_id:          e.g., "unity_bank"
        client_name:        e.g., "Unity Bank"
        domain:             Actual domain extracted from email ("unitybank.co.in")
        tenant_type:        Bank, NBFC, etc.
        environment:        UAT / Production / etc.
        enabled_tools:      Tools available for this tenant's investigations
        credentials_ref:    Reference key for secrets store (NOT the secret itself)
        workflow_overrides: Any tenant-specific workflow customizations
        portal_base_url:    Base URL for client's support portal API
    """
    client_id:           str
    client_name:         str
    domain:              str
    tenant_type:         TenantType
    environment:         TenantEnvironment
    enabled_tools:              tuple[str, ...]
    credentials_ref:            str
    workflow_overrides:         dict[str, Any] = field(default_factory=dict)
    portal_base_url:            str = ""
    log_datasource_reference:   str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id":                 self.client_id,
            "client_name":               self.client_name,
            "domain":                    self.domain,
            "tenant_type":               self.tenant_type.value,
            "environment":               self.environment.value,
            "enabled_tools":             list(self.enabled_tools),
            "credentials_ref":           self.credentials_ref,
            "workflow_overrides":        dict(self.workflow_overrides),
            "portal_base_url":           self.portal_base_url,
            "log_datasource_reference":  self.log_datasource_reference,
        }


class UnknownClientError(Exception):
    """
    Raised when a requester email domain cannot be resolved to a registered tenant.

    Per SUPPORT_OPERATIONS_BLUEPRINT Layer 1.5:
      "Automation must never continue with an unresolved tenant."

    Callers MUST catch this and:
      1. Create an audit event (CLIENT_RESOLUTION_FAILED)
      2. Route to human review
      3. NOT proceed with any investigation
    """
    def __init__(self, domain: str, email: str = "") -> None:
        self.domain = domain
        self.email  = email
        super().__init__(
            f"Unknown client domain: {domain!r}. "
            "Register the tenant in TenantRegistry to enable automated processing."
        )
