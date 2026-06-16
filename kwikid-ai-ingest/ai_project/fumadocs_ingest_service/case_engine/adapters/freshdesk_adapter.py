"""
case_engine/adapters/freshdesk_adapter.py

Sprint 2.27: FreshdeskAdapter — placeholder implementation.

Covers Freshdesk operations:
  READ   — fetch ticket details, customer data
  WRITE  — post internal notes, update ticket fields
  UPDATE — update ticket status (closed, pending, etc.)
  CREATE — create tickets, sub-tickets

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


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class FreshdeskAdapter(Adapter):
    """
    Placeholder Freshdesk adapter.

    Simulates Freshdesk ticket management without making any HTTP calls.
    Supports: READ, WRITE, UPDATE, CREATE, EXECUTE (OTP resend + ticket actions).

    Sprint 2.28 will replace this with real FreshdeskHTTPAdapter.
    """

    @property
    def adapter_name(self) -> str:
        return "freshdesk-placeholder"

    @property
    def adapter_type(self) -> AdapterType:
        return AdapterType.FRESHDESK

    def supported_operations(self) -> frozenset[AdapterOperation]:
        return _SUPPORTED_OPS

    def execute(self, request: AdapterRequest) -> AdapterResponse:
        """
        Execute a Freshdesk operation and return a deterministic placeholder response.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            LOGGER.debug(
                "freshdesk_adapter.execute op=%s case_id=%s action_type=%s",
                request.operation.value, request.case_id, request.action_type,
            )

            if not self.supports(request.operation):
                return AdapterResponse.unsupported(
                    request_id=request.request_id,
                    adapter_type=AdapterType.FRESHDESK,
                    operation=request.operation,
                )

            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            data = self._build_response_data(request)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.FRESHDESK,
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
            LOGGER.exception("freshdesk_adapter.execute internal error: %s", exc)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.FRESHDESK,
                operation=request.operation,
                status=AdapterStatus.FAILED,
                data={},
                error_code="FRESHDESK_INTERNAL_ERROR",
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
                "http_configured":  False,
            }
        except Exception as exc:  # pragma: no cover
            return {"healthy": False, "adapter": "freshdesk-placeholder", "error": str(exc)}

    def _build_response_data(self, request: AdapterRequest) -> dict[str, Any]:
        """Build deterministic response data per operation type."""
        op = request.operation
        if op == AdapterOperation.READ:
            return {
                "ticket_id":     request.payload.get("ticket_id", "TICKET-PLACEHOLDER"),
                "status":        "open",
                "subject":       "Placeholder ticket",
                "requester":     request.payload.get("requester", "customer@example.com"),
                "mock":          True,
            }
        if op == AdapterOperation.WRITE:
            return {
                "note_id":       _new_id(),
                "ticket_id":     request.payload.get("ticket_id", "TICKET-PLACEHOLDER"),
                "type":          "internal_note",
                "body_preview":  str(request.payload.get("body", ""))[:100],
                "mock":          True,
            }
        if op == AdapterOperation.UPDATE:
            return {
                "ticket_id":     request.payload.get("ticket_id", "TICKET-PLACEHOLDER"),
                "field_updated": request.payload.get("field", "status"),
                "new_value":     request.payload.get("value", "resolved"),
                "mock":          True,
            }
        if op == AdapterOperation.CREATE:
            return {
                "ticket_id":     f"TICKET-{_new_id()[:8].upper()}",
                "subject":       request.payload.get("subject", "Placeholder ticket"),
                "mock":          True,
            }
        if op == AdapterOperation.EXECUTE:
            return {
                "action":        request.action_type,
                "ticket_id":     request.payload.get("ticket_id", "TICKET-PLACEHOLDER"),
                "executed":      True,
                "mock":          True,
            }
        return {"mock": True, "operation": op.value}
