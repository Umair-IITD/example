"""
case_engine/adapters/models.py

Sprint 2.27: Adapter Domain Models.

Adapters represent external system integrations (Freshdesk, Admin Portal,
Asana, Monitoring). This module defines the data transfer objects for
adapter requests, responses, and execution results.

All models:
  - frozen dataclasses (immutable)
  - JSON serializable via to_dict() / from_dict()
  - Never raise on construction
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── AdapterType ───────────────────────────────────────────────────────────────

class AdapterType(str, Enum):
    """
    Target external system for an adapter request.

    FRESHDESK    — Ticket management, notes, customer replies, ticket closure
    ADMIN_PORTAL — KYC admin actions: session reset, onboarding, manual overrides
    ASANA        — L2 engineering ticket creation and status tracking
    MONITORING   — Metrics emission, alerting, health reporting
    """
    FRESHDESK    = "FRESHDESK"
    ADMIN_PORTAL = "ADMIN_PORTAL"
    ASANA        = "ASANA"
    MONITORING   = "MONITORING"


# ── AdapterOperation ──────────────────────────────────────────────────────────

class AdapterOperation(str, Enum):
    """
    Operation type for an adapter request.

    READ    — fetch or retrieve data (idempotent)
    WRITE   — emit or push data (non-idempotent)
    UPDATE  — modify existing resource
    CREATE  — create new resource
    SEARCH  — query/filter data
    EXECUTE — trigger an action (e.g., session reset, OTP resend)
    """
    READ    = "READ"
    WRITE   = "WRITE"
    UPDATE  = "UPDATE"
    CREATE  = "CREATE"
    SEARCH  = "SEARCH"
    EXECUTE = "EXECUTE"


# ── AdapterStatus ─────────────────────────────────────────────────────────────

class AdapterStatus(str, Enum):
    """
    Result status of an adapter operation.

    SUCCESS     — adapter confirmed operation completed
    FAILED      — adapter confirmed operation failed (non-retryable)
    RETRYABLE   — transient failure; caller should retry with backoff
    BLOCKED     — operation not allowed (unknown adapter, unsupported op, blocked by policy)
    UNSUPPORTED — adapter does not support this operation type
    """
    SUCCESS     = "SUCCESS"
    FAILED      = "FAILED"
    RETRYABLE   = "RETRYABLE"
    BLOCKED     = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


# ── AdapterRequest ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdapterRequest:
    """
    Request to an external adapter.

    Created by AdapterRouter callers (e.g., AdapterBackedExecutionAdapter).
    Routed by AdapterRouter to the correct Adapter.
    """
    request_id:   str
    adapter_type: AdapterType
    operation:    AdapterOperation
    payload:      dict[str, Any]
    case_id:      str
    action_type:  str
    metadata:     dict[str, Any]

    @classmethod
    def create(
        cls,
        adapter_type: AdapterType,
        operation:    AdapterOperation,
        payload:      dict[str, Any],
        case_id:      str = "",
        action_type:  str = "",
        metadata:     dict[str, Any] | None = None,
    ) -> "AdapterRequest":
        """Factory: create a new AdapterRequest with a generated request_id."""
        return cls(
            request_id=_new_id(),
            adapter_type=adapter_type,
            operation=operation,
            payload=dict(payload),
            case_id=case_id,
            action_type=action_type,
            metadata=dict(metadata) if metadata else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id":   self.request_id,
            "adapter_type": self.adapter_type.value,
            "operation":    self.operation.value,
            "payload":      self.payload,
            "case_id":      self.case_id,
            "action_type":  self.action_type,
            "metadata":     self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AdapterRequest":
        return cls(
            request_id=d.get("request_id", _new_id()),
            adapter_type=AdapterType(d.get("adapter_type", AdapterType.FRESHDESK.value)),
            operation=AdapterOperation(d.get("operation", AdapterOperation.EXECUTE.value)),
            payload=d.get("payload") or {},
            case_id=d.get("case_id", ""),
            action_type=d.get("action_type", ""),
            metadata=d.get("metadata") or {},
        )


# ── AdapterResponse ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdapterResponse:
    """
    Response from an adapter execution.

    Returned by Adapter.execute(). Contains the operation outcome.
    """
    response_id:   str
    request_id:    str
    adapter_type:  AdapterType
    operation:     AdapterOperation
    status:        AdapterStatus
    data:          dict[str, Any]
    error_code:    str | None
    error_message: str | None
    duration_ms:   int
    responded_at:  str

    def is_success(self) -> bool:
        return self.status == AdapterStatus.SUCCESS

    def is_retryable(self) -> bool:
        return self.status == AdapterStatus.RETRYABLE

    def is_blocked(self) -> bool:
        return self.status in (AdapterStatus.BLOCKED, AdapterStatus.UNSUPPORTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_id":   self.response_id,
            "request_id":    self.request_id,
            "adapter_type":  self.adapter_type.value,
            "operation":     self.operation.value,
            "status":        self.status.value,
            "data":          self.data,
            "error_code":    self.error_code,
            "error_message": self.error_message,
            "duration_ms":   self.duration_ms,
            "responded_at":  self.responded_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AdapterResponse":
        return cls(
            response_id=d.get("response_id", _new_id()),
            request_id=d.get("request_id", ""),
            adapter_type=AdapterType(d.get("adapter_type", AdapterType.FRESHDESK.value)),
            operation=AdapterOperation(d.get("operation", AdapterOperation.EXECUTE.value)),
            status=AdapterStatus(d.get("status", AdapterStatus.FAILED.value)),
            data=d.get("data") or {},
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            duration_ms=d.get("duration_ms", 0),
            responded_at=d.get("responded_at", _now_iso()),
        )

    @classmethod
    def blocked(
        cls,
        request_id:    str,
        adapter_type:  AdapterType,
        operation:     AdapterOperation,
        reason:        str,
    ) -> "AdapterResponse":
        """Factory: return a BLOCKED response."""
        return cls(
            response_id=_new_id(),
            request_id=request_id,
            adapter_type=adapter_type,
            operation=operation,
            status=AdapterStatus.BLOCKED,
            data={},
            error_code="ADAPTER_BLOCKED",
            error_message=reason,
            duration_ms=0,
            responded_at=_now_iso(),
        )

    @classmethod
    def unsupported(
        cls,
        request_id:   str,
        adapter_type: AdapterType,
        operation:    AdapterOperation,
    ) -> "AdapterResponse":
        """Factory: return an UNSUPPORTED response."""
        return cls(
            response_id=_new_id(),
            request_id=request_id,
            adapter_type=adapter_type,
            operation=operation,
            status=AdapterStatus.UNSUPPORTED,
            data={},
            error_code="OPERATION_UNSUPPORTED",
            error_message=f"Adapter does not support operation: {operation.value}",
            duration_ms=0,
            responded_at=_now_iso(),
        )


# ── AdapterExecutionResult ────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdapterExecutionResult:
    """
    Complete result of routing and executing one AdapterRequest.

    Produced by AdapterRouter.route(). Contains both the original request
    and the response, plus a summary success/retryable flag.
    """
    result_id:    str
    request:      AdapterRequest
    response:     AdapterResponse
    adapter_name: str
    success:      bool
    retryable:    bool
    executed_at:  str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":    self.result_id,
            "request":      self.request.to_dict(),
            "response":     self.response.to_dict(),
            "adapter_name": self.adapter_name,
            "success":      self.success,
            "retryable":    self.retryable,
            "executed_at":  self.executed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AdapterExecutionResult":
        return cls(
            result_id=d.get("result_id", _new_id()),
            request=AdapterRequest.from_dict(d["request"]),
            response=AdapterResponse.from_dict(d["response"]),
            adapter_name=d.get("adapter_name", "unknown"),
            success=d.get("success", False),
            retryable=d.get("retryable", False),
            executed_at=d.get("executed_at", _now_iso()),
        )

    @classmethod
    def from_response(
        cls,
        request:      AdapterRequest,
        response:     AdapterResponse,
        adapter_name: str,
    ) -> "AdapterExecutionResult":
        """Factory: build result from a completed request/response pair."""
        return cls(
            result_id=_new_id(),
            request=request,
            response=response,
            adapter_name=adapter_name,
            success=response.is_success(),
            retryable=response.is_retryable(),
            executed_at=_now_iso(),
        )
