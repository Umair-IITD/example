"""
case_engine/tools/tool_registry.py

Sprint 2.17: ToolRegistry — central index of registered investigation tools.

Design:
- Tools are registered by name at startup (or on demand in tests).
- Registry is write-once per tool_name (re-registration replaces the previous entry).
- Thread-safe: after initial load, only reads occur from request handlers.
- Provider-agnostic: any BaseTool implementation can be registered.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from case_engine.tools.tool_models import ToolDefinition

if TYPE_CHECKING:
    from case_engine.tools.tool_executor import BaseTool

LOGGER = logging.getLogger(__name__)


class ToolRegistry:
    """
    Central registry of all registered BaseTool implementations.

    Usage:
      registry = ToolRegistry()
      registry.register(GetSessionDetailsTool())
      tool = registry.get("GetSessionDetailsTool")
      definitions = registry.list_tools()
    """

    def __init__(self) -> None:
        self._tools: dict[str, "BaseTool"] = {}

    def register(self, tool: "BaseTool") -> None:
        """
        Register a tool. If a tool with the same name already exists, it is replaced.
        """
        name = tool.definition.tool_name
        if name in self._tools:
            LOGGER.warning("tool_registry: replacing existing tool '%s'", name)
        self._tools[name] = tool
        LOGGER.info(
            "tool_registry: registered tool=%s version=%s tags=%s",
            name, tool.definition.version, list(tool.definition.tags),
        )

    def get(self, tool_name: str) -> "BaseTool | None":
        """Return the registered tool by name, or None."""
        return self._tools.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        """Return True if a tool with this name is registered."""
        return tool_name in self._tools

    def list_tools(self) -> list[ToolDefinition]:
        """Return definitions of all registered tools, sorted by tool_name."""
        return sorted(
            (t.definition for t in self._tools.values()),
            key=lambda d: d.tool_name,
        )

    def tool_names(self) -> list[str]:
        """Return all registered tool names, sorted."""
        return sorted(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    @classmethod
    def build_default(cls) -> "ToolRegistry":
        """
        Build and return a ToolRegistry pre-loaded with all mock investigation tools.

        Used by the application factory at startup.
        """
        from case_engine.tools.mock_tools import (
            GetCaseHistoryTool,
            GetFailureReasonTool,
            GetOnboardingStatusTool,
            GetSessionDetailsTool,
            GetUserDetailsTool,
        )
        registry = cls()
        registry.register(GetSessionDetailsTool())
        registry.register(GetUserDetailsTool())
        registry.register(GetFailureReasonTool())
        registry.register(GetCaseHistoryTool())
        registry.register(GetOnboardingStatusTool())
        LOGGER.info("tool_registry: default registry built with %d tools", len(registry))
        return registry
