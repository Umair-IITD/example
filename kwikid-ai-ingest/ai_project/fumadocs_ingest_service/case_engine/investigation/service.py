"""
case_engine/investigation/service.py

Sprint 2.18: InvestigationService — orchestrates the full investigation pipeline.

Pipeline (per blueprint flow_diagram.mermaid):
  ENGINE → INVESTIGATION (planner) → EVIDENCE (collector)
         → ROOTCAUSE → OBSGEN → InvestigationResult

Design invariants:
  - Never raises to callers — all exceptions produce an InvestigationResult
    with category=UNKNOWN, escalate=True, and a diagnostic observation note
  - Audit events emitted: INVESTIGATION_STARTED, INVESTIGATION_COMPLETED
  - No LLM calls — fully deterministic
  - Case argument is optional (allows standalone admin invocations without a live Case)

Public API:
  service = InvestigationService(planner, collector, rca_engine, obs_gen, audit_logger)
  result  = service.investigate(topic, workflow_def, slot_values, case=None)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.investigation._collector_sprint218 import EvidenceCollector
from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationResult,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)
from case_engine.investigation._observation_sprint218 import ObservationGenerator
from case_engine.investigation.planner import InvestigationPlanner
from case_engine.investigation._root_cause_sprint218 import RootCauseEngine

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.models import Case
    from case_engine.workflows.models import WorkflowDefinition

LOGGER = logging.getLogger(__name__)


class InvestigationService:
    """
    Orchestrates the Investigation Layer pipeline:
      plan → collect → analyse → generate observation → return result

    Stateless per call. One instance is safe to share across requests.
    """

    def __init__(
        self,
        planner: InvestigationPlanner,
        collector: EvidenceCollector,
        rca_engine: RootCauseEngine,
        obs_gen: ObservationGenerator,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._planner    = planner
        self._collector  = collector
        self._rca        = rca_engine
        self._obs        = obs_gen
        self._audit      = audit_logger

    def investigate(
        self,
        topic: str,
        workflow_def: WorkflowDefinition | None,
        slot_values: dict[str, Any],
        case: Case | None = None,
    ) -> InvestigationResult:
        """
        Run the full investigation pipeline and return an InvestigationResult.

        Args:
            topic:        Ticket topic (e.g. "VKYC_Session_Failure").
            workflow_def: Active playbook, if one is selected. May be None.
            slot_values:  Current slot state from slot filling.
            case:         The live Case object (optional — used only for audit logging).

        Returns:
            InvestigationResult — never raises.
        """
        try:
            return self._investigate(topic, workflow_def, slot_values, case)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "investigation_service.fatal_error topic=%s", topic
            )
            return self._error_result(topic, slot_values, str(exc))

    # ── Private ────────────────────────────────────────────────────────────────

    def _investigate(
        self,
        topic: str,
        workflow_def: WorkflowDefinition | None,
        slot_values: dict[str, Any],
        case: Case | None,
    ) -> InvestigationResult:
        # 1. Plan
        plan = self._planner.plan(topic, workflow_def, slot_values)

        if case is not None and self._audit is not None:
            self._audit.log_investigation_started(
                case,
                topic=plan.topic,
                plan_id=plan.plan_id,
                step_count=len(plan.steps),
            )

        # 2. Collect evidence
        bundle = self._collector.collect(plan, slot_values)

        # 3. Root cause analysis
        root_cause = self._rca.analyse(bundle)

        # 4. Generate observation note
        observation = self._obs.generate(bundle, root_cause)

        # 5. Assemble result
        result = InvestigationResult(
            result_id=str(uuid.uuid4()),
            case_id=plan.case_id,
            plan=plan,
            bundle=bundle,
            root_cause=root_cause,
            observation=observation,
            completed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        if case is not None and self._audit is not None:
            self._audit.log_investigation_completed(
                case,
                plan_id=plan.plan_id,
                bundle_id=bundle.bundle_id,
                analysis_id=root_cause.analysis_id,
                category=root_cause.category.value,
                escalate=root_cause.escalate,
            )

        LOGGER.info(
            "investigation_service.complete topic=%s result_id=%s category=%s escalate=%s",
            topic, result.result_id, root_cause.category.value, root_cause.escalate,
        )
        return result

    def _error_result(
        self,
        topic: str,
        slot_values: dict[str, Any],
        error_msg: str,
    ) -> InvestigationResult:
        """Build a safe error-state InvestigationResult when the pipeline crashes."""
        from case_engine.investigation.planner import _extract_case_id  # noqa: PLC0415

        case_id   = _extract_case_id(slot_values)
        plan_id   = str(uuid.uuid4())
        bundle_id = str(uuid.uuid4())
        now       = datetime.now(tz=timezone.utc).isoformat()

        from case_engine.investigation.models import (  # noqa: PLC0415
            EvidenceBundle,
            InvestigationPlan,
        )

        plan = InvestigationPlan(
            plan_id=plan_id,
            case_id=case_id,
            topic=topic,
            workflow_id=None,
            steps=(),
            created_at=now,
        )
        bundle = EvidenceBundle(
            bundle_id=bundle_id,
            case_id=case_id,
            topic=topic,
            plan_id=plan_id,
            items=[],
            collected_at=now,
        )
        root_cause = RootCauseAnalysis(
            analysis_id=str(uuid.uuid4()),
            case_id=case_id,
            topic=topic,
            category=RootCauseCategory.UNKNOWN,
            confidence=0.0,
            explanation=f"Investigation pipeline error: {error_msg}",
            evidence_ids=[],
            recommended_action=RecommendedAction.ESCALATE,
            escalate=True,
            analysed_at=now,
        )
        observation = (
            f"[INVESTIGATION PIPELINE ERROR]\n\n"
            f"Case ID: {case_id}\nTopic:   {topic}\n"
            f"Error:   {error_msg}\n\n"
            "Automated investigation failed. Manual review required."
        )
        return InvestigationResult(
            result_id=str(uuid.uuid4()),
            case_id=case_id,
            plan=plan,
            bundle=bundle,
            root_cause=root_cause,
            observation=observation,
            completed_at=now,
        )
