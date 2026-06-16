"""
case_engine/execution/executor.py

Sprint 2.23: Action Executor -- adapter-based execution layer.

Flow: ActionExecutor.execute() --> ExecutionAdapter.run() --> ExecutionResult

The ExecutionAdapter is an interface. Currently only MockExecutionAdapter
is implemented. Real adapters (Freshdesk, AdminPortal, Asana) are future work
(blueprint Section 33 -- Future Integrations).

Design:
- No external API calls in this module.
- MockExecutionAdapter is deterministic and controlled by action_type.
- ActionExecutor.execute() never raises -- all failures captured in ExecutionResult.
"""
from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from case_engine.execution.models import ExecutionResult, ExecutionStatus

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── ExecutionAdapter interface ────────────────────────────────────────────────

class ExecutionAdapter(ABC):
    """
    Interface for all action execution backends.

    Current: MockExecutionAdapter (Sprint 2.23)
    Future:  FreshdeskAdapter, AdminPortalAdapter, AsanaAdapter (Section 33)
    """

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique identifier for this adapter."""

    @abstractmethod
    def run(
        self,
        action_type: str,
        action_params: dict[str, Any],
        case_id: str,
    ) -> dict[str, Any]:
        """
        Execute the action and return a raw response dict.

        Must never raise.
        Returns: {"success": bool, "response_data": dict,
                  "error_code": str|None, "error_message": str|None}
        """


# ── MockExecutionAdapter ──────────────────────────────────────────────────────

_ALWAYS_FAIL_ACTIONS: frozenset[str] = frozenset({
    "simulate_failure",
    "fail_for_test",
})


class MockExecutionAdapter(ExecutionAdapter):
    """
    Deterministic mock adapter for testing and offline execution.

    Behavior:
    - Actions in _ALWAYS_FAIL_ACTIONS --> FAILED
    - All other actions --> SUCCESS (default happy path)
    - Never makes any external API call.
    """

    @property
    def adapter_name(self) -> str:
        return "mock"

    def run(
        self,
        action_type: str,
        action_params: dict[str, Any],
        case_id: str,
    ) -> dict[str, Any]:
        try:
            if action_type in _ALWAYS_FAIL_ACTIONS:
                return {
                    "success": False,
                    "response_data": {},
                    "error_code": "MOCK_FORCED_FAILURE",
                    "error_message": f"MockExecutionAdapter: {action_type} always fails",
                }
            return {
                "success": True,
                "response_data": {
                    "mock": True,
                    "action_type": action_type,
                    "case_id": case_id,
                },
                "error_code": None,
                "error_message": None,
            }
        except Exception as exc:  # pragma: no cover
            return {
                "success": False,
                "response_data": {},
                "error_code": "ADAPTER_INTERNAL_ERROR",
                "error_message": str(exc),
            }


# ── ActionExecutor ────────────────────────────────────────────────────────────

class ActionExecutor:
    """
    Executes an action via the registered adapter.

    Never raises -- all failures are captured in the returned ExecutionResult.
    """

    def __init__(self, adapter: ExecutionAdapter | None = None) -> None:
        self._adapter: ExecutionAdapter = adapter or MockExecutionAdapter()

    @property
    def adapter_name(self) -> str:
        return self._adapter.adapter_name

    def execute(
        self,
        action_type: str,
        action_params: dict[str, Any],
        case_id: str = "",
    ) -> ExecutionResult:
        """
        Execute the action and return an ExecutionResult.

        Never raises. Returns FAILED result on any internal error.
        """
        result_id = _new_id()
        started_ms = int(time.monotonic() * 1000)

        try:
            LOGGER.info(
                "action_executor.execute action_type=%s case_id=%s adapter=%s",
                action_type, case_id, self._adapter.adapter_name,
            )
            response = self._adapter.run(action_type, action_params, case_id)
            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)

            success = bool(response.get("success", False))
            status = ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED

            return ExecutionResult(
                result_id=result_id,
                adapter_name=self._adapter.adapter_name,
                action_type=action_type,
                action_params=action_params,
                status=status,
                success=success,
                response_data=response.get("response_data") or {},
                error_code=response.get("error_code"),
                error_message=response.get("error_message"),
                executed_at=_now_iso(),
                duration_ms=elapsed,
            )

        except Exception as exc:
            elapsed = max(0, int(time.monotonic() * 1000) - started_ms)
            LOGGER.exception(
                "action_executor.execute internal error action_type=%s case_id=%s error=%s",
                action_type, case_id, exc,
            )
            return ExecutionResult(
                result_id=result_id,
                adapter_name=self._adapter.adapter_name,
                action_type=action_type,
                action_params=action_params,
                status=ExecutionStatus.FAILED,
                success=False,
                response_data={},
                error_code="EXECUTOR_INTERNAL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                executed_at=_now_iso(),
                duration_ms=elapsed,
            )


def build_action_executor(adapter: ExecutionAdapter | None = None) -> ActionExecutor:
    """Factory function for ActionExecutor."""
    return ActionExecutor(adapter=adapter)
