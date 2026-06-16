"""
tests/test_sprint217_reasoning.py

Sprint 2.17 Part D — Reasoning Framework Tests

Covers:
  - ReasoningContext construction and helper methods
  - ReasoningDecision structure and to_dict
  - ReasoningEngine.analyze()
  - ReasoningEngine.choose_next_step() — all 6 decision paths
  - ReasoningEngine.determine_missing_information()
  - ReasoningEngine.recommend_tool()
  - build_context() and build_context_from_case() helpers
"""
from __future__ import annotations

import pytest

from case_engine.models import Case
from case_engine.case_state import CaseState
from case_engine.slot_filling.models import SlotStatus, SlotValue
from case_engine.tools.tool_models import ToolResult
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState
from case_engine.reasoning.reasoning_models import (
    NextStepType,
    ReasoningContext,
    ReasoningDecision,
    ReasoningStep,
)
from case_engine.reasoning.reasoning_engine import ReasoningEngine
from case_engine.reasoning.reasoning_context import build_context, build_context_from_case


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _filled_slot(name: str, value: str = "test") -> SlotValue:
    sv = SlotValue(slot_name=name)
    sv.status = SlotStatus.FILLED
    sv.value = value
    return sv


def _unfilled_slot(name: str) -> SlotValue:
    sv = SlotValue(slot_name=name)
    sv.status = SlotStatus.PENDING
    return sv


def _vkyc_case(state: CaseState = CaseState.WORKFLOW_ACTIVE) -> Case:
    case = Case(ticket_id="T-001", client="test")
    case.topic = "VKYC_Session_Failure"
    case.current_state = state
    return case


def _tool_result_ok(tool_name: str) -> ToolResult:
    return ToolResult.ok(tool_name, {"result": "fake"})


# ── ReasoningStep ─────────────────────────────────────────────────────────────

class TestReasoningStep:
    def test_construction(self):
        step = ReasoningStep(
            step_name="check",
            observation="obs",
            conclusion="conclude",
            confidence=0.9,
        )
        assert step.step_name == "check"
        assert step.confidence == 0.9

    def test_frozen(self):
        step = ReasoningStep("n", "o", "c", 0.5)
        with pytest.raises((AttributeError, TypeError)):
            step.confidence = 1.0  # type: ignore


# ── ReasoningDecision ─────────────────────────────────────────────────────────

class TestReasoningDecision:
    def test_to_dict_structure(self):
        decision = ReasoningDecision(
            next_step=NextStepType.ASK_FOR_SLOT,
            confidence=1.0,
            rationale="test rationale",
            slot_name="session_id",
            missing_slots=["session_id"],
            reasoning_steps=[
                ReasoningStep("s", "o", "c", 0.9)
            ],
        )
        d = decision.to_dict()
        assert d["next_step"] == "ASK_FOR_SLOT"
        assert d["confidence"] == 1.0
        assert d["slot_name"] == "session_id"
        assert d["missing_slots"] == ["session_id"]
        assert len(d["reasoning_steps"]) == 1

    def test_all_next_step_types(self):
        for nst in NextStepType:
            d = ReasoningDecision(next_step=nst, confidence=0.5, rationale="x")
            assert d.to_dict()["next_step"] == nst.value


# ── ReasoningContext ──────────────────────────────────────────────────────────

class TestReasoningContext:
    def test_slot_names_filled(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            slot_values={
                "session_id":   _filled_slot("session_id"),
                "phone_number": _unfilled_slot("phone_number"),
            },
        )
        assert "session_id" in ctx.slot_names_filled()
        assert "phone_number" not in ctx.slot_names_filled()

    def test_slot_names_missing(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            slot_values={
                "session_id":   _filled_slot("session_id"),
                "phone_number": _unfilled_slot("phone_number"),
            },
        )
        missing = ctx.slot_names_missing()
        assert "phone_number" in missing
        assert "session_id" not in missing

    def test_latest_tool_result_found(self):
        r1 = _tool_result_ok("GetSessionDetailsTool")
        ctx = ReasoningContext(case=_vkyc_case(), tool_results=[r1])
        result = ctx.latest_tool_result("GetSessionDetailsTool")
        assert result is r1

    def test_latest_tool_result_not_found(self):
        ctx = ReasoningContext(case=_vkyc_case())
        assert ctx.latest_tool_result("GetUserDetailsTool") is None

    def test_latest_tool_result_skips_failed(self):
        failed = ToolResult.fail("GetSessionDetailsTool", "ERR", "fail")
        ok = _tool_result_ok("GetSessionDetailsTool")
        ctx = ReasoningContext(case=_vkyc_case(), tool_results=[failed, ok])
        result = ctx.latest_tool_result("GetSessionDetailsTool")
        assert result is ok

    def test_all_tools_run_true(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            tool_results=[
                _tool_result_ok("GetSessionDetailsTool"),
                _tool_result_ok("GetUserDetailsTool"),
            ],
        )
        assert ctx.all_tools_run(["GetSessionDetailsTool", "GetUserDetailsTool"]) is True

    def test_all_tools_run_false_when_missing(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            tool_results=[_tool_result_ok("GetSessionDetailsTool")],
        )
        assert ctx.all_tools_run(["GetSessionDetailsTool", "GetUserDetailsTool"]) is False

    def test_to_dict_has_key_fields(self):
        case = _vkyc_case()
        ctx = ReasoningContext(case=case)
        d = ctx.to_dict()
        assert d["case_id"] == case.case_id
        assert d["topic"] == "VKYC_Session_Failure"
        assert "slots_filled" in d
        assert "tools_run" in d


# ── build_context ─────────────────────────────────────────────────────────────

class TestBuildContext:
    def test_build_context_basic(self):
        case = _vkyc_case()
        ctx = build_context(case)
        assert ctx.case is case
        assert ctx.slot_values == {}
        assert ctx.tool_results == []

    def test_build_context_with_slots(self):
        case = _vkyc_case()
        sv = {"session_id": _filled_slot("session_id")}
        ctx = build_context(case, slot_values=sv)
        assert "session_id" in ctx.slot_values

    def test_build_context_from_case_minimal(self):
        case = _vkyc_case()
        case.slot_state = {}
        case.workflow_context = {}
        ctx = build_context_from_case(case)
        assert ctx.case is case
        assert isinstance(ctx.slot_values, dict)

    def test_build_context_from_case_with_workflow_context(self):
        case = _vkyc_case()
        wer = WorkflowExecutionResult(workflow_id="test_wf_v1")
        case.workflow_context = wer.to_dict()
        ctx = build_context_from_case(case)
        assert ctx.workflow_result is not None
        assert ctx.workflow_result.workflow_id == "test_wf_v1"


# ── ReasoningEngine ───────────────────────────────────────────────────────────

class TestReasoningEngine:
    def setup_method(self):
        self.engine = ReasoningEngine()

    def _vkyc_context_all_filled_all_run(self) -> ReasoningContext:
        case = _vkyc_case()
        return ReasoningContext(
            case=case,
            slot_values={
                "session_id":   _filled_slot("session_id"),
                "phone_number": _filled_slot("phone_number"),
            },
            tool_results=[
                _tool_result_ok("GetSessionDetailsTool"),
                _tool_result_ok("GetUserDetailsTool"),
            ],
        )

    # Rule 1: Workflow completed
    def test_choose_workflow_complete_when_completed(self):
        case = _vkyc_case()
        case.workflow_state = WorkflowState.COMPLETED.value
        ctx = ReasoningContext(case=case)
        decision = self.engine.choose_next_step(ctx)
        assert decision.next_step == NextStepType.WORKFLOW_COMPLETE
        assert decision.confidence == 1.0

    # Rule 2: Workflow paused
    def test_choose_wait_for_action_when_paused(self):
        case = _vkyc_case()
        case.workflow_state = WorkflowState.PAUSED.value
        ctx = ReasoningContext(case=case)
        decision = self.engine.choose_next_step(ctx)
        assert decision.next_step == NextStepType.WAIT_FOR_ACTION

    # Rule 3: Missing slots
    def test_choose_ask_for_slot_when_slots_missing(self):
        case = _vkyc_case()
        ctx = ReasoningContext(
            case=case,
            slot_values={
                "session_id": _unfilled_slot("session_id"),
            },
        )
        decision = self.engine.choose_next_step(ctx)
        assert decision.next_step == NextStepType.ASK_FOR_SLOT
        assert decision.slot_name == "session_id"
        assert "session_id" in decision.missing_slots

    # Rule 4: Tool needed
    def test_choose_run_tool_when_investigation_needed(self):
        case = _vkyc_case()
        case.workflow_state = None
        ctx = ReasoningContext(
            case=case,
            slot_values={
                "session_id":   _filled_slot("session_id"),
                "phone_number": _filled_slot("phone_number"),
            },
            tool_results=[],  # no tools run yet
        )
        decision = self.engine.choose_next_step(ctx)
        assert decision.next_step == NextStepType.RUN_TOOL
        assert decision.tool_name in ("GetSessionDetailsTool", "GetUserDetailsTool")

    # Rule 5: All done → propose action
    def test_choose_propose_action_when_all_done(self):
        ctx = self._vkyc_context_all_filled_all_run()
        ctx.case.workflow_state = None  # not completed/paused
        decision = self.engine.choose_next_step(ctx)
        assert decision.next_step == NextStepType.PROPOSE_ACTION

    def test_analyze_returns_list_with_decision(self):
        case = _vkyc_case()
        case.workflow_state = WorkflowState.COMPLETED.value
        ctx = ReasoningContext(case=case)
        decisions = self.engine.analyze(ctx)
        assert len(decisions) >= 1
        assert decisions[0].next_step == NextStepType.WORKFLOW_COMPLETE

    def test_analyze_never_raises(self):
        # Even with a broken context, analyze should not raise
        class BrokenCase:
            case_id = "broken"
            topic = None
            current_state = CaseState.WORKFLOW_ACTIVE
            workflow_state = None
        ctx = ReasoningContext(case=BrokenCase())  # type: ignore
        decisions = self.engine.analyze(ctx)
        assert isinstance(decisions, list)

    def test_determine_missing_information_empty_when_all_filled(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            slot_values={
                "session_id":   _filled_slot("session_id"),
                "phone_number": _filled_slot("phone_number"),
            },
        )
        missing = self.engine.determine_missing_information(ctx)
        assert missing == []

    def test_determine_missing_information_lists_unfilled(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            slot_values={
                "session_id":   _unfilled_slot("session_id"),
                "phone_number": _filled_slot("phone_number"),
            },
        )
        missing = self.engine.determine_missing_information(ctx)
        assert "session_id" in missing

    def test_recommend_tool_vkyc_no_tools_run(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            slot_values={"session_id": _filled_slot("session_id")},
            tool_results=[],
        )
        tool = self.engine.recommend_tool(ctx)
        assert tool == "GetSessionDetailsTool"

    def test_recommend_tool_vkyc_session_tool_already_run(self):
        ctx = ReasoningContext(
            case=_vkyc_case(),
            tool_results=[_tool_result_ok("GetSessionDetailsTool")],
        )
        tool = self.engine.recommend_tool(ctx)
        assert tool == "GetUserDetailsTool"

    def test_recommend_tool_returns_none_when_all_run(self):
        ctx = self._vkyc_context_all_filled_all_run()
        tool = self.engine.recommend_tool(ctx)
        assert tool is None

    def test_recommend_tool_otp_topic(self):
        case = Case(ticket_id="T-OTP", client="test")
        case.topic = "OTP_Delivery_Failure"
        case.current_state = CaseState.WORKFLOW_ACTIVE
        ctx = ReasoningContext(case=case, tool_results=[])
        tool = self.engine.recommend_tool(ctx)
        assert tool == "GetUserDetailsTool"

    def test_reasoning_steps_in_decision(self):
        case = _vkyc_case()
        case.workflow_state = WorkflowState.COMPLETED.value
        ctx = ReasoningContext(case=case)
        decision = self.engine.choose_next_step(ctx)
        assert len(decision.reasoning_steps) >= 1
        assert decision.reasoning_steps[0].step_name

    def test_choose_produces_valid_confidence(self):
        ctx = self._vkyc_context_all_filled_all_run()
        decision = self.engine.choose_next_step(ctx)
        assert 0.0 <= decision.confidence <= 1.0

    def test_analyze_unknown_topic_does_not_raise(self):
        case = Case()
        case.topic = "Unknown_Topic_Not_In_Map"
        case.current_state = CaseState.WORKFLOW_ACTIVE
        case.workflow_state = None
        ctx = ReasoningContext(case=case)
        decisions = self.engine.analyze(ctx)
        assert isinstance(decisions, list)
        assert len(decisions) >= 1
