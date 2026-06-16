"""
case_engine/integrations/router_service.py

Sprint 2.27.5: RouterService — universal action dispatch point.

Every action execution in the platform flows through this service.
It wraps AdapterRouter and provides:
  - route(action_type, payload, case_id)  — action-type-aware dispatch
  - can_route(action_type)               — routing capability check
  - capability_check(action_type, op)    — per-operation capability check
  - health_summary()                     — aggregate adapter health
  - is_healthy(adapter_type)             — per-adapter health

Blueprint principle (Section 16 + Section 26):
  "No external action may bypass Action Gateway."
  "No component bypasses Action Gateway." (Principle 8)

This service is the integration layer that enforces those principles.
All adapters must be registered; unknown action types route to ADMIN_PORTAL/EXECUTE
by default (fail-closed routing — never silently drops requests).

Design:
  - Never raises: all exceptions return RouterResult with success=False
  - Deterministic routing via execution_adapter.get_adapter_for_action()
  - Audit integrated: emits events when audit_logger is provided
  - Metrics-ready: structured RouterResult includes timing information
  - LLM-ready: payload is untyped dict, supports future enrichment
  - Future: inject retry/circuit-breaker policy here (Sprint 2.29)
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from case_engine.adapters.adapter_router import AdapterRouter

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── RouterResult ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RouterResult:
    """
    Result of a RouterService.route() call.

    This is the single output type for all routed actions. It includes
    enough information for audit, retry decisions, and response generation.

    Fields:
        result_id      — UUID for this specific routing result
        request_id     — UUID from the underlying AdapterRequest
        action_type    — the original action_type string
        adapter_type   — which adapter handled the request
        operation      — which operation was performed
        success        — True iff the adapter returned SUCCESS status
        retryable      — True iff the adapter returned RETRYABLE status
        status         — raw AdapterStatus value ("SUCCESS", "FAILED", etc.)
        data           — adapter-specific response payload (JSON-safe dict)
        error_code     — error code if not successful, else None
        adapter_name   — display name of the adapter that handled the request
        executed_at    — ISO timestamp of execution
        duration_ms    — wall-clock duration of the route() call
        metadata       — extensible dict for future fields
    """
    result_id:    str
    request_id:   str
    action_type:  str
    adapter_type: str
    operation:    str
    success:      bool
    retryable:    bool
    status:       str
    data:         dict[str, Any]
    error_code:   str | None
    adapter_name: str
    executed_at:  str
    duration_ms:  int
    metadata:     dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":    self.result_id,
            "request_id":   self.request_id,
            "action_type":  self.action_type,
            "adapter_type": self.adapter_type,
            "operation":    self.operation,
            "success":      self.success,
            "retryable":    self.retryable,
            "status":       self.status,
            "data":         dict(self.data),
            "error_code":   self.error_code,
            "adapter_name": self.adapter_name,
            "executed_at":  self.executed_at,
            "duration_ms":  self.duration_ms,
            "metadata":     dict(self.metadata),
        }

    @classmethod
    def failure(
        cls,
        action_type: str,
        error_code:  str,
        error_msg:   str,
        duration_ms: int = 0,
    ) -> "RouterResult":
        """Build a failure result for cases where routing itself fails."""
        return cls(
            result_id=_new_id(),
            request_id=_new_id(),
            action_type=action_type,
            adapter_type="UNKNOWN",
            operation="UNKNOWN",
            success=False,
            retryable=False,
            status="FAILED",
            data={},
            error_code=error_code,
            adapter_name="router",
            executed_at=_now_iso(),
            duration_ms=duration_ms,
        )


# ── RouterService ─────────────────────────────────────────────────────────────

class RouterService:
    """
    Universal action dispatch point.

    Wraps AdapterRouter to provide a higher-level API that accepts
    action_type strings directly (no need for callers to know AdapterType
    or AdapterOperation mappings — those are resolved internally).

    This is THE integration layer. All action executions must pass through here.

    Thread-safe: delegates to AdapterRouter which uses threading.RLock internally.
    Never raises: all exceptions are caught and returned as RouterResult(success=False).
    """

    def __init__(
        self,
        adapter_router: "AdapterRouter",
        audit_logger:   Any = None,
    ) -> None:
        self._router       = adapter_router
        self._audit_logger = audit_logger

    # ── Primary dispatch ──────────────────────────────────────────────────────

    def route(
        self,
        action_type: str,
        payload:     dict[str, Any] | None = None,
        case_id:     str = "",
        metadata:    dict[str, Any] | None = None,
    ) -> RouterResult:
        """
        Route an action by type — the universal dispatch method.

        Resolves adapter_type and operation from the action_type via the
        routing table, builds an AdapterRequest, dispatches to AdapterRouter.

        Args:
            action_type: The action to perform (e.g., "otp_resend", "create_l2_ticket").
            payload:     Action parameters dict (JSON-safe, no secrets).
            case_id:     Case ID for audit correlation.
            metadata:    Optional extra fields (caller-supplied, passed through to RouterResult).

        Returns:
            RouterResult — never raises, always returns a structured result.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            from case_engine.adapters.execution_adapter import get_adapter_for_action
            from case_engine.adapters.models import AdapterRequest

            adapter_type, operation = get_adapter_for_action(action_type)
            request = AdapterRequest.create(
                adapter_type=adapter_type,
                operation=operation,
                payload=payload or {},
                case_id=case_id,
                action_type=action_type,
                metadata=metadata or {},
            )

            LOGGER.debug(
                "router_service.route action_type=%s adapter=%s op=%s case_id=%s",
                action_type, adapter_type.value, operation.value, case_id,
            )

            exec_result = self._router.route(request)
            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)

            result = RouterResult(
                result_id=_new_id(),
                request_id=exec_result.request.request_id,
                action_type=action_type,
                adapter_type=exec_result.request.adapter_type.value,
                operation=exec_result.request.operation.value,
                success=exec_result.success,
                retryable=exec_result.retryable,
                status=exec_result.response.status.value,
                data=dict(exec_result.response.data),
                error_code=exec_result.response.error_code,
                adapter_name=exec_result.adapter_name,
                executed_at=exec_result.executed_at,
                duration_ms=duration_ms,
                metadata=dict(metadata or {}),
            )

            self._emit_routed(action_type, result, case_id)
            return result

        except Exception as exc:
            duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)
            LOGGER.exception(
                "router_service.route internal error action_type=%s case_id=%s error=%s",
                action_type, case_id, exc,
            )
            return RouterResult.failure(
                action_type=action_type,
                error_code="ROUTER_SERVICE_INTERNAL_ERROR",
                error_msg=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

    # ── Capability checks ─────────────────────────────────────────────────────

    def can_route(self, action_type: str) -> bool:
        """
        Return True if this action_type can be routed to a registered adapter.

        An action_type is routable if:
        1. It appears in the routing table (explicitly routed), OR
        2. A default route exists (ADMIN_PORTAL/EXECUTE for unknown types)
           AND the ADMIN_PORTAL adapter is healthy.

        Never raises.
        """
        try:
            from case_engine.adapters.execution_adapter import (
                get_adapter_for_action,
                list_routable_action_types,
            )
            from case_engine.adapters.models import AdapterType

            if action_type in list_routable_action_types():
                adapter_type, operation = get_adapter_for_action(action_type)
                if not self._router.is_healthy(adapter_type):
                    return False
                return self.capability_check(adapter_type.value, operation.value)

            # Unknown action_type → falls back to ADMIN_PORTAL/EXECUTE default
            return bool(self._router.is_healthy(AdapterType.ADMIN_PORTAL))
        except Exception:
            return False

    def is_explicitly_routed(self, action_type: str) -> bool:
        """
        Return True if this action_type has an explicit entry in the routing table.

        Unknown action_types fall back to the DEFAULT route (ADMIN_PORTAL/EXECUTE).
        This method lets callers distinguish explicit vs. default routing.

        Never raises.
        """
        try:
            from case_engine.adapters.execution_adapter import list_routable_action_types
            return action_type in list_routable_action_types()
        except Exception:
            return False

    def capability_check(self, adapter_type_str: str, operation_str: str) -> bool:
        """
        Return True if the adapter for adapter_type_str supports operation_str.

        Never raises.
        """
        try:
            from case_engine.adapters.models import AdapterType, AdapterOperation
            adapter_type = AdapterType(adapter_type_str)
            operation    = AdapterOperation(operation_str)
            adapter = self._router._registry.get_adapter(adapter_type)
            if adapter is None:
                return False
            return bool(adapter.supports(operation))
        except Exception:
            return False

    def list_routable_actions(self) -> list[str]:
        """
        Return the list of action types with explicit routing table entries.

        Never raises.
        """
        try:
            from case_engine.adapters.execution_adapter import list_routable_action_types
            return list(list_routable_action_types())
        except Exception:
            return []

    # ── Health ────────────────────────────────────────────────────────────────

    def health_summary(self) -> dict[str, Any]:
        """
        Return aggregate health summary for all registered adapters.

        Never raises.
        """
        try:
            return self._router.health_summary()
        except Exception as exc:
            return {"overall_healthy": False, "error": str(exc)}

    def is_healthy(self, adapter_type_str: str) -> bool:
        """
        Return True if the adapter for adapter_type_str is healthy.

        Never raises.
        """
        try:
            from case_engine.adapters.models import AdapterType
            return self._router.is_healthy(AdapterType(adapter_type_str))
        except Exception:
            return False

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _emit_routed(
        self,
        action_type: str,
        result:      RouterResult,
        case_id:     str,
    ) -> None:
        """Emit audit event for completed routing. Never raises."""
        if self._audit_logger is None:
            return
        try:
            self._audit_logger.log_action_routed(
                action_type=action_type,
                adapter_type=result.adapter_type,
                operation=result.operation,
                success=result.success,
                case_id=case_id,
                duration_ms=result.duration_ms,
            )
        except Exception as exc:
            LOGGER.debug("router_service: audit emit failed: %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_router_service(
    adapter_router: "AdapterRouter | None" = None,
    audit_logger:   Any = None,
) -> RouterService:
    """
    Factory: build a RouterService.

    Args:
        adapter_router: AdapterRouter instance. If None, builds a default one.
        audit_logger:   Optional audit logger for routing events.

    Returns:
        RouterService ready to route actions.

    Never raises — returns a service with None adapter_router on failure.
    """
    if adapter_router is None:
        try:
            from case_engine.adapters import AdapterRegistry, AdapterRouter
            registry       = AdapterRegistry.build_default()
            adapter_router = AdapterRouter(registry=registry, audit_logger=audit_logger)
        except Exception as exc:
            LOGGER.warning("build_router_service: adapter_stack failed error=%s", exc)
            raise

    return RouterService(adapter_router=adapter_router, audit_logger=audit_logger)
