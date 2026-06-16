"""
case_engine/adapters/execution_adapter.py

Sprint 2.27: AdapterBackedExecutionAdapter.

Bridges the production Adapter Framework (Sprint 2.27) with the existing
ExecutionAdapter interface (Sprint 2.23).

Flow:
  ActionExecutor.execute()
    → AdapterBackedExecutionAdapter.run()
        → AdapterRouter.route(AdapterRequest)
            → Adapter.execute(request)
        → translate AdapterExecutionResult → dict (ExecutionAdapter return format)

Routing table:
  Maps action_type to (AdapterType, AdapterOperation).
  Unknown action types default to (ADMIN_PORTAL, EXECUTE).

Design:
  - Implements ExecutionAdapter.run() — drop-in replacement for MockExecutionAdapter
  - Never raises — all exceptions return failure dict
  - Logging at DEBUG level for routing decisions
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.adapters.adapter_router import AdapterRouter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterType,
)
from case_engine.execution.executor import ExecutionAdapter

LOGGER = logging.getLogger(__name__)

# ── Action type routing table ─────────────────────────────────────────────────
# Maps action_type → (AdapterType, AdapterOperation)
# This centralizes the "which system handles which action" logic.

_ACTION_ROUTING_TABLE: dict[str, tuple[AdapterType, AdapterOperation]] = {
    # OTP actions → Freshdesk (customer-facing)
    "otp_resend":               (AdapterType.FRESHDESK,    AdapterOperation.EXECUTE),
    # VKYC and session actions → Admin Portal
    "vkyc_session_reset":       (AdapterType.ADMIN_PORTAL, AdapterOperation.EXECUTE),
    "agent_session_refresh":    (AdapterType.ADMIN_PORTAL, AdapterOperation.EXECUTE),
    # OCR and document actions → Admin Portal
    "document_ocr_reprocess":   (AdapterType.ADMIN_PORTAL, AdapterOperation.EXECUTE),
    # Callback retry → Admin Portal
    "api_callback_retry":       (AdapterType.ADMIN_PORTAL, AdapterOperation.EXECUTE),
    # Monitoring / alerting → Monitoring
    "emit_metric":              (AdapterType.MONITORING,   AdapterOperation.WRITE),
    "trigger_alert":            (AdapterType.MONITORING,   AdapterOperation.EXECUTE),
    # L2 escalation → Asana
    "create_l2_ticket":         (AdapterType.ASANA,        AdapterOperation.CREATE),
    # Freshdesk note / ticket operations
    "post_ticket_note":         (AdapterType.FRESHDESK,    AdapterOperation.WRITE),
    "close_ticket":             (AdapterType.FRESHDESK,    AdapterOperation.UPDATE),
    "update_ticket_status":     (AdapterType.FRESHDESK,    AdapterOperation.UPDATE),
}

_DEFAULT_ROUTING: tuple[AdapterType, AdapterOperation] = (
    AdapterType.ADMIN_PORTAL,
    AdapterOperation.EXECUTE,
)


class AdapterBackedExecutionAdapter(ExecutionAdapter):
    """
    Production execution adapter backed by the AdapterRouter framework.

    Replaces MockExecutionAdapter when an AdapterRouter is available.
    Translates action_type → AdapterType + AdapterOperation and delegates
    to the appropriate registered Adapter via AdapterRouter.route().
    """

    def __init__(self, router: AdapterRouter) -> None:
        self._router = router

    @property
    def adapter_name(self) -> str:
        return "adapter-router-backed"

    def run(
        self,
        action_type:   str,
        action_params: dict[str, Any],
        case_id:       str,
    ) -> dict[str, Any]:
        """
        Execute an action by routing it through the AdapterRouter.

        Returns a dict with: {"success": bool, "response_data": dict,
                              "error_code": str|None, "error_message": str|None}
        Never raises.
        """
        try:
            adapter_type, operation = _ACTION_ROUTING_TABLE.get(
                action_type, _DEFAULT_ROUTING
            )
            LOGGER.debug(
                "adapter_backed.run action_type=%s → adapter=%s op=%s case_id=%s",
                action_type, adapter_type.value, operation.value, case_id,
            )

            request = AdapterRequest.create(
                adapter_type=adapter_type,
                operation=operation,
                payload=dict(action_params),
                case_id=case_id,
                action_type=action_type,
            )

            result = self._router.route(request)

            return {
                "success":       result.success,
                "response_data": {
                    "adapter_name": result.adapter_name,
                    "adapter_type": result.response.adapter_type.value,
                    "status":       result.response.status.value,
                    "data":         result.response.data,
                    "retryable":    result.retryable,
                },
                "error_code":    result.response.error_code,
                "error_message": result.response.error_message,
            }

        except Exception as exc:
            LOGGER.exception(
                "adapter_backed.run internal error action_type=%s case_id=%s error=%s",
                action_type, case_id, exc,
            )
            return {
                "success":       False,
                "response_data": {},
                "error_code":    "ADAPTER_BACKED_INTERNAL_ERROR",
                "error_message": f"{type(exc).__name__}: {exc}",
            }


# ── Routing table access ──────────────────────────────────────────────────────

def get_adapter_for_action(action_type: str) -> tuple[AdapterType, AdapterOperation]:
    """
    Return the (AdapterType, AdapterOperation) for a given action_type.

    Returns _DEFAULT_ROUTING for unknown action types.
    """
    return _ACTION_ROUTING_TABLE.get(action_type, _DEFAULT_ROUTING)


def list_routable_action_types() -> list[str]:
    """Return all action types that have explicit routing entries."""
    return list(_ACTION_ROUTING_TABLE.keys())
