"""
case_engine/workflows/investigation_step.py

Sprint 2.19: InvestigationStepExecutor — executes an INVESTIGATE workflow step.

Navigation contract (per blueprint flow_diagram.mermaid):
  success = True  → on_success route (investigation ran; root_cause.escalate=False)
  success = False → on_failure route (root_cause.escalate=True, or service unavailable)

Design invariants:
  - Never raises — all exceptions produce a safe error-state InvestigationResult
  - Fully deterministic (no LLM)
  - Injects _meta.case_id into slot_values when case is provided
  - Logs at INFO level on start and completion
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from case_engine.investigation.models import InvestigationResult
    from case_engine.investigation.service import InvestigationService
    from case_engine.models import Case
    from case_engine.workflows.models import WorkflowDefinition, WorkflowStep

LOGGER = logging.getLogger(__name__)


class InvestigationStepExecutor:
    """
    Executes a single INVESTIGATE workflow step by delegating to InvestigationService.

    Stateless per call — safe to share across concurrent requests.
    """

    def __init__(self, investigation_service: "InvestigationService") -> None:
        self._service = investigation_service

    def execute(
        self,
        step: "WorkflowStep",
        defn: "WorkflowDefinition",
        slot_context: dict[str, str],
        case: "Case | None",
    ) -> tuple["InvestigationResult", bool]:
        """
        Run the investigation pipeline for a workflow step.

        Args:
            step:         The INVESTIGATE WorkflowStep being executed.
            defn:         The active WorkflowDefinition — provides topic and investigation_steps.
            slot_context: Flat string slot dict built by WorkflowEngine._build_slot_context().
            case:         Live Case object (optional — used for audit logging only).

        Returns:
            (InvestigationResult, success)
            success = True  if investigation ran and root_cause.escalate is False
            success = False if root_cause.escalate is True, or if service errored
        """
        # Build slot_values compatible with InvestigationService.investigate()
        # slot_context is already a flat str→str dict; inject _meta for case tracing
        inv_slot_values: dict[str, Any] = dict(slot_context)
        if case is not None:
            inv_slot_values["_meta"] = {"case_id": case.case_id}

        LOGGER.info(
            "investigation_step.execute workflow=%s step=%s topic=%s case=%s",
            defn.workflow_id,
            step.step_id,
            defn.topic,
            case.case_id if case else "none",
        )

        result = self._service.investigate(
            topic=defn.topic,
            workflow_def=defn,
            slot_values=inv_slot_values,
            case=case,
        )

        success = not result.root_cause.escalate
        LOGGER.info(
            "investigation_step.complete step=%s category=%s confidence=%.2f escalate=%s success=%s",
            step.step_id,
            result.root_cause.category.value,
            result.root_cause.confidence,
            result.root_cause.escalate,
            success,
        )
        return result, success
