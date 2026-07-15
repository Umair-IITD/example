"""
case_engine/investigation/planner/_legacy.py

Sprint 2.18 InvestigationPlanner — tool-based planning (preserved for backward compat).

This module is the original planner.py, moved here when the planner package was
introduced in Sprint 2.39. All existing imports still work via planner/__init__.py.

The Sprint 2.39 evidence-based planner is in planner/engine.py.
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

_TOOL_SLOT_MAP: dict[str, tuple[str, str]] = {
    "GetSessionDetailsTool":   ("session_id",     "session_id"),
    "GetUserDetailsTool":      ("phone_number",   "phone_number"),
    "GetFailureReasonTool":    ("session_id",     "operation_id"),
    "GetCaseHistoryTool":      ("phone_number",   "phone_number"),
    "GetOnboardingStatusTool": ("application_id", "application_id"),
}

_TOPIC_TOOL_MAP: dict[str, list[str]] = {
    "VKYC_Session_Failure":  ["GetSessionDetailsTool", "GetUserDetailsTool"],
    "OTP_Delivery_Failure":  ["GetUserDetailsTool",    "GetFailureReasonTool"],
    "Document_OCR_Failure":  ["GetUserDetailsTool",    "GetOnboardingStatusTool"],
    "Agent_Portal_Issue":    ["GetUserDetailsTool",    "GetFailureReasonTool"],
    "API_Callback_Failure":  ["GetFailureReasonTool"],
}

_TOOL_PURPOSES: dict[str, str] = {
    "GetSessionDetailsTool":   "Retrieve session status, attempt count, and failure code",
    "GetUserDetailsTool":      "Retrieve KYC status, account state, and risk tier for the user",
    "GetFailureReasonTool":    "Identify the failure category, code, and transience of the error",
    "GetCaseHistoryTool":      "Review prior case history, repeat patterns, and escalation rate",
    "GetOnboardingStatusTool": "Determine onboarding stage, completion percentage, and blocking step",
}


class InvestigationPlanner:
    """
    Sprint 2.18 tool-based InvestigationPlanner (legacy).

    Preserved for backward compatibility. New evidence-based planning is in
    case_engine.investigation.planner.engine.InvestigationPlanner.
    """

    def plan(
        self,
        topic: str,
        workflow_def: WorkflowDefinition | None,
        slot_values: dict[str, Any],
    ) -> InvestigationPlan:
        case_id = _extract_case_id(slot_values)
        plan_id = str(uuid.uuid4())
        now     = datetime.now(tz=timezone.utc).isoformat()
        steps   = self._build_steps(topic, workflow_def, slot_values)
        plan = InvestigationPlan(
            plan_id=plan_id,
            case_id=case_id,
            topic=topic,
            workflow_id=workflow_def.workflow_id if workflow_def else None,
            steps=tuple(steps),
            created_at=now,
        )
        LOGGER.info(
            "legacy_planner.plan topic=%s workflow=%s steps=%d",
            topic, plan.workflow_id, len(steps),
        )
        return plan

    def _build_steps(
        self,
        topic: str,
        workflow_def: WorkflowDefinition | None,
        slot_values: dict[str, Any],
    ) -> list[InvestigationStep]:
        if workflow_def is not None and workflow_def.investigation_steps:
            return self._steps_from_playbook(workflow_def.investigation_steps)
        return self._steps_from_topic_map(topic)

    def _steps_from_playbook(
        self,
        investigation_steps: tuple[dict[str, Any], ...],
    ) -> list[InvestigationStep]:
        steps: list[InvestigationStep] = []
        for idx, raw in enumerate(investigation_steps):
            tool_name = str(raw.get("tool", ""))
            if not tool_name:
                continue
            slot_map     = _TOOL_SLOT_MAP.get(tool_name, (tool_name, tool_name))
            required_slot = str(raw.get("required_input", slot_map[0]))
            input_key    = slot_map[1]
            purpose      = str(raw.get("purpose", _TOOL_PURPOSES.get(tool_name, tool_name)))
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
        tools = _TOPIC_TOOL_MAP.get(topic, [])
        steps: list[InvestigationStep] = []
        for idx, tool_name in enumerate(tools):
            slot_map = _TOOL_SLOT_MAP.get(tool_name)
            if slot_map is None:
                LOGGER.warning("legacy_planner: no slot map for tool=%s, skipping", tool_name)
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
            LOGGER.warning("legacy_planner: no tools for topic=%s — empty plan", topic)
        return steps


def _extract_case_id(slot_values: dict[str, Any]) -> str:
    meta = slot_values.get("_meta") or {}
    if isinstance(meta, dict):
        case_id = meta.get("case_id")
        if case_id:
            return str(case_id)
    return "unknown"
