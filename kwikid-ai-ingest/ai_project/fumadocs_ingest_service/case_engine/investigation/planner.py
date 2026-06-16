"""
case_engine/investigation/planner.py

Sprint 2.18: InvestigationPlanner — generates InvestigationPlan from topic + playbook.

Strategy (in priority order):
  1. If the WorkflowDefinition has populated investigation_steps, use those
     directly (they were authored by the playbook designer).
  2. Fall back to the topic → tool map (same map used by ReasoningEngine).

For each tool in the plan, the planner resolves:
  - required_slot: which slot_name provides the input value
  - input_key:     the parameter name the tool expects

This mapping is stable and does not require LLM involvement.

Public API:
  planner = InvestigationPlanner()
  plan    = planner.plan(topic, workflow_def, slot_values)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.investigation.models import (
    InvestigationPlan,
    InvestigationStep,
)

if TYPE_CHECKING:
    from case_engine.workflows.models import WorkflowDefinition

LOGGER = logging.getLogger(__name__)

# ── Slot → tool input mapping ──────────────────────────────────────────────────
# Maps each tool to (required_slot, input_key).
# required_slot: the slot_name the planner expects to be filled
# input_key:     the parameter name the tool's run() expects
_TOOL_SLOT_MAP: dict[str, tuple[str, str]] = {
    "GetSessionDetailsTool":   ("session_id",     "session_id"),
    "GetUserDetailsTool":      ("phone_number",   "phone_number"),
    "GetFailureReasonTool":    ("session_id",     "operation_id"),   # session_id used as operation id
    "GetCaseHistoryTool":      ("phone_number",   "phone_number"),
    "GetOnboardingStatusTool": ("application_id", "application_id"),
}

# ── Topic → ordered tool list (fallback when playbook has no investigation_steps)
_TOPIC_TOOL_MAP: dict[str, list[str]] = {
    "VKYC_Session_Failure":  ["GetSessionDetailsTool", "GetUserDetailsTool"],
    "OTP_Delivery_Failure":  ["GetUserDetailsTool",    "GetFailureReasonTool"],
    "Document_OCR_Failure":  ["GetUserDetailsTool",    "GetOnboardingStatusTool"],
    "Agent_Portal_Issue":    ["GetUserDetailsTool",    "GetFailureReasonTool"],
    "API_Callback_Failure":  ["GetFailureReasonTool"],
}

# ── Purpose descriptions for auto-generated steps ─────────────────────────────
_TOOL_PURPOSES: dict[str, str] = {
    "GetSessionDetailsTool":   "Retrieve session status, attempt count, and failure code",
    "GetUserDetailsTool":      "Retrieve KYC status, account state, and risk tier for the user",
    "GetFailureReasonTool":    "Identify the failure category, code, and transience of the error",
    "GetCaseHistoryTool":      "Review prior case history, repeat patterns, and escalation rate",
    "GetOnboardingStatusTool": "Determine onboarding stage, completion percentage, and blocking step",
}


class InvestigationPlanner:
    """
    Generates an InvestigationPlan from a topic, an optional playbook definition,
    and the current slot values.

    Stateless — safe to share across requests.
    """

    def plan(
        self,
        topic: str,
        workflow_def: WorkflowDefinition | None,
        slot_values: dict[str, Any],
    ) -> InvestigationPlan:
        """
        Build and return an InvestigationPlan.

        Args:
            topic:        The ticket topic (e.g. "VKYC_Session_Failure").
            workflow_def: The active playbook, if one has been selected. May be None.
            slot_values:  Current slot state — used to verify inputs are available.

        Returns:
            An InvestigationPlan with ordered steps.
        """
        case_id = _extract_case_id(slot_values)
        plan_id = str(uuid.uuid4())
        now     = datetime.now(tz=timezone.utc).isoformat()

        steps = self._build_steps(topic, workflow_def, slot_values)

        plan = InvestigationPlan(
            plan_id=plan_id,
            case_id=case_id,
            topic=topic,
            workflow_id=workflow_def.workflow_id if workflow_def else None,
            steps=tuple(steps),
            created_at=now,
        )
        LOGGER.info(
            "investigation_planner.plan topic=%s workflow=%s steps=%d",
            topic,
            plan.workflow_id,
            len(steps),
        )
        return plan

    # ── Step construction ──────────────────────────────────────────────────────

    def _build_steps(
        self,
        topic: str,
        workflow_def: WorkflowDefinition | None,
        slot_values: dict[str, Any],
    ) -> list[InvestigationStep]:
        """Build ordered InvestigationStep list."""

        # Priority 1: playbook-authored investigation_steps
        if workflow_def is not None and workflow_def.investigation_steps:
            return self._steps_from_playbook(workflow_def.investigation_steps)

        # Priority 2: topic → tool map fallback
        return self._steps_from_topic_map(topic)

    def _steps_from_playbook(
        self,
        investigation_steps: tuple[dict[str, Any], ...],
    ) -> list[InvestigationStep]:
        """
        Convert playbook investigation_steps (YAML dicts) to InvestigationStep objects.

        YAML format:
          investigation_steps:
            - tool: GetSessionDetailsTool
              purpose: "..."
              required_input: session_id
        """
        steps: list[InvestigationStep] = []
        for idx, raw in enumerate(investigation_steps):
            tool_name = str(raw.get("tool", ""))
            if not tool_name:
                continue

            slot_map = _TOOL_SLOT_MAP.get(tool_name, (tool_name, tool_name))
            required_slot = str(raw.get("required_input", slot_map[0]))
            input_key     = slot_map[1]
            purpose       = str(raw.get("purpose", _TOOL_PURPOSES.get(tool_name, tool_name)))

            steps.append(InvestigationStep(
                step_id=f"step_{idx:02d}_{tool_name.lower().replace('tool', '')}",
                sequence=idx,
                tool_name=tool_name,
                purpose=purpose,
                required_slot=required_slot,
                input_key=input_key,
            ))

        return steps

    def _steps_from_topic_map(self, topic: str) -> list[InvestigationStep]:
        """
        Build steps from the static topic → tool map.

        Used when the playbook has no investigation_steps or no playbook was selected.
        """
        tools = _TOPIC_TOOL_MAP.get(topic, [])
        steps: list[InvestigationStep] = []

        for idx, tool_name in enumerate(tools):
            slot_map = _TOOL_SLOT_MAP.get(tool_name)
            if slot_map is None:
                LOGGER.warning(
                    "investigation_planner: no slot map for tool=%s, skipping", tool_name
                )
                continue

            required_slot, input_key = slot_map
            steps.append(InvestigationStep(
                step_id=f"step_{idx:02d}_{tool_name.lower().replace('tool', '')}",
                sequence=idx,
                tool_name=tool_name,
                purpose=_TOOL_PURPOSES.get(tool_name, tool_name),
                required_slot=required_slot,
                input_key=input_key,
            ))

        if not steps:
            LOGGER.warning(
                "investigation_planner: no tools found for topic=%s — empty plan", topic
            )

        return steps


def _extract_case_id(slot_values: dict[str, Any]) -> str:
    """Pull case_id out of slot_values metadata, or generate a placeholder."""
    meta = slot_values.get("_meta") or {}
    if isinstance(meta, dict):
        case_id = meta.get("case_id")
        if case_id:
            return str(case_id)
    return "unknown"
