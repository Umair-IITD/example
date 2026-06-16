"""
case_engine/reasoning/reasoning_models.py

Sprint 2.17: Reasoning domain models.

ReasoningContext: all state the reasoning engine can observe.
ReasoningDecision: what the reasoning engine concludes should happen next.
ReasoningStep: individual reasoning conclusion at one analysis step.

Design:
- All models are plain dataclasses. No LLM coupling.
- ReasoningContext carries a snapshot of case state — it is NOT mutated.
- ReasoningDecision carries a typed next_step, not free-form text.
  This prevents LLM hallucination when LLM integration is added later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from case_engine.models import Case
    from case_engine.slot_filling.models import SlotValue
    from case_engine.tools.tool_models import ToolResult
    from case_engine.workflows.models import WorkflowExecutionResult


class NextStepType(str, Enum):
    """
    Typed enumeration of next-step recommendations from the reasoning engine.

    This replaces free-form text to keep reasoning outputs machine-interpretable.
    LLM integration (future sprint) will map its output onto these values.
    """
    ASK_FOR_SLOT         = "ASK_FOR_SLOT"          # More info needed from user
    RUN_TOOL             = "RUN_TOOL"               # Invoke an investigation tool
    PROPOSE_ACTION       = "PROPOSE_ACTION"          # Ready to propose an action
    ESCALATE             = "ESCALATE"               # Cannot resolve automatically
    WAIT_FOR_ACTION      = "WAIT_FOR_ACTION"        # Pending action approval
    WORKFLOW_COMPLETE    = "WORKFLOW_COMPLETE"       # Case is resolved


@dataclass(frozen=True)
class ReasoningStep:
    """
    One analysis step performed by the reasoning engine.

    Used to build an audit trail of reasoning decisions.
    """
    step_name:   str
    observation: str
    conclusion:  str
    confidence:  float  # 0.0 – 1.0


@dataclass
class ReasoningContext:
    """
    Snapshot of all observable state for one reasoning pass.

    Contains:
    - case: the Case object (read-only — do NOT mutate)
    - slot_values: current slot fill state
    - workflow_result: current WorkflowExecutionResult (if workflow is active)
    - tool_results: results from investigation tools run so far
    - conversation_history: recent messages from the user
    """
    case:                 "Case"
    slot_values:          dict[str, "SlotValue"] = field(default_factory=dict)
    workflow_result:      "WorkflowExecutionResult | None" = None
    tool_results:         list["ToolResult"] = field(default_factory=list)
    conversation_history: list[dict[str, Any]] = field(default_factory=list)

    def slot_names_filled(self) -> list[str]:
        """Return names of all slots with FILLED status."""
        from case_engine.slot_filling.models import SlotStatus
        return [
            name for name, sv in self.slot_values.items()
            if sv.status == SlotStatus.FILLED
        ]

    def slot_names_missing(self) -> list[str]:
        """Return names of all slots that are not yet FILLED."""
        from case_engine.slot_filling.models import SlotStatus
        return [
            name for name, sv in self.slot_values.items()
            if sv.status != SlotStatus.FILLED
        ]

    def latest_tool_result(self, tool_name: str) -> "ToolResult | None":
        """Return the most recent successful result for the given tool name."""
        for result in reversed(self.tool_results):
            if result.tool_name == tool_name and result.success:
                return result
        return None

    def all_tools_run(self, tool_names: list[str]) -> bool:
        """Return True if all listed tools have a successful result."""
        run_tools = {r.tool_name for r in self.tool_results if r.success}
        return all(name in run_tools for name in tool_names)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id":            self.case.case_id,
            "topic":              self.case.topic,
            "case_state":         self.case.current_state.value,
            "workflow_state":     self.case.workflow_state,
            "slots_filled":       self.slot_names_filled(),
            "slots_missing":      self.slot_names_missing(),
            "tool_results_count": len(self.tool_results),
            "tools_run":          [r.tool_name for r in self.tool_results if r.success],
        }


@dataclass
class ReasoningDecision:
    """
    The reasoning engine's conclusion for one analysis pass.

    next_step        : typed enum value (what to do next)
    confidence       : 0.0-1.0 confidence in this decision (1.0 for deterministic rules)
    rationale        : human-readable explanation for logging / audit
    tool_name        : if next_step == RUN_TOOL, which tool to run
    slot_name        : if next_step == ASK_FOR_SLOT, which slot to ask for
    missing_slots    : all slots missing (for diagnostics)
    reasoning_steps  : ordered list of intermediate conclusions
    """
    next_step:       NextStepType
    confidence:      float
    rationale:       str
    tool_name:       str | None = None
    slot_name:       str | None = None
    missing_slots:   list[str] = field(default_factory=list)
    reasoning_steps: list[ReasoningStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "next_step":       self.next_step.value,
            "confidence":      self.confidence,
            "rationale":       self.rationale,
            "tool_name":       self.tool_name,
            "slot_name":       self.slot_name,
            "missing_slots":   self.missing_slots,
            "reasoning_steps": [
                {"step": s.step_name, "observation": s.observation, "conclusion": s.conclusion}
                for s in self.reasoning_steps
            ],
        }
