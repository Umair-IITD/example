"""
case_engine/reasoning/reasoning_engine.py

Sprint 2.17: ReasoningEngine — deterministic rule-based reasoning.

Implements the reasoning contract without LLM involvement.
All decisions are produced by explicit if/elif rules.

Future integration point: When LLM reasoning is added, this class
can be subclassed (LLMReasoningEngine) or wrapped (LLMReasoningEngineMixin)
without changing the public interface.

Rules implemented (deterministic):

1. Missing required slot → ASK_FOR_SLOT (highest priority)
2. Session ID present but GetSessionDetailsTool not run → RUN_TOOL
3. Failure reason unknown → RUN_TOOL (GetFailureReasonTool)
4. User details not retrieved → RUN_TOOL (GetUserDetailsTool)
5. Onboarding context needed → RUN_TOOL (GetOnboardingStatusTool)
6. All investigation done → PROPOSE_ACTION
7. Workflow paused, waiting → WAIT_FOR_ACTION
8. Workflow completed → WORKFLOW_COMPLETE
9. Cannot determine path → ESCALATE
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from case_engine.reasoning.reasoning_models import (
    NextStepType,
    ReasoningContext,
    ReasoningDecision,
    ReasoningStep,
)
from case_engine.workflows.models import WorkflowState

if TYPE_CHECKING:
    pass

LOGGER = logging.getLogger(__name__)

# Topic → required investigation tools
_TOPIC_TOOL_MAP: dict[str, list[str]] = {
    "VKYC_Session_Failure":  ["GetSessionDetailsTool", "GetUserDetailsTool"],
    "OTP_Delivery_Failure":  ["GetUserDetailsTool", "GetFailureReasonTool"],
    "Document_OCR_Failure":  ["GetUserDetailsTool", "GetOnboardingStatusTool"],
    "Agent_Portal_Issue":    ["GetUserDetailsTool", "GetFailureReasonTool"],
    "API_Callback_Failure":  ["GetFailureReasonTool"],
}

# Slot that triggers tool recommendation per topic
_SESSION_ID_TOPICS: frozenset[str] = frozenset({
    "VKYC_Session_Failure",
})
_APPLICATION_ID_TOPICS: frozenset[str] = frozenset({
    "Document_OCR_Failure",
    "API_Callback_Failure",
    "Agent_Portal_Issue",
})


class ReasoningEngine:
    """
    Deterministic reasoning engine for the KwikID support agent.

    All public methods never raise — they return typed ReasoningDecision objects.
    Stateless: create once, use across many cases.
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(self, ctx: ReasoningContext) -> list[ReasoningDecision]:
        """
        Produce an ordered list of all reasoning conclusions for a context.

        Returns one ReasoningDecision per reasoning pass (multiple passes
        may be needed to reach a terminal decision). In deterministic mode,
        each decision is derived from explicit rules.

        The list represents an ordered reasoning chain, not multiple alternatives.
        """
        steps: list[ReasoningDecision] = []
        try:
            decision = self.choose_next_step(ctx)
            steps.append(decision)
        except Exception as exc:
            LOGGER.exception("reasoning_engine.analyze failed case_id=%s error=%s", ctx.case.case_id, exc)
            steps.append(ReasoningDecision(
                next_step=NextStepType.ESCALATE,
                confidence=0.0,
                rationale=f"Reasoning error: {type(exc).__name__}",
            ))
        return steps

    def choose_next_step(self, ctx: ReasoningContext) -> ReasoningDecision:
        """
        Return the single best next step for this context.

        Priority order:
        1. Terminal workflow state → WORKFLOW_COMPLETE
        2. Paused workflow → WAIT_FOR_ACTION
        3. Missing required slots → ASK_FOR_SLOT
        4. Investigation tools needed → RUN_TOOL
        5. All investigation done → PROPOSE_ACTION
        6. Unknown state → ESCALATE
        """
        reasoning_steps: list[ReasoningStep] = []

        # Rule 1: Workflow already completed
        if ctx.case.workflow_state == WorkflowState.COMPLETED.value:
            reasoning_steps.append(ReasoningStep(
                step_name="check_workflow_state",
                observation=f"workflow_state={ctx.case.workflow_state}",
                conclusion="Workflow is already complete",
                confidence=1.0,
            ))
            return ReasoningDecision(
                next_step=NextStepType.WORKFLOW_COMPLETE,
                confidence=1.0,
                rationale="Workflow has reached COMPLETED state",
                reasoning_steps=reasoning_steps,
            )

        # Rule 2: Workflow paused waiting for action
        if ctx.case.workflow_state == WorkflowState.PAUSED.value:
            reasoning_steps.append(ReasoningStep(
                step_name="check_workflow_state",
                observation=f"workflow_state={ctx.case.workflow_state}",
                conclusion="Workflow is paused waiting for action approval",
                confidence=1.0,
            ))
            return ReasoningDecision(
                next_step=NextStepType.WAIT_FOR_ACTION,
                confidence=1.0,
                rationale="Workflow is PAUSED waiting for action gateway response",
                reasoning_steps=reasoning_steps,
            )

        # Rule 3: Missing required slots → ask user
        missing = self.determine_missing_information(ctx)
        if missing:
            slot = missing[0]
            reasoning_steps.append(ReasoningStep(
                step_name="check_slot_completeness",
                observation=f"Missing slots: {missing}",
                conclusion=f"Must gather '{slot}' from user before proceeding",
                confidence=1.0,
            ))
            return ReasoningDecision(
                next_step=NextStepType.ASK_FOR_SLOT,
                confidence=1.0,
                rationale=f"Required slot '{slot}' is not filled",
                slot_name=slot,
                missing_slots=missing,
                reasoning_steps=reasoning_steps,
            )

        # Rule 4: Investigation tools needed
        tool = self.recommend_tool(ctx)
        if tool is not None:
            reasoning_steps.append(ReasoningStep(
                step_name="recommend_investigation_tool",
                observation=f"Topic={ctx.case.topic}, tools_run={[r.tool_name for r in ctx.tool_results if r.success]}",
                conclusion=f"Run {tool} to gather investigation context",
                confidence=0.95,
            ))
            return ReasoningDecision(
                next_step=NextStepType.RUN_TOOL,
                confidence=0.95,
                rationale=f"Investigation tool '{tool}' has not been run yet for this topic",
                tool_name=tool,
                reasoning_steps=reasoning_steps,
            )

        # Rule 5: All slots filled, all tools run → propose action
        reasoning_steps.append(ReasoningStep(
            step_name="propose_action_check",
            observation="All required slots filled and investigation complete",
            conclusion="Ready to propose remediation action",
            confidence=0.9,
        ))
        return ReasoningDecision(
            next_step=NextStepType.PROPOSE_ACTION,
            confidence=0.9,
            rationale="All required slots are filled and investigation tools have been run",
            reasoning_steps=reasoning_steps,
        )

    def determine_missing_information(self, ctx: ReasoningContext) -> list[str]:
        """
        Return a list of slot names that are required but not yet filled.

        Uses the clarification engine's slot registry for the current topic.
        Returns an empty list if the topic is unknown.
        """
        from case_engine.slot_filling.models import SlotStatus
        try:
            missing: list[str] = []
            for slot_name, sv in ctx.slot_values.items():
                if sv.status != SlotStatus.FILLED:
                    missing.append(slot_name)
            return missing
        except Exception as exc:
            LOGGER.warning("reasoning_engine.determine_missing_information failed: %s", exc)
            return []

    def recommend_tool(self, ctx: ReasoningContext) -> str | None:
        """
        Return the name of the next investigation tool to run, or None.

        Uses the topic → tool map and checks which tools have already succeeded.
        Rules:
        - Session ID present → recommend GetSessionDetailsTool (VKYC topic)
        - Failure reason unknown → recommend GetFailureReasonTool
        - User details missing → recommend GetUserDetailsTool
        - Onboarding context needed → recommend GetOnboardingStatusTool
        """
        topic = ctx.case.topic or ""
        required_tools = _TOPIC_TOOL_MAP.get(topic, [])
        already_run = {r.tool_name for r in ctx.tool_results if r.success}

        for tool_name in required_tools:
            if tool_name not in already_run:
                return tool_name

        # Fallback: session_id present but GetSessionDetailsTool not run
        if topic in _SESSION_ID_TOPICS:
            if "GetSessionDetailsTool" not in already_run:
                slot_ctx = {name: sv for name, sv in ctx.slot_values.items()}
                from case_engine.slot_filling.models import SlotStatus
                if "session_id" in slot_ctx and slot_ctx["session_id"].status == SlotStatus.FILLED:
                    return "GetSessionDetailsTool"

        return None
