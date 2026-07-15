"""
case_engine/investigation/collector/validators.py

Sprint 2.42: Validation for the Evidence Collector Engine.

CollectorValidator checks:
  - CollectionResult against EvidenceRequirement (expected_fields, required)
  - PlanningStep preconditions against CollectionContext
  - InvestigationPlan well-formedness before execution

All validate_* methods return (ok: bool, reasons: list[str]).
They never raise — validation failures are recorded, not raised.

Dependency direction:
  validators.py → collector/contracts.py (CollectionResult, CollectionContext)
  validators.py → investigation/planner/models.py (EvidenceRequirement, PlanningStep, InvestigationPlan, PreconditionKind)
  validators.py → stdlib only
"""
from __future__ import annotations

import logging

from case_engine.investigation.collector.contracts import CollectionContext, CollectionResult
from case_engine.investigation.planner.models import (
    EvidenceRequirement,
    InvestigationPlan,
    PlanningStep,
    PreconditionKind,
)

LOGGER = logging.getLogger(__name__)


class CollectorValidator:
    """
    Validates CollectionResults and PlanningStep preconditions.

    All validate_* methods return (ok: bool, reasons: list[str]).
    They never raise — validation failures are recorded, not raised.
    """

    def validate_result(
        self,
        result: CollectionResult,
        requirement: EvidenceRequirement,
    ) -> tuple[bool, list[str]]:
        """
        Check that a CollectionResult satisfies an EvidenceRequirement.

        Checks:
          V01: Result must report success=True.
          V02: All expected_fields must be present in payload (if success).
          V03: required=True requirements must have non-empty payload.

        Returns:
            (ok, reasons) — reasons is non-empty when ok=False.
        """
        reasons: list[str] = []

        if not result.success:
            reasons.append(
                f"V01: result is failure (code={result.error_code!r}, "
                f"msg={result.error_message!r})"
            )
            return False, reasons

        if requirement.expected_fields:
            missing = [
                f for f in requirement.expected_fields
                if f not in result.payload
            ]
            if missing:
                reasons.append(
                    f"V02: payload missing expected fields: {missing}"
                )

        if requirement.required and not result.payload:
            reasons.append("V03: required evidence has empty payload")

        return len(reasons) == 0, reasons

    def validate_preconditions(
        self,
        step: PlanningStep,
        context: CollectionContext,
        completed_step_ids: frozenset[str],
    ) -> tuple[bool, list[str]]:
        """
        Check that all preconditions for a step are satisfied.

        Checks:
          P01: SLOT_PRESENT — slot must be in context.slots with a truthy value
          P02: EVIDENCE_PRESENT — the referenced step_id must be in completed_step_ids
          P03: ALWAYS — always passes
          P04: NEVER — always fails (used to disable a step)

        Returns:
            (ok, reasons) — reasons is non-empty when ok=False.
        """
        reasons: list[str] = []

        for pre in step.preconditions:
            if pre.kind == PreconditionKind.ALWAYS:
                continue
            elif pre.kind == PreconditionKind.NEVER:
                reasons.append(
                    f"P04: precondition {pre.precondition_id!r} is NEVER (step disabled)"
                )
            elif pre.kind == PreconditionKind.SLOT_PRESENT:
                value = context.slots.get(pre.key)
                if not value:
                    reasons.append(
                        f"P01: required slot {pre.key!r} not present in context"
                    )
            elif pre.kind == PreconditionKind.EVIDENCE_PRESENT:
                if pre.key not in completed_step_ids:
                    reasons.append(
                        f"P02: prerequisite step {pre.key!r} has not completed"
                    )

        return len(reasons) == 0, reasons

    def validate_plan(
        self,
        plan: InvestigationPlan,
    ) -> tuple[bool, list[str]]:
        """
        Check that an InvestigationPlan is well-formed before execution.

        Checks:
          PL01: plan_id must be non-empty
          PL02: case_id must be non-empty
          PL03: steps must not be empty
          PL04: all step_ids must be unique
          PL05: graph.nodes must match steps

        Returns:
            (ok, reasons)
        """
        reasons: list[str] = []

        if not plan.plan_id:
            reasons.append("PL01: plan_id is empty")
        if not plan.case_id:
            reasons.append("PL02: case_id is empty")
        if not plan.steps:
            reasons.append("PL03: plan has no steps")
            return False, reasons

        step_ids = [s.step_id for s in plan.steps]
        if len(step_ids) != len(set(step_ids)):
            reasons.append("PL04: duplicate step_ids detected")

        node_ids = {n.step_id for n in plan.graph.nodes}
        step_id_set = set(step_ids)
        if node_ids != step_id_set:
            extra = node_ids - step_id_set
            missing = step_id_set - node_ids
            reasons.append(
                f"PL05: graph.nodes do not match steps "
                f"(extra_in_graph={sorted(extra)}, "
                f"missing_from_graph={sorted(missing)})"
            )

        return len(reasons) == 0, reasons
