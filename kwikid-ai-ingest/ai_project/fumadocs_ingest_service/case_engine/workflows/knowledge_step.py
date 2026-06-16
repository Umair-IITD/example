"""
case_engine/workflows/knowledge_step.py

Sprint 2.20: KnowledgeLookupStepExecutor — executes a KNOWLEDGE_LOOKUP workflow step.

Navigation contract (per blueprint flow_diagram.mermaid):
  success = True  → on_success route (knowledge service ran, result stored)
  success = False → on_failure route (service unavailable or pipeline error)

Note: success is NOT determined by whether an SOP match was found.
  - success=True means the knowledge service executed and produced a result
  - success=False means the service was unavailable or crashed

The recommendation itself may indicate escalation_required=True even when
success=True — callers inspect knowledge_result["recommendation"]["escalation_required"]
to decide downstream routing.

Design invariants:
  - Never raises — delegates to KnowledgeService.search() which never raises
  - Fully deterministic (no LLM)
  - Extracts investigation_result from WorkflowExecutionResult for context
  - Logs at INFO level on start and completion
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from case_engine.knowledge.service import KnowledgeService
    from case_engine.models import Case
    from case_engine.workflows.models import (
        WorkflowDefinition,
        WorkflowExecutionResult,
        WorkflowStep,
    )

LOGGER = logging.getLogger(__name__)


class KnowledgeLookupStepExecutor:
    """
    Executes a single KNOWLEDGE_LOOKUP workflow step via KnowledgeService.

    Stateless per call — safe to share across concurrent requests.
    """

    def __init__(self, knowledge_service: "KnowledgeService") -> None:
        self._service = knowledge_service

    def execute(
        self,
        step: "WorkflowStep",
        defn: "WorkflowDefinition",
        result: "WorkflowExecutionResult",
        case: "Case | None",
    ) -> tuple[dict[str, Any], bool]:
        """
        Run the knowledge search pipeline for a workflow step.

        Args:
            step:   The KNOWLEDGE_LOOKUP WorkflowStep being executed.
            defn:   The active WorkflowDefinition — provides topic.
            result: Current WorkflowExecutionResult — provides investigation_result.
            case:   Live Case object (optional — used for audit logging only).

        Returns:
            (knowledge_result_dict, success)
            knowledge_result_dict: KnowledgeResult.to_dict() — always JSON-safe
            success: True  if service ran successfully
                     False if service is unavailable or pipeline crashed
        """
        LOGGER.info(
            "knowledge_step.execute workflow=%s step=%s topic=%s case=%s"
            " has_investigation=%s",
            defn.workflow_id,
            step.step_id,
            defn.topic,
            case.case_id if case else "none",
            result.investigation_result is not None,
        )

        knowledge_dict = self._service.search(
            topic=defn.topic,
            investigation_result=result.investigation_result,
            case=case,
        )

        # Success if service ran (dict has a result_id)
        success = bool(knowledge_dict.get("result_id"))

        sop_found  = knowledge_dict.get("sop_match_found", False)
        rec_action = (knowledge_dict.get("recommendation") or {}).get("recommended_action", "")
        confidence = (knowledge_dict.get("recommendation") or {}).get("confidence", 0.0)

        LOGGER.info(
            "knowledge_step.complete step=%s sop_found=%s action=%s confidence=%.2f success=%s",
            step.step_id,
            sop_found,
            rec_action,
            float(confidence),
            success,
        )
        return knowledge_dict, success
