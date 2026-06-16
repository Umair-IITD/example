"""
case_engine/adapters/base.py

Sprint 2.27: Adapter Abstract Base Class.

All external system adapters (Freshdesk, Admin Portal, Asana, Monitoring)
must implement this interface. No vendor-specific logic lives here.

Design:
  - execute() must never raise — exceptions must be caught and returned
    as FAILED/RETRYABLE AdapterResponse objects.
  - health_check() must never raise — returns {"healthy": bool, ...}.
  - supported_operations() must return a non-empty frozenset.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from case_engine.adapters.models import AdapterOperation, AdapterResponse, AdapterRequest, AdapterType


class Adapter(ABC):
    """
    Abstract base for all external system adapters.

    Concrete implementations:
      FreshdeskAdapter   — Freshdesk ticket management
      AdminPortalAdapter — KYC admin portal actions
      AsanaAdapter       — Asana task management
      MonitoringAdapter  — Metrics and observability

    Contract guarantees:
      - execute() never raises
      - health_check() never raises
      - supported_operations() returns frozenset, never raises
    """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """
        Unique human-readable identifier for this adapter instance.
        E.g., "freshdesk-placeholder", "admin-portal-placeholder"
        """

    @property
    @abstractmethod
    def adapter_type(self) -> AdapterType:
        """The target external system this adapter connects to."""

    @abstractmethod
    def execute(self, request: AdapterRequest) -> AdapterResponse:
        """
        Execute the adapter request and return a response.

        Must never raise. Exceptions must be caught internally and returned
        as AdapterResponse with status=FAILED or RETRYABLE.

        Args:
            request: The AdapterRequest to execute.

        Returns:
            AdapterResponse — always (even on internal error).
        """

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """
        Return the current health status of this adapter.

        Must never raise.

        Returns:
            dict with at least: {"healthy": bool, "adapter": str, "type": str}
        """

    @abstractmethod
    def supported_operations(self) -> frozenset[AdapterOperation]:
        """
        Return the set of operations this adapter can handle.

        Must never raise. Must return a non-empty frozenset.
        """

    def supports(self, operation: AdapterOperation) -> bool:
        """
        Return True if this adapter supports the given operation.

        Never raises. Convenience wrapper over supported_operations().
        """
        try:
            return operation in self.supported_operations()
        except Exception:
            return False
