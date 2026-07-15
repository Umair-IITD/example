"""
case_engine/tools/adapters — Real (non-mock) BaseTool implementations that
bridge external systems into the Sprint 2.17 / 2.45 Tool Framework.

Sprint 2.50 additions:
    MetricTool        — canonical METRICTOOL adapter over Uptime Kuma
    ServerTool        — canonical SERVERTOOL adapter over Uptime Kuma

    register_metrics_tools(registry)
                      — helper that registers both adapters + their
                        capability mappings against a ProductionToolRegistry
                        (or the classic ToolRegistry).
"""
from case_engine.tools.adapters.metrics_tool import (
    MetricTool,
    ServerTool,
    build_metric_tool,
    build_server_tool,
    register_metrics_tools,
)
from case_engine.tools.adapters.unity_tools import (
    GetCaseHistoryTool as UnityGetCaseHistoryTool,
    GetFailureReasonTool as UnityGetFailureReasonTool,
    GetOnboardingStatusTool as UnityGetOnboardingStatusTool,
    GetSessionDetailsTool as UnityGetSessionDetailsTool,
    GetUserDetailsTool as UnityGetUserDetailsTool,
    register_unity_tools,
)

__all__ = [
    # Sprint 2.50 metrics
    "MetricTool",
    "ServerTool",
    "build_metric_tool",
    "build_server_tool",
    "register_metrics_tools",
    # Sprint 2.51 unity (production replacements for the Sprint 2.17 mocks)
    "UnityGetSessionDetailsTool",
    "UnityGetUserDetailsTool",
    "UnityGetFailureReasonTool",
    "UnityGetCaseHistoryTool",
    "UnityGetOnboardingStatusTool",
    "register_unity_tools",
]
