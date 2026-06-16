"""
case_engine/adapters/adapter_router.py

Sprint 2.27: AdapterRouter — request routing and audit.

Responsibilities:
  - Receive AdapterRequest
  - Look up correct adapter in AdapterRegistry
  - Validate adapter exists (BLOCKED if not)
  - Validate adapter supports requested operation (UNSUPPORTED if not)
  - Call adapter.execute()
  - Emit audit events at start/completion/failure
  - Return AdapterExecutionResult

Design:
  - Never raises — all exceptions return FAILED AdapterExecutionResult
  - Unknown adapter type → BLOCKED status
  - Unsupported operation → UNSUPPORTED status
  - Audit logger is optional (no audit if None)
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.adapters.adapter_registry import AdapterRegistry
from case_engine.adapters.models import (
    AdapterExecutionResult,
    AdapterOperation,
    AdapterRequest,
    AdapterResponse,
    AdapterStatus,
    AdapterType,
)

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class AdapterRouter:
    """
    Routes AdapterRequests to the correct Adapter via the AdapterRegistry.

    Never raises. All exceptions are captured and returned as FAILED results.
    """

    def __init__(
        self,
        registry:     AdapterRegistry,
        audit_logger: "AuditLogger | None" = None,
    ) -> None:
        self._registry     = registry
        self._audit_logger = audit_logger

    # ── Primary routing method ────────────────────────────────────────────────

    def route(self, request: AdapterRequest) -> AdapterExecutionResult:
        """
        Route an AdapterRequest to the correct Adapter and return the result.

        Never raises. Returns BLOCKED if adapter not found.
        Returns UNSUPPORTED if adapter doesn't support the operation.
        Returns FAILED on any internal exception.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            self._emit_started(request)

            # Look up adapter
            adapter = self._registry.get_adapter(request.adapter_type)
            if adapter is None:
                reason = f"No adapter registered for type: {request.adapter_type.value}"
                LOGGER.warning("adapter_router.route: %s request_id=%s", reason, request.request_id)
                self._emit_routing_failed(request, reason)
                response = AdapterResponse.blocked(
                    request_id=request.request_id,
                    adapter_type=request.adapter_type,
                    operation=request.operation,
                    reason=reason,
                )
                return AdapterExecutionResult.from_response(request, response, "router")

            # Validate operation supported
            if not adapter.supports(request.operation):
                LOGGER.warning(
                    "adapter_router.route: adapter=%s does not support op=%s request_id=%s",
                    adapter.adapter_name, request.operation.value, request.request_id,
                )
                response = AdapterResponse.unsupported(
                    request_id=request.request_id,
                    adapter_type=request.adapter_type,
                    operation=request.operation,
                )
                elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
                self._emit_completed(request, response, adapter.adapter_name)
                return AdapterExecutionResult.from_response(request, response, adapter.adapter_name)

            # Execute
            LOGGER.debug(
                "adapter_router.route: routing to adapter=%s op=%s request_id=%s",
                adapter.adapter_name, request.operation.value, request.request_id,
            )
            response = adapter.execute(request)
            elapsed  = max(0, int(time.monotonic() * 1000) - started_ms)
            self._emit_completed(request, response, adapter.adapter_name)
            return AdapterExecutionResult.from_response(request, response, adapter.adapter_name)

        except Exception as exc:
            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            LOGGER.exception(
                "adapter_router.route internal error request_id=%s error=%s",
                request.request_id, exc,
            )
            response = AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=request.adapter_type,
                operation=request.operation,
                status=AdapterStatus.FAILED,
                data={},
                error_code="ROUTER_INTERNAL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                duration_ms=elapsed,
                responded_at=_now_iso(),
            )
            return AdapterExecutionResult.from_response(request, response, "router")

    # ── Health check ──────────────────────────────────────────────────────────

    def is_healthy(self, adapter_type: AdapterType) -> bool:
        """
        Return True if the adapter for the given type is healthy.

        Never raises.
        """
        try:
            adapter = self._registry.get_adapter(adapter_type)
            if adapter is None:
                return False
            h = adapter.health_check()
            return bool(h.get("healthy", False))
        except Exception:
            return False

    def health_summary(self) -> dict[str, Any]:
        """
        Return aggregate health summary for all registered adapters.

        Never raises.
        """
        try:
            return self._registry.health_summary()
        except Exception as exc:
            return {"overall_healthy": False, "error": str(exc)}

    # ── Audit emission ────────────────────────────────────────────────────────

    def _emit_started(self, request: AdapterRequest) -> None:
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log_adapter_request_started(
                adapter_type=request.adapter_type.value,
                operation=request.operation.value,
                request_id=request.request_id,
                case_id=request.case_id,
                action_type=request.action_type,
            )
        except Exception as exc:
            LOGGER.debug("adapter_router: audit start failed: %s", exc)

    def _emit_completed(
        self,
        request:      AdapterRequest,
        response:     AdapterResponse,
        adapter_name: str,
    ) -> None:
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log_adapter_request_completed(
                adapter_type=request.adapter_type.value,
                operation=request.operation.value,
                request_id=request.request_id,
                case_id=request.case_id,
                status=response.status.value,
                success=response.is_success(),
                duration_ms=response.duration_ms,
                adapter_name=adapter_name,
            )
        except Exception as exc:
            LOGGER.debug("adapter_router: audit complete failed: %s", exc)

    def _emit_routing_failed(self, request: AdapterRequest, reason: str) -> None:
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log_adapter_routing_failed(
                adapter_type=request.adapter_type.value,
                operation=request.operation.value,
                request_id=request.request_id,
                case_id=request.case_id,
                reason=reason,
            )
        except Exception as exc:
            LOGGER.debug("adapter_router: audit routing_failed failed: %s", exc)
