"""
case_engine/tools/tool_executor.py

Sprint 2.17: BaseTool ABC and ToolExecutor.

BaseTool defines the contract all investigation tools must implement.
ToolExecutor dispatches invocations to registered tools and handles errors.

Design:
- No LLM. No async. No side effects beyond tool-specific calls.
- ToolExecutor never raises — returns ToolResult.fail on any exception.
- BaseTool.run() is the only entrypoint (no direct executor access).
- Input validation (required fields) happens in ToolExecutor before calling run().
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from case_engine.tools.tool_models import ToolDefinition, ToolInput, ToolResult

if TYPE_CHECKING:
    from case_engine.tools.tool_registry import ToolRegistry

LOGGER = logging.getLogger(__name__)


class BaseTool(ABC):
    """
    Abstract base for all investigation tools.

    Subclass and implement:
      - definition property: returns the ToolDefinition for this tool
      - run(inputs): performs the tool's work, returns output dict on success

    run() is NOT expected to handle its own exceptions — ToolExecutor wraps it.
    run() SHOULD return a plain dict on success.
    run() SHOULD raise on failure — ToolExecutor catches and wraps the exception.
    """

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the static ToolDefinition for this tool."""
        ...

    @abstractmethod
    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the tool and return a payload dict.

        Inputs are pre-validated by ToolExecutor before run() is called.
        Raise any exception on failure — ToolExecutor will wrap it in ToolResult.fail.
        """
        ...


class ToolExecutor:
    """
    Dispatches tool invocations to registered BaseTool implementations.

    Handles:
    - Input validation (required fields present)
    - Timing (records duration_ms)
    - Exception containment (never propagates — returns ToolResult.fail)
    - Logging of all invocations

    Never raises.
    """

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    def execute(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        *,
        requested_by: str = "system",
    ) -> ToolResult:
        """
        Execute a named tool with the given inputs.

        1. Look up tool in registry.
        2. Validate required inputs are present.
        3. Run tool, measuring duration.
        4. Return ToolResult.ok or ToolResult.fail.

        Never raises.
        """
        tool_input = ToolInput(
            tool_name=tool_name,
            inputs=inputs,
            requested_by=requested_by,
        )

        LOGGER.info(
            "tool_executor.execute tool=%s invocation=%s requested_by=%s",
            tool_name, tool_input.invocation_id, requested_by,
        )

        tool = self._registry.get(tool_name)
        if tool is None:
            LOGGER.warning("tool_executor: unknown tool '%s'", tool_name)
            return ToolResult.fail(
                tool_name=tool_name,
                error_code="TOOL_NOT_FOUND",
                error_message=f"No tool registered with name '{tool_name}'",
                invocation_id=tool_input.invocation_id,
            )

        # Validate required inputs
        missing = [
            req for req in tool.definition.required_inputs
            if req not in inputs
        ]
        if missing:
            LOGGER.warning(
                "tool_executor: missing required inputs for tool=%s missing=%s",
                tool_name, missing,
            )
            return ToolResult.fail(
                tool_name=tool_name,
                error_code="MISSING_REQUIRED_INPUTS",
                error_message=f"Missing required inputs: {missing}",
                invocation_id=tool_input.invocation_id,
            )

        # Execute
        start_ms = int(time.monotonic() * 1000)
        try:
            payload = tool.run(inputs)
            duration_ms = int(time.monotonic() * 1000) - start_ms

            LOGGER.info(
                "tool_executor.success tool=%s invocation=%s duration_ms=%d",
                tool_name, tool_input.invocation_id, duration_ms,
            )
            return ToolResult.ok(
                tool_name=tool_name,
                payload=payload,
                invocation_id=tool_input.invocation_id,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = int(time.monotonic() * 1000) - start_ms
            LOGGER.exception(
                "tool_executor.error tool=%s invocation=%s error=%s duration_ms=%d",
                tool_name, tool_input.invocation_id, exc, duration_ms,
            )
            return ToolResult.fail(
                tool_name=tool_name,
                error_code="TOOL_EXECUTION_ERROR",
                error_message=str(exc)[:500],
                invocation_id=tool_input.invocation_id,
            )
