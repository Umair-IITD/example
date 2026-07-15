"""
case_engine/tools/framework/adapters.py

Sprint 2.45: Tool Adapter contracts (Part D).

Defines the ToolAdapter ABC that all future production integrations MUST inherit.
Also provides Protocol definitions (structural contracts) for specific adapter
categories (AdminPortal, Session, Logs, Vision, Metrics, Freshdesk).

Dependency direction:
  adapters.py → framework/models.py (ToolContext, ToolHealth)
  adapters.py → stdlib only

DO NOT implement concrete adapters here.
Concrete adapters arrive in future sprints when production APIs are integrated.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from case_engine.tools.framework.models import ToolContext, ToolHealth, ToolStatus


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Base adapter ───────────────────────────────────────────────────────────────

class ToolAdapter(ABC):
    """
    Abstract base class for all production tool adapters.

    Every future production integration that connects to an external API
    (Support Admin Portal, Session API, Logs API, etc.) MUST extend this class.

    Sprint 2.45 establishes the contract only.
    Concrete implementations arrive in future sprints as APIs are onboarded.

    Relationship with BaseTool:
      BaseTool is the Sprint 2.17 execution interface (definition + run).
      ToolAdapter is the Sprint 2.45 production interface (adapter_name,
      supported_tools, is_available, execute_tool, health_check).

      A production tool adapter would normally extend BOTH ToolAdapter
      (for routing and health) and BaseTool (for execution dispatch).
      For Sprint 2.45, these are separate contracts.
    """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Stable identifier for this adapter (e.g. 'AdminPortalAdapter')."""
        ...

    @property
    @abstractmethod
    def supported_tools(self) -> tuple[str, ...]:
        """Tool names this adapter can execute."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return True if the target API is reachable.
        Must complete quickly (< 2s). Do NOT block.
        """
        ...

    @abstractmethod
    def execute_tool(
        self,
        tool_name: str,
        inputs:    dict[str, Any],
        context:   ToolContext,
    ) -> dict[str, Any]:
        """
        Execute the named tool and return a payload dict.

        Raise on failure — ProductionToolExecutor catches and wraps.
        Must NOT modify external state beyond the targeted API call.
        Must NOT call LLMs.
        Must NOT bypass Action Gateway for any write/execute operations.
        """
        ...

    def health_check(self) -> ToolHealth:
        """
        Return a ToolHealth snapshot.

        Default implementation: UNKNOWN status.
        Concrete adapters should override with real health checks.
        """
        return ToolHealth(
            tool_name=self.adapter_name,
            status=ToolStatus.UNKNOWN.value,
            last_checked_at=_now_iso(),
        )

    def can_handle(self, tool_name: str) -> bool:
        """Return True if this adapter supports the given tool_name."""
        return tool_name in self.supported_tools


# ── Protocol stubs for future adapter categories ───────────────────────────────
# These are structural contracts — no implementation required in Sprint 2.45.
# Future sprint implementations will satisfy these protocols.

@runtime_checkable
class AdminPortalAdapterProtocol(Protocol):
    """
    Protocol for multi-tenant Support Admin Portal API adapters.

    Implemented per tenant (UnityBankAdminAdapter, BoBAdminAdapter, …).
    Reached via TenantAPIRouter which selects the correct adapter by tenant_id.
    """
    def get_session_details(
        self, session_id: str, tenant_id: str
    ) -> dict[str, Any]: ...

    def get_user_details(
        self, phone_number: str, tenant_id: str
    ) -> dict[str, Any]: ...

    def get_failure_reason(
        self, operation_id: str, tenant_id: str
    ) -> dict[str, Any]: ...

    def get_case_history(
        self, phone_number: str, tenant_id: str
    ) -> dict[str, Any]: ...

    def get_onboarding_status(
        self, application_id: str, tenant_id: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class SessionAdapterProtocol(Protocol):
    """Protocol for session management API adapters."""
    def get_session(
        self, session_id: str
    ) -> dict[str, Any]: ...

    def get_session_logs(
        self, session_id: str
    ) -> dict[str, Any]: ...

    def get_session_summary(
        self, session_id: str
    ) -> dict[str, Any]: ...

    def get_session_video(
        self, session_id: str
    ) -> dict[str, Any]: ...

    def reset_session(
        self, session_id: str, reason: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class LogsAdapterProtocol(Protocol):
    """Protocol for backend operation log API adapters."""
    def get_failure_reason(
        self, operation_id: str
    ) -> dict[str, Any]: ...

    def get_operation_logs(
        self, session_id: str, operation_type: str
    ) -> dict[str, Any]: ...

    def get_audit_events(
        self, session_id: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class VisionAdapterProtocol(Protocol):
    """Protocol for computer vision / video analysis API adapters."""
    def analyse_image(
        self, image_url: str, analysis_type: str
    ) -> dict[str, Any]: ...

    def analyse_video(
        self, video_url: str
    ) -> dict[str, Any]: ...

    def is_available(self) -> bool: ...


@runtime_checkable
class MetricsAdapterProtocol(Protocol):
    """Protocol for metrics and monitoring API adapters."""
    def get_system_metrics(
        self, tenant_id: str, time_range: str
    ) -> dict[str, Any]: ...

    def get_server_status(
        self, service_name: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class FreshdeskAdapterProtocol(Protocol):
    """Protocol for Freshdesk CRM API adapters."""
    def add_internal_note(
        self, ticket_id: str, body: str
    ) -> dict[str, Any]: ...

    def send_customer_reply(
        self, ticket_id: str, body: str
    ) -> dict[str, Any]: ...

    def close_ticket(
        self, ticket_id: str
    ) -> dict[str, Any]: ...

    def get_ticket(
        self, ticket_id: str
    ) -> dict[str, Any]: ...
