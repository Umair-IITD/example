"""
case_engine/reasoning/reasoning_context.py

Sprint 2.17: ReasoningContext builder.

Provides factory functions for constructing ReasoningContext from
available case engine objects. Keeps construction logic out of the
ReasoningEngine and ReasoningContext dataclasses.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from case_engine.reasoning.reasoning_models import ReasoningContext

if TYPE_CHECKING:
    from case_engine.models import Case
    from case_engine.slot_filling.models import SlotValue
    from case_engine.tools.tool_models import ToolResult
    from case_engine.workflows.models import WorkflowExecutionResult


def build_context(
    case: "Case",
    slot_values: dict[str, "SlotValue"] | None = None,
    workflow_result: "WorkflowExecutionResult | None" = None,
    tool_results: list["ToolResult"] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
) -> ReasoningContext:
    """
    Build a ReasoningContext from case engine objects.

    slot_values may come from ClarificationEngine.slot_values_from_dict(case.slot_state)
    or from a dict passed in by the workflow engine.
    """
    return ReasoningContext(
        case=case,
        slot_values=slot_values or {},
        workflow_result=workflow_result,
        tool_results=tool_results or [],
        conversation_history=conversation_history or [],
    )


def build_context_from_case(case: "Case") -> ReasoningContext:
    """
    Build a minimal ReasoningContext from a Case object alone.

    Deserializes slot_state and workflow_context from the case.
    Used in API request handlers where only the case object is available.
    """
    from case_engine.clarification_engine import ClarificationEngine

    slot_values = ClarificationEngine.slot_values_from_dict(case.slot_state)

    workflow_result = None
    if case.workflow_context:
        from case_engine.workflows.models import WorkflowExecutionResult
        try:
            workflow_result = WorkflowExecutionResult.from_dict(case.workflow_context)
        except Exception:
            workflow_result = None

    return ReasoningContext(
        case=case,
        slot_values=slot_values,
        workflow_result=workflow_result,
        tool_results=[],
        conversation_history=[],
    )
