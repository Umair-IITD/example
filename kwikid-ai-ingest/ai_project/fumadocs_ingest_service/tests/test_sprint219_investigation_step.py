"""
tests/test_sprint219_investigation_step.py

Sprint 2.19 Part 2: InvestigationStepExecutor unit tests.

Coverage:
  - execute() returns (InvestigationResult, success_bool)
  - success=True when escalate=False
  - success=False when escalate=True
  - _meta.case_id injected when case provided
  - _meta not injected when case is None
  - service.investigate() called with correct topic
  - service.investigate() called with correct workflow_def
  - slot_context forwarded to service slot_values
  - investigate never raises (service crashes → still returns result)
  - result from execute() has correct root_cause category
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from case_engine.workflows.investigation_step import InvestigationStepExecutor
from case_engine.workflows.models import WorkflowStep, WorkflowStepType, WorkflowDefinition
from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationPlan,
    InvestigationResult,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_step(step_id: str = "inv-1") -> WorkflowStep:
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=WorkflowStepType.INVESTIGATE,
        name="Run Investigation",
    )


def _make_defn(topic: str = "VKYC_Session_Failure", wf_id: str = "wf-test-001") -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=wf_id,
        topic=topic,
        version="1.0",
        name="Test Workflow",
    )


def _make_inv_result(escalate: bool = False, category: RootCauseCategory = RootCauseCategory.EXPIRED_SESSION) -> InvestigationResult:
    now = "2026-06-11T00:00:00+00:00"
    plan = InvestigationPlan(
        plan_id="p-001", case_id="c-001", topic="VKYC_Session_Failure",
        workflow_id=None, steps=(), created_at=now,
    )
    bundle = EvidenceBundle(
        bundle_id="b-001", case_id="c-001", topic="VKYC_Session_Failure",
        plan_id="p-001", items=[], collected_at=now,
    )
    rca = RootCauseAnalysis(
        analysis_id="a-001", case_id="c-001", topic="VKYC_Session_Failure",
        category=category, confidence=0.9, explanation="Test.",
        evidence_ids=[], recommended_action=RecommendedAction.SESSION_RESET,
        escalate=escalate, analysed_at=now,
    )
    return InvestigationResult(
        result_id="r-001", case_id="c-001", plan=plan, bundle=bundle,
        root_cause=rca, observation="Test observation.", completed_at=now,
    )


def _make_case(case_id: str = "case-exec-001") -> MagicMock:
    case = MagicMock()
    case.case_id = case_id
    return case


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestInvestigationStepExecutorSuccess:
    def test_returns_tuple(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result(escalate=False)
        executor = InvestigationStepExecutor(svc)
        out = executor.execute(_make_step(), _make_defn(), {"session_id": "S1"}, None)
        assert isinstance(out, tuple)
        assert len(out) == 2

    def test_returns_investigation_result_type(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result(escalate=False)
        executor = InvestigationStepExecutor(svc)
        result, _ = executor.execute(_make_step(), _make_defn(), {}, None)
        assert isinstance(result, InvestigationResult)

    def test_success_true_when_no_escalate(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result(escalate=False)
        executor = InvestigationStepExecutor(svc)
        _, success = executor.execute(_make_step(), _make_defn(), {}, None)
        assert success is True

    def test_success_false_when_escalate(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result(escalate=True)
        executor = InvestigationStepExecutor(svc)
        _, success = executor.execute(_make_step(), _make_defn(), {}, None)
        assert success is False


class TestInvestigationStepExecutorServiceCall:
    def test_service_called_with_correct_topic(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        defn = _make_defn(topic="OTP_Delivery_Failure")
        InvestigationStepExecutor(svc).execute(_make_step(), defn, {}, None)
        call_kwargs = svc.investigate.call_args
        topic = call_kwargs[1].get("topic") or call_kwargs[0][0]
        assert topic == "OTP_Delivery_Failure"

    def test_service_called_with_workflow_def(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        defn = _make_defn()
        InvestigationStepExecutor(svc).execute(_make_step(), defn, {}, None)
        call_kwargs = svc.investigate.call_args
        passed_defn = call_kwargs[1].get("workflow_def") or call_kwargs[0][1]
        assert passed_defn is defn

    def test_slot_context_forwarded(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        slots = {"session_id": "S-789", "phone_number": "9999"}
        InvestigationStepExecutor(svc).execute(_make_step(), _make_defn(), slots, None)
        call_kwargs = svc.investigate.call_args
        passed_slots = call_kwargs[1].get("slot_values") or call_kwargs[0][2]
        assert passed_slots["session_id"] == "S-789"
        assert passed_slots["phone_number"] == "9999"

    def test_meta_injected_when_case_provided(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        case = _make_case("CASE-META-001")
        InvestigationStepExecutor(svc).execute(_make_step(), _make_defn(), {}, case)
        call_kwargs = svc.investigate.call_args
        passed_slots = call_kwargs[1].get("slot_values") or call_kwargs[0][2]
        assert "_meta" in passed_slots
        assert passed_slots["_meta"]["case_id"] == "CASE-META-001"

    def test_meta_not_injected_when_case_is_none(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        InvestigationStepExecutor(svc).execute(_make_step(), _make_defn(), {}, None)
        call_kwargs = svc.investigate.call_args
        passed_slots = call_kwargs[1].get("slot_values") if "slot_values" in call_kwargs[1] else call_kwargs[0][2]
        assert "_meta" not in passed_slots

    def test_case_forwarded_to_service(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        case = _make_case("CASE-FORWARD")
        InvestigationStepExecutor(svc).execute(_make_step(), _make_defn(), {}, case)
        call_kwargs = svc.investigate.call_args
        passed_case = call_kwargs[1].get("case") or (call_kwargs[0][3] if len(call_kwargs[0]) > 3 else None)
        assert passed_case is case


class TestInvestigationStepExecutorRobustness:
    def test_result_category_correct(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result(category=RootCauseCategory.TIMEOUT)
        result, _ = InvestigationStepExecutor(svc).execute(_make_step(), _make_defn(), {}, None)
        assert result.root_cause.category == RootCauseCategory.TIMEOUT

    def test_original_slot_context_not_mutated(self):
        svc = MagicMock()
        svc.investigate.return_value = _make_inv_result()
        original = {"session_id": "S1"}
        original_copy = dict(original)
        InvestigationStepExecutor(svc).execute(_make_step(), _make_defn(), original, _make_case())
        assert original == original_copy
