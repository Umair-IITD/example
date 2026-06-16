"""
case_engine/adapters/portal_adapter.py

Sprint 2.27: AdminPortalAdapter — placeholder implementation.

Covers KYC Admin Portal operations:
  READ    — fetch user/session/onboarding status
  WRITE   — push notifications, update records
  UPDATE  — modify session state, onboarding status
  CREATE  — create admin records, tasks
  EXECUTE — trigger actions: session reset, OTP resend, document re-upload

No HTTP calls. No credentials. Returns deterministic placeholder responses.
Purpose: prove runtime wiring works before Sprint 2.28 real integration.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.adapters.base import Adapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterResponse,
    AdapterStatus,
    AdapterType,
)

LOGGER = logging.getLogger(__name__)

_SUPPORTED_OPS: frozenset[AdapterOperation] = frozenset({
    AdapterOperation.READ,
    AdapterOperation.WRITE,
    AdapterOperation.UPDATE,
    AdapterOperation.CREATE,
    AdapterOperation.EXECUTE,
})

# Action types routed to Admin Portal
_PORTAL_ACTION_TYPES: frozenset[str] = frozenset({
    "vkyc_session_reset",
    "document_ocr_reprocess",
    "agent_session_refresh",
    "api_callback_retry",
})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class AdminPortalAdapter(Adapter):
    """
    Placeholder KYC Admin Portal adapter.

    Simulates admin portal API calls without making any HTTP calls.
    Handles all VKYC session, OCR, agent portal, and callback actions.

    Sprint 2.28 will replace this with real AdminPortalHTTPAdapter.
    """

    @property
    def adapter_name(self) -> str:
        return "admin-portal-placeholder"

    @property
    def adapter_type(self) -> AdapterType:
        return AdapterType.ADMIN_PORTAL

    def supported_operations(self) -> frozenset[AdapterOperation]:
        return _SUPPORTED_OPS

    def execute(self, request: AdapterRequest) -> AdapterResponse:
        """
        Execute an Admin Portal operation and return a deterministic placeholder response.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            LOGGER.debug(
                "portal_adapter.execute op=%s case_id=%s action_type=%s",
                request.operation.value, request.case_id, request.action_type,
            )

            if not self.supports(request.operation):
                return AdapterResponse.unsupported(
                    request_id=request.request_id,
                    adapter_type=AdapterType.ADMIN_PORTAL,
                    operation=request.operation,
                )

            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            data = self._build_response_data(request)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.ADMIN_PORTAL,
                operation=request.operation,
                status=AdapterStatus.SUCCESS,
                data=data,
                error_code=None,
                error_message=None,
                duration_ms=elapsed,
                responded_at=_now_iso(),
            )

        except Exception as exc:
            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            LOGGER.exception("portal_adapter.execute internal error: %s", exc)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.ADMIN_PORTAL,
                operation=request.operation,
                status=AdapterStatus.FAILED,
                data={},
                error_code="PORTAL_INTERNAL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                duration_ms=elapsed,
                responded_at=_now_iso(),
            )

    def health_check(self) -> dict[str, Any]:
        """Return placeholder health status. Never raises."""
        try:
            return {
                "healthy":          True,
                "adapter":          self.adapter_name,
                "type":             self.adapter_type.value,
                "mode":             "placeholder",
                "supported_ops":    [op.value for op in _SUPPORTED_OPS],
                "portal_url":       None,
                "http_configured":  False,
            }
        except Exception as exc:  # pragma: no cover
            return {"healthy": False, "adapter": "admin-portal-placeholder", "error": str(exc)}

    def _build_response_data(self, request: AdapterRequest) -> dict[str, Any]:
        """Build deterministic response data per action type and operation."""
        op          = request.operation
        action_type = request.action_type

        if op == AdapterOperation.EXECUTE:
            if action_type == "vkyc_session_reset":
                return {
                    "session_id":     request.payload.get("session_id", "SES-PLACEHOLDER"),
                    "action":         "session_reset",
                    "new_status":     "RESET",
                    "reset_token":    _new_id()[:8].upper(),
                    "mock":           True,
                }
            if action_type == "document_ocr_reprocess":
                return {
                    "document_type":  request.payload.get("document_type", "AADHAAR"),
                    "application_id": request.payload.get("application_id", "APP-PLACEHOLDER"),
                    "action":         "ocr_reprocess_queued",
                    "queue_id":       _new_id()[:8].upper(),
                    "mock":           True,
                }
            if action_type == "agent_session_refresh":
                return {
                    "agent_id":       request.payload.get("agent_id", "AGENT-PLACEHOLDER"),
                    "portal_type":    request.payload.get("portal_type", "VKYC"),
                    "action":         "session_refresh",
                    "new_session_id": _new_id()[:8].upper(),
                    "mock":           True,
                }
            if action_type == "api_callback_retry":
                return {
                    "callback_type":  request.payload.get("callback_type", "EKYC"),
                    "application_id": request.payload.get("application_id", "APP-PLACEHOLDER"),
                    "action":         "callback_retry_queued",
                    "retry_id":       _new_id()[:8].upper(),
                    "mock":           True,
                }
            return {
                "action_type": action_type,
                "action":      "executed",
                "mock":        True,
            }

        if op == AdapterOperation.READ:
            return {
                "resource":  request.payload.get("resource", "unknown"),
                "status":    "found",
                "mock":      True,
            }
        if op in (AdapterOperation.WRITE, AdapterOperation.UPDATE, AdapterOperation.CREATE):
            return {
                "resource_id": _new_id()[:8].upper(),
                "operation":   op.value,
                "mock":        True,
            }
        return {"mock": True, "operation": op.value}
