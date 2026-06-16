"""
case_engine/adapters/monitoring_adapter.py

Sprint 2.27: MonitoringAdapter — placeholder implementation.

Covers monitoring/observability operations:
  READ    — fetch metrics, alert status
  WRITE   — emit metrics, log events
  EXECUTE — trigger alert rules, flush metric buffers

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
    AdapterOperation.EXECUTE,
})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class MonitoringAdapter(Adapter):
    """
    Placeholder monitoring/observability adapter.

    Simulates metrics emission and alert management without making any HTTP calls.
    Supports: READ, WRITE, EXECUTE.

    Sprint 2.28 will replace this with real monitoring backend (Prometheus/Grafana/Datadog).
    """

    @property
    def adapter_name(self) -> str:
        return "monitoring-placeholder"

    @property
    def adapter_type(self) -> AdapterType:
        return AdapterType.MONITORING

    def supported_operations(self) -> frozenset[AdapterOperation]:
        return _SUPPORTED_OPS

    def execute(self, request: AdapterRequest) -> AdapterResponse:
        """
        Execute a monitoring operation and return a deterministic placeholder response.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            LOGGER.debug(
                "monitoring_adapter.execute op=%s case_id=%s",
                request.operation.value, request.case_id,
            )

            if not self.supports(request.operation):
                return AdapterResponse.unsupported(
                    request_id=request.request_id,
                    adapter_type=AdapterType.MONITORING,
                    operation=request.operation,
                )

            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            data = self._build_response_data(request)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.MONITORING,
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
            LOGGER.exception("monitoring_adapter.execute internal error: %s", exc)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.MONITORING,
                operation=request.operation,
                status=AdapterStatus.FAILED,
                data={},
                error_code="MONITORING_INTERNAL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                duration_ms=elapsed,
                responded_at=_now_iso(),
            )

    def health_check(self) -> dict[str, Any]:
        """Return placeholder health status. Never raises."""
        try:
            return {
                "healthy":         True,
                "adapter":         self.adapter_name,
                "type":            self.adapter_type.value,
                "mode":            "placeholder",
                "supported_ops":   [op.value for op in _SUPPORTED_OPS],
                "backend":         None,
                "http_configured": False,
            }
        except Exception as exc:  # pragma: no cover
            return {"healthy": False, "adapter": "monitoring-placeholder", "error": str(exc)}

    def _build_response_data(self, request: AdapterRequest) -> dict[str, Any]:
        """Build deterministic response data per operation type."""
        op = request.operation
        if op == AdapterOperation.WRITE:
            return {
                "metric_name":  request.payload.get("metric_name", "kwikid.placeholder"),
                "value":        request.payload.get("value", 1),
                "labels":       request.payload.get("labels", {}),
                "emitted_at":   _now_iso(),
                "mock":         True,
            }
        if op == AdapterOperation.READ:
            return {
                "metric_name":  request.payload.get("metric_name", "kwikid.placeholder"),
                "value":        0,
                "timestamp":    _now_iso(),
                "mock":         True,
            }
        if op == AdapterOperation.EXECUTE:
            return {
                "rule":         request.payload.get("rule", "default"),
                "action":       "alert_rule_evaluated",
                "fired":        False,
                "mock":         True,
            }
        return {"mock": True, "operation": op.value}
