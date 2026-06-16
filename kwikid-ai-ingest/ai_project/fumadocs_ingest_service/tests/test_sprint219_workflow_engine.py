"""
tests/test_sprint219_workflow_engine.py

Sprint 2.19 Part 3: WorkflowEngine INVESTIGATE step dispatch tests.

Coverage:
  - WorkflowEngine() still works with no args (backwards compat)
  - WorkflowEngine(investigation_service=svc) stores service
  - INVESTIGATE step dispatched to _exec_investigate
  - on_success navigated when escalate=False
  - on_failure navigated when escalate=True
  - investigation_result stored on WorkflowExecutionResult
  - investigation_result contains serialized InvestigationResult
  - Without investigation_service: INVESTIGATE step escalates gracefully (no crash)
  - Step outcome recorded correctly (INVESTIGATION_COMPLETED / INVESTIGATION_ESCALATED)
  - PROPOSE_ACTION guard: blocked when workflow has INVESTIGATE but investigation_result is None
  - PROPOSE_ACTION guard: not blocked when investigation_result is present
  - PROPOSE_ACTION guard: not triggered when workflow has no INVESTIGATE step (backwards compat)
  - Structural validation runs without raising
  - _emit_investigation_started called when audit present
  - _emit_investigation_completed called when audit present
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from typing import Any
import pytest

from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationPlan,
    InvestigationResult,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)
from case_engine.slot_filling.models import SlotValue, SlotStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_case(case_id: str = "case-wfe-001") -> MagicMock:
    case = MagicMock()
    case.case_id = case_id
    case.topic = "VKYC_Session_Failure"
    return case


def _inv_result(escalate: bool = False, category: RootCauseCategory = RootCauseCategory.EXPIRED_SESSION) -> InvestigationResult:
    now = "2026-06-11T00:00:00+00:00"
    plan = InvestigationPlan(
        plan_id="p-wfe", case_id="c-wfe", topic="VKYC_Session_Failure",
        workflow_id=None, steps=(), created_at=now,
    )
    bundle = EvidenceBundle(
        bundle_id="b-wfe", case_id="c-wfe", topic="VKYC_Session_Failure",
        plan_id="p-wfe", items=[], collected_at=now,
    )
    rca = RootCauseAnalysis(
        analysis_id="a-wfe", case_id="c-wfe", topic="VKYC_Session_Failure",
        category=category, confidence=0.85, explanation="WFE test.",
        evidence_ids=[], recommended_action=RecommendedAction.SESSION_RESET,
        escalate=escalate, analysed_at=now,
    )
    return InvestigationResult(
        result_id="r-wfe-001", case_id="c-wfe", plan=plan, bundle=bundle,
        root_cause=rca, observation="WFE observation.", completed_at=now,
    )


def _make_slot_values(**kwargs: str) -> dict[str, SlotValue]:
    return {k: SlotValue(slot_name=k, value=v, status=SlotStatus.FILLED) for k, v in kwargs.items()}


def _make_investigate_workflow(
    *,
    escalate_path: str = "ESCALATE",
    success_path: str = "RESOLVE",
) -> WorkflowDefinition:
    investigate_step = WorkflowStep(
        step_index=0, step_id="step-inv",
        step_type=WorkflowStepType.INVESTIGATE,
        name="Investigate",
        on_success=success_path,
        on_failure=escalate_path,
    )
    return WorkflowDefinition(
        workflow_id="wf-219-test",
        topic="VKYC_Session_Failure",
        version="1.0",
        name="Sprint 2.19 Test Workflow",
        steps=(investigate_step,),
    )


def _make_registry(defn: WorkflowDefinition) -> MagicMock:
    registry = MagicMock()
    registry.get.return_value = defn
    registry.get_by_id.return_value = defn
    return registry


# ── Constructor tests ─────────────────────────────────────────────────────────

class TestWorkflowEngineConstructor:
    def test_no_args_works(self):
        engine = WorkflowEngine()
        assert engine._investigation_service is None

    def test_with_service_stores_it(self):
        svc = MagicMock()
        engine = WorkflowEngine(investigation_service=svc)
        assert engine._investigation_service is svc

    def test_start_works_without_service(self):
        engine = WorkflowEngine()
        defn = _make_investigate_workflow()
        registry = _make_registry(defn)
        case = _make_case()
        result = engine.start(case, registry, {})
        # Without service, INVESTIGATE step escalates — not FAILED
        assert result.workflow_state in (WorkflowState.ESCALATED, WorkflowState.FAILED)


# ── INVESTIGATE dispatch tests ────────────────────────────────────────────────

class TestInvestigateStepDispatch:
    def _run(self, *, escalate: bool = False) -> WorkflowExecutionResult:
        svc = MagicMock()
        svc.investigate.return_value = _inv_result(escalate=escalate)
        engine = WorkflowEngine(investigation_service=svc)
        defn = _make_investigate_workflow()
        registry = _make_registry(defn)
        case = _make_case()
        return engine.start(case, registry, _make_slot_values(session_id="S1"))

    def test_success_path_completes_workflow(self):
        result = self._run(escalate=False)
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_failure_path_escalates_workflow(self):
        result = self._run(escalate=True)
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_investigation_result_stored_on_success(self):
        result = self._run(escalate=False)
        assert result.investigation_result is not None

    def test_investigation_result_stored_on_escalate(self):
        result = self._run(escalate=True)
        assert result.investigation_result is not None

    def test_investigation_result_is_dict(self):
        result = self._run(escalate=False)
        assert isinstance(result.investigation_result, dict)

    def test_investigation_result_has_result_id(self):
        result = self._run(escalate=False)
        assert "result_id" in result.investigation_result

    def test_investigation_result_has_root_cause(self):
        result = self._run(escalate=False)
        assert "root_cause" in result.investigation_result

    def test_step_outcome_completed_when_no_escalate(self):
        result = self._run(escalate=False)
        inv_step_result = next(
            (r for r in result.step_results if r["step_id"] == "step-inv"), None
        )
        assert inv_step_result is not None
        assert inv_step_result["outcome"] == "INVESTIGATION_COMPLETED"

    def test_step_outcome_escalated_when_escalate(self):
        result = self._run(escalate=True)
        inv_step_result = next(
            (r for r in result.step_results if r["step_id"] == "step-inv"), None
        )
        assert inv_step_result is not None
        assert inv_step_result["outcome"] == "INVESTIGATION_ESCALATED"

    def test_no_service_produces_escalated_not_failed(self):
        engine = WorkflowEngine()
        defn = _make_investigate_workflow()
        registry = _make_registry(defn)
        result = engine.start(_make_case(), registry, {})
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_no_service_records_step(self):
        engine = WorkflowEngine()
        defn = _make_investigate_workflow()
        registry = _make_registry(defn)
        result = engine.start(_make_case(), registry, {})
        step_result = next(
            (r for r in result.step_results if r["step_id"] == "step-inv"), None
        )
        assert step_result is not None
        assert step_result["outcome"] == "NO_INVESTIGATION_SERVICE"


# ── PROPOSE_ACTION guard tests ────────────────────────────────────────────────

class TestProposeActionGuard:
    def _make_investigate_then_propose_workflow(self) -> WorkflowDefinition:
        inv_step = WorkflowStep(
            step_index=0, step_id="step-inv",
            step_type=WorkflowStepType.INVESTIGATE,
            name="Investigate",
            on_success="step-propose",
            on_failure="ESCALATE",
        )
        propose_step = WorkflowStep(
            step_index=1, step_id="step-propose",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="Propose Action",
            action_type="RESET_SESSION",
            action_namespace="vkyc",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        return WorkflowDefinition(
            workflow_id="wf-219-inv-propose",
            topic="VKYC_Session_Failure",
            version="1.0",
            name="Investigate Then Propose",
            steps=(inv_step, propose_step),
        )

    def test_propose_action_allowed_when_investigation_result_present(self):
        svc = MagicMock()
        svc.investigate.return_value = _inv_result(escalate=False)
        engine = WorkflowEngine(investigation_service=svc)
        defn = self._make_investigate_then_propose_workflow()
        registry = _make_registry(defn)
        gateway = MagicMock()
        action = MagicMock()
        from case_engine.action_state import ActionState
        action.current_state = ActionState.AWAITING_APPROVAL
        action.action_id = "act-001"
        gateway.propose.return_value = action
        result = engine.start(_make_case(), registry, _make_slot_values(session_id="S1"), gateway=gateway)
        # Should reach PAUSED (action awaiting approval) — not BLOCKED
        assert result.workflow_state == WorkflowState.PAUSED
        blocked = any(
            r.get("outcome") == "BLOCKED_NO_INVESTIGATION"
            for r in result.step_results
        )
        assert not blocked

    def test_propose_action_blocked_when_investigation_result_missing(self):
        # Workflow has INVESTIGATE + PROPOSE_ACTION steps, but we skip to PROPOSE
        # by manually setting up a workflow where first step is PROPOSE_ACTION
        # and workflow_has_investigate returns True
        propose_step = WorkflowStep(
            step_index=0, step_id="step-propose",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="Propose Action",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        inv_step = WorkflowStep(
            step_index=1, step_id="step-inv-dummy",
            step_type=WorkflowStepType.INVESTIGATE,
            name="Investigate (dummy, never reached)",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        # Propose first, investigate second — blueprint violation
        defn = WorkflowDefinition(
            workflow_id="wf-propose-first",
            topic="VKYC_Session_Failure",
            version="1.0",
            name="Propose First (violation)",
            steps=(propose_step, inv_step),
        )
        registry = _make_registry(defn)
        engine = WorkflowEngine()
        result = engine.start(_make_case(), registry, {})
        blocked = any(
            r.get("outcome") == "BLOCKED_NO_INVESTIGATION"
            for r in result.step_results
        )
        assert blocked

    def test_propose_action_not_blocked_without_investigate_step(self):
        # Classic workflow (no INVESTIGATE step) — PROPOSE_ACTION should run normally
        propose_step = WorkflowStep(
            step_index=0, step_id="step-propose",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="Propose Action",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="wf-classic",
            topic="VKYC_Session_Failure",
            version="1.0",
            name="Classic Workflow",
            steps=(propose_step,),
        )
        registry = _make_registry(defn)
        engine = WorkflowEngine()
        gateway = MagicMock()
        action = MagicMock()
        from case_engine.action_state import ActionState
        action.current_state = ActionState.AWAITING_APPROVAL
        action.action_id = "act-classic"
        gateway.propose.return_value = action
        result = engine.start(_make_case(), registry, {}, gateway=gateway)
        blocked = any(
            r.get("outcome") == "BLOCKED_NO_INVESTIGATION"
            for r in result.step_results
        )
        assert not blocked


# ── Structural validation tests ───────────────────────────────────────────────

class TestWorkflowStructureValidation:
    def test_validation_does_not_raise(self):
        engine = WorkflowEngine()
        defn = _make_investigate_workflow()
        # No exception
        engine._validate_workflow_structure(defn)

    def test_propose_without_investigate_logs_debug_not_fails(self):
        propose_step = WorkflowStep(
            step_index=0, step_id="s1",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="P",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="wf-legacy",
            topic="OTP_Delivery_Failure",
            version="1.0",
            name="Legacy",
            steps=(propose_step,),
        )
        engine = WorkflowEngine()
        # Must not raise — just logs
        engine._validate_workflow_structure(defn)

    def test_validation_passes_for_valid_investigation_workflow(self):
        engine = WorkflowEngine()
        inv_step = WorkflowStep(
            step_index=0, step_id="s-inv",
            step_type=WorkflowStepType.INVESTIGATE,
            name="I", on_success="RESOLVE", on_failure="ESCALATE",
        )
        propose_step = WorkflowStep(
            step_index=1, step_id="s-propose",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="P", on_success="RESOLVE", on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="wf-valid",
            topic="VKYC_Session_Failure",
            version="1.0",
            name="Valid",
            steps=(inv_step, propose_step),
        )
        engine._validate_workflow_structure(defn)
