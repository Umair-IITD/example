"""
case_engine/runtime

Sprint 2.27.5: Support Agent Runtime — THE AGENT.

Exposes SupportAgentRuntime as the single cohesive entry point for
case-level processing. All caller code should use run_case() rather
than calling individual services.

Blueprint: ONE AGENT — single cohesive entry point.
"""
from case_engine.runtime.agent_models import (
    AgentExecutionResult,
    AgentStatus,
)
from case_engine.runtime.support_agent_runtime import (
    SupportAgentRuntime,
    build_support_agent_runtime,
)

__all__ = [
    "AgentExecutionResult",
    "AgentStatus",
    "SupportAgentRuntime",
    "build_support_agent_runtime",
]
