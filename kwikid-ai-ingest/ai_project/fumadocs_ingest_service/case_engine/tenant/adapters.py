"""
case_engine/tenant/adapters.py

Sprint 2.27.9: Tenant Adapter Architecture — abstract base + Unity Bank stub.

Per flow_diagram.mermaid:
  TENANTCTX → TENANTROUTER → UNITYADMIN → ADMINSVC → [tools]

Adapters bridge the system to each client's Support Admin Portal.
Every client (Unity Bank, Bank of Baroda, CBI, ...) has its own adapter.

Sprint 2.27.9 delivers:
  - TenantAdapter: abstract interface (the contract)
  - UnityBankAdapter: stub (architecture ready, no real API calls yet)
  - build_adapter_for_context(): factory (data-driven, no if/else for new clients)

Sprint 2.28 will add real Unity Bank API calls inside UnityBankAdapter.

Design:
  - authenticate() — validate portal credentials
  - health_check() — verify portal is reachable
  - supported_tools() — tools this adapter can execute
  - execute() — dispatch a tool call to the portal

Adding a new adapter:
  1. Create NewClientAdapter(TenantAdapter)
  2. Register in _ADAPTER_MAP: {"new_client_id": NewClientAdapter}
  That's it — zero other code changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from case_engine.tenant.models import TenantContext, UnknownClientError


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuthResult:
    """Result of an authenticate() call."""
    success:   bool
    error_msg: str = ""
    details:   dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "error_msg": self.error_msg}


@dataclass(frozen=True)
class HealthResult:
    """Result of a health_check() call."""
    healthy:    bool
    latency_ms: int = 0
    error_msg:  str = ""
    details:    dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy":    self.healthy,
            "latency_ms": self.latency_ms,
            "error_msg":  self.error_msg,
        }


@dataclass(frozen=True)
class ToolExecutionResult:
    """Result of an execute() call."""
    tool_name:  str
    success:    bool
    data:       dict[str, Any] = field(default_factory=dict)
    error_msg:  str = ""
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name":  self.tool_name,
            "success":    self.success,
            "data":       dict(self.data),
            "error_msg":  self.error_msg,
            "error_code": self.error_code,
        }


# ── Abstract base ─────────────────────────────────────────────────────────────

class TenantAdapter(ABC):
    """
    Abstract interface for all tenant-specific support portal adapters.

    Each client has exactly one adapter implementing this contract.
    The TenantRouter selects the correct adapter from TenantContext.client_id.

    Guarantees:
    - authenticate() never raises — returns AuthResult with success=False on failure
    - health_check() never raises — returns HealthResult with healthy=False on failure
    - execute() never raises — returns ToolExecutionResult with success=False on failure
    - No method stores or logs credentials (credentials_ref is a reference only)
    """

    @property
    @abstractmethod
    def client_id(self) -> str:
        """Return the client_id this adapter serves."""
        ...

    @abstractmethod
    def authenticate(self) -> AuthResult:
        """
        Validate credentials with the client's support portal.
        Returns AuthResult. Never raises.
        """
        ...

    @abstractmethod
    def health_check(self) -> HealthResult:
        """
        Verify the client's portal is reachable and healthy.
        Returns HealthResult. Never raises.
        """
        ...

    @abstractmethod
    def supported_tools(self) -> tuple[str, ...]:
        """Return names of tools this adapter can execute."""
        ...

    @abstractmethod
    def execute(self, tool_name: str, params: dict[str, Any]) -> ToolExecutionResult:
        """
        Execute a tool call against the client's support portal.

        Args:
            tool_name: Name of the tool to execute
            params:    Input parameters for the tool

        Returns ToolExecutionResult. Never raises.
        """
        ...


# ── Unity Bank adapter (stub) ─────────────────────────────────────────────────

class UnityBankAdapter(TenantAdapter):
    """
    Unity Bank Support Admin Portal adapter.

    Sprint 2.27.9: Architecture stub — returns placeholder responses.
    Sprint 2.28: Will call real Unity Bank Admin APIs.

    Supported domain: unitybank.co.in
    Client ID:        unity_bank
    """

    _CLIENT_ID: str = "unity_bank"

    _SUPPORTED_TOOLS: tuple[str, ...] = (
        "GetUserDetails",
        "GetSessionDetails",
        "GetFailureReason",
        "GetCaseHistory",
        "GetOnboardingStatus",
        "SessionLogsTool",
        "SessionSummaryTool",
        "SessionVideoTool",
        "MetricsTool",
        "ServerStatusTool",
    )

    def __init__(self, credentials_ref: str = "unity_bank_api_credentials") -> None:
        self._credentials_ref = credentials_ref

    @property
    def client_id(self) -> str:
        return self._CLIENT_ID

    def authenticate(self) -> AuthResult:
        """Sprint 2.27.9: Stub — returns success. Sprint 2.28: Validates API key."""
        return AuthResult(success=True, error_msg="")

    def health_check(self) -> HealthResult:
        """Sprint 2.27.9: Stub — returns healthy. Sprint 2.28: Pings portal endpoint."""
        return HealthResult(healthy=True, latency_ms=0, error_msg="")

    def supported_tools(self) -> tuple[str, ...]:
        return self._SUPPORTED_TOOLS

    def execute(self, tool_name: str, params: dict[str, Any]) -> ToolExecutionResult:
        """
        Sprint 2.27.9: Returns NOT_IMPLEMENTED for all tools.
        Sprint 2.28: Will dispatch to Unity Bank Admin API endpoints.
        """
        if tool_name not in self._SUPPORTED_TOOLS:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_msg=f"Tool {tool_name!r} is not supported by UnityBankAdapter",
                error_code="TOOL_NOT_SUPPORTED",
            )
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_msg="Sprint 2.28: Unity Bank API integration not yet implemented",
            error_code="NOT_IMPLEMENTED",
        )


# ── Adapter factory (data-driven — register new adapters here) ────────────────

_ADAPTER_MAP: dict[str, type[TenantAdapter]] = {
    UnityBankAdapter._CLIENT_ID: UnityBankAdapter,
    # Future:
    # "bank_of_baroda":   BankOfBarodaAdapter,
    # "central_bank_india": CentralBankAdapter,
}


def build_adapter_for_context(ctx: TenantContext) -> TenantAdapter:
    """
    Factory: return the correct TenantAdapter for a resolved TenantContext.

    Adding a new adapter:
    1. Create NewAdapter(TenantAdapter)
    2. Add: _ADAPTER_MAP["new_client_id"] = NewAdapter
    3. Zero other code changes needed.

    Raises UnknownClientError if no adapter registered for ctx.client_id.
    """
    adapter_cls = _ADAPTER_MAP.get(ctx.client_id)
    if adapter_cls is None:
        raise UnknownClientError(
            domain=ctx.domain,
            email="",
        )
    return adapter_cls(credentials_ref=ctx.credentials_ref)


def build_adapter_for_client(client_id: str) -> TenantAdapter:
    """
    Factory: return adapter by client_id (no TenantContext needed).

    Raises UnknownClientError if no adapter registered for client_id.
    """
    adapter_cls = _ADAPTER_MAP.get(client_id)
    if adapter_cls is None:
        raise UnknownClientError(domain="", email="")
    return adapter_cls()


def registered_adapter_client_ids() -> list[str]:
    """Return list of client_ids that have registered adapters."""
    return list(_ADAPTER_MAP.keys())
