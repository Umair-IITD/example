"""
case_engine/investigation/collector.py

Sprint 2.18: EvidenceCollector — executes an InvestigationPlan and produces an EvidenceBundle.

Responsibilities (per blueprint Section 7):
  - Walk through each InvestigationStep in the plan
  - Resolve the required slot value from slot_values
  - Invoke the tool via ToolExecutor
  - Normalize the ToolResult into a typed Evidence subclass
  - Aggregate all Evidence into an EvidenceBundle

Guarantees:
  - Never raises — tool failures are recorded as failed Evidence items
  - Deterministic — same inputs produce the same evidence structure
  - No LLM calls — all logic is pure Python

Public API:
  collector = EvidenceCollector(tool_executor)
  bundle    = collector.collect(plan, slot_values)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.investigation.models import (
    Evidence,
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    InvestigationPlan,
    InvestigationStep,
    LogEvidence,
    MetricEvidence,
    SessionEvidence,
    SummaryEvidence,
    UserEvidence,
    VideoEvidence,
)
from case_engine.tools.tool_models import ToolResult

if False:  # TYPE_CHECKING avoids circular import at runtime
    from case_engine.tools.tool_executor import ToolExecutor

LOGGER = logging.getLogger(__name__)

# Maps tool name → (EvidenceType, EvidenceSource, Evidence subclass)
_TOOL_EVIDENCE_MAP: dict[str, tuple[EvidenceType, EvidenceSource, type[Evidence]]] = {
    "GetUserDetailsTool":      (EvidenceType.USER,    EvidenceSource.GET_USER_DETAILS,      UserEvidence),
    "GetSessionDetailsTool":   (EvidenceType.SESSION, EvidenceSource.GET_SESSION_DETAILS,   SessionEvidence),
    "GetFailureReasonTool":    (EvidenceType.LOG,     EvidenceSource.GET_FAILURE_REASON,     LogEvidence),
    "GetCaseHistoryTool":      (EvidenceType.SUMMARY, EvidenceSource.GET_CASE_HISTORY,       SummaryEvidence),
    "GetOnboardingStatusTool": (EvidenceType.SUMMARY, EvidenceSource.GET_ONBOARDING_STATUS,  SummaryEvidence),
}


class EvidenceCollector:
    """
    Executes an InvestigationPlan against the live ToolExecutor and aggregates results.

    Stateless per collection run. One EvidenceCollector instance is safe to share.
    """

    def __init__(self, tool_executor: ToolExecutor) -> None:  # type: ignore[name-defined]
        self._executor = tool_executor

    def collect(
        self,
        plan: InvestigationPlan,
        slot_values: dict[str, Any],
    ) -> EvidenceBundle:
        """
        Execute all steps in the plan and return the aggregate EvidenceBundle.

        Args:
            plan:        The InvestigationPlan produced by InvestigationPlanner.
            slot_values: Current slot values — used to resolve each step's input.
                         Keys are slot_name strings; values are the slot value
                         (string or SlotValue object with .value attribute).

        Returns:
            EvidenceBundle containing one Evidence item per step (success or failure).
        """
        bundle_id    = str(uuid.uuid4())
        collected_at = datetime.now(tz=timezone.utc).isoformat()
        items: list[Evidence] = []

        for step in plan.steps:
            evidence = self._execute_step(step, slot_values)
            items.append(evidence)
            LOGGER.info(
                "evidence_collector.step tool=%s success=%s evidence_id=%s",
                step.tool_name,
                evidence.success,
                evidence.evidence_id,
            )

        bundle = EvidenceBundle(
            bundle_id=bundle_id,
            case_id=plan.case_id,
            topic=plan.topic,
            plan_id=plan.plan_id,
            items=items,
            collected_at=collected_at,
        )
        LOGGER.info(
            "evidence_collector.bundle bundle_id=%s case_id=%s items=%d successful=%d",
            bundle_id,
            plan.case_id,
            len(items),
            len(bundle.successful_items),
        )
        return bundle

    # ── Private ────────────────────────────────────────────────────────────────

    def _execute_step(
        self,
        step: InvestigationStep,
        slot_values: dict[str, Any],
    ) -> Evidence:
        """Execute one investigation step and return typed Evidence."""
        evidence_id = str(uuid.uuid4())
        executed_at = datetime.now(tz=timezone.utc).isoformat()

        # Resolve the slot value required by this step
        raw_value = _resolve_slot(step.required_slot, slot_values)

        if raw_value is None:
            return self._missing_slot_evidence(
                step, evidence_id, executed_at,
                f"Required slot '{step.required_slot}' is not filled",
            )

        # Build the tool inputs dict
        tool_inputs = {step.input_key: raw_value}

        # Execute via ToolExecutor — never raises
        result: ToolResult = self._executor.execute(
            step.tool_name,
            tool_inputs,
            requested_by="investigation_collector",
        )

        return self._normalize(step, result, evidence_id)

    def _normalize(
        self,
        step: InvestigationStep,
        result: ToolResult,
        evidence_id: str,
    ) -> Evidence:
        """Convert a ToolResult into a typed Evidence subclass."""
        ev_type, ev_source, ev_class = _TOOL_EVIDENCE_MAP.get(
            step.tool_name,
            (EvidenceType.LOG, EvidenceSource.GET_FAILURE_REASON, LogEvidence),
        )

        return ev_class(
            evidence_id=evidence_id,
            evidence_type=ev_type,
            source=ev_source,
            tool_name=step.tool_name,
            payload=result.payload if result.success else {},
            collected_at=result.executed_at,
            invocation_id=result.invocation_id,
            success=result.success,
            error_code=result.error_code,
            error_message=result.error_message,
        )

    def _missing_slot_evidence(
        self,
        step: InvestigationStep,
        evidence_id: str,
        collected_at: str,
        message: str,
    ) -> Evidence:
        """Create a failed Evidence item for a missing required slot."""
        ev_type, ev_source, ev_class = _TOOL_EVIDENCE_MAP.get(
            step.tool_name,
            (EvidenceType.LOG, EvidenceSource.GET_FAILURE_REASON, LogEvidence),
        )
        LOGGER.warning(
            "evidence_collector.missing_slot step=%s slot=%s",
            step.step_id,
            step.required_slot,
        )
        return ev_class(
            evidence_id=evidence_id,
            evidence_type=ev_type,
            source=ev_source,
            tool_name=step.tool_name,
            payload={},
            collected_at=collected_at,
            invocation_id="",
            success=False,
            error_code="MISSING_REQUIRED_SLOT",
            error_message=message,
        )


def _resolve_slot(slot_name: str, slot_values: dict[str, Any]) -> str | None:
    """
    Extract a concrete string value for a slot_name from slot_values.

    Handles two forms:
      slot_values[slot_name] = "raw string value"
      slot_values[slot_name] = SlotValue(status=FILLED, value="...")
    """
    raw = slot_values.get(slot_name)
    if raw is None:
        return None

    # SlotValue object (has .status and .value)
    if hasattr(raw, "value") and hasattr(raw, "status"):
        from case_engine.slot_filling.models import SlotStatus
        if raw.status == SlotStatus.FILLED and raw.value is not None:
            return str(raw.value)
        return None

    # Plain string
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    return None
