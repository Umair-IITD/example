"""
case_engine/adapters/asana_adapter.py

Sprint 2.27: AsanaAdapter — placeholder implementation.

Covers Asana L2 engineering ticket operations:
  READ   — fetch task/project status
  CREATE — create L2 engineering tasks
  UPDATE — update task status, add comments

No HTTP calls. No credentials. Returns deterministic placeholder responses.
Purpose: prove runtime wiring works before Sprint 2.28 real integration.

Blueprint reference: Section 28 — L2 Workflow (ASANA → DEV → FIXED → FDUPDATE)
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
    AdapterOperation.CREATE,
    AdapterOperation.UPDATE,
})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class AsanaAdapter(Adapter):
    """
    Placeholder Asana adapter for L2 engineering task management.

    Simulates Asana API calls without making any HTTP calls.
    Supports: READ, CREATE, UPDATE.

    Sprint 2.28 will replace this with real AsanaHTTPAdapter.
    """

    @property
    def adapter_name(self) -> str:
        return "asana-placeholder"

    @property
    def adapter_type(self) -> AdapterType:
        return AdapterType.ASANA

    def supported_operations(self) -> frozenset[AdapterOperation]:
        return _SUPPORTED_OPS

    def execute(self, request: AdapterRequest) -> AdapterResponse:
        """
        Execute an Asana operation and return a deterministic placeholder response.

        Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            LOGGER.debug(
                "asana_adapter.execute op=%s case_id=%s",
                request.operation.value, request.case_id,
            )

            if not self.supports(request.operation):
                return AdapterResponse.unsupported(
                    request_id=request.request_id,
                    adapter_type=AdapterType.ASANA,
                    operation=request.operation,
                )

            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            data = self._build_response_data(request)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.ASANA,
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
            LOGGER.exception("asana_adapter.execute internal error: %s", exc)
            return AdapterResponse(
                response_id=_new_id(),
                request_id=request.request_id,
                adapter_type=AdapterType.ASANA,
                operation=request.operation,
                status=AdapterStatus.FAILED,
                data={},
                error_code="ASANA_INTERNAL_ERROR",
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
                "workspace":       None,
                "http_configured": False,
            }
        except Exception as exc:  # pragma: no cover
            return {"healthy": False, "adapter": "asana-placeholder", "error": str(exc)}

    def _build_response_data(self, request: AdapterRequest) -> dict[str, Any]:
        """Build deterministic response data per operation type."""
        op = request.operation
        if op == AdapterOperation.CREATE:
            return {
                "task_id":     f"ASANA-{_new_id()[:8].upper()}",
                "project":     request.payload.get("project", "L2-ENGINEERING"),
                "title":       request.payload.get("title", "L2 ticket placeholder"),
                "assignee":    request.payload.get("assignee", "engineering-team"),
                "status":      "OPEN",
                "case_id":     request.case_id,
                "mock":        True,
            }
        if op == AdapterOperation.READ:
            return {
                "task_id":     request.payload.get("task_id", "ASANA-PLACEHOLDER"),
                "status":      "OPEN",
                "last_update": _now_iso(),
                "mock":        True,
            }
        if op == AdapterOperation.UPDATE:
            return {
                "task_id":     request.payload.get("task_id", "ASANA-PLACEHOLDER"),
                "field":       request.payload.get("field", "status"),
                "new_value":   request.payload.get("value", "IN_PROGRESS"),
                "mock":        True,
            }
        return {"mock": True, "operation": op.value}
