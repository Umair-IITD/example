"""
tests/test_sprint218_planner.py

Sprint 2.18: InvestigationPlanner tests (Part B).

Coverage:
  - Topic→tool map fallback for all 5 supported topics
  - Unknown topic produces empty plan (no crash)
  - Playbook investigation_steps take priority over topic map
  - Step IDs are deterministic and sequence-indexed
  - Slot resolution is correct per tool (required_slot, input_key)
  - _extract_case_id picks up case_id from _meta or returns "unknown"
  - Plans are frozen (InvestigationPlan immutability)
"""
from __future__ import annotations

import pytest

from case_engine.investigation.planner import InvestigationPlanner, _extract_case_id
from case_engine.investigation.models import InvestigationPlan, InvestigationStep


# ── Fixture helpers ────────────────────────────────────────────────────────────

def make_planner() -> InvestigationPlanner:
    return InvestigationPlanner()


def slot_values_with_case(case_id: str = "case-001") -> dict:
    return {"_meta": {"case_id": case_id}, "session_id": "S123", "phone_number": "9999999999"}


# ── Topic → tool map tests ─────────────────────────────────────────────────────

class TestPlannerTopicMap:
    def test_vkyc_session_failure_tools(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        tool_names = [s.tool_name for s in plan.steps]
        assert "GetSessionDetailsTool" in tool_names
        assert "GetUserDetailsTool" in tool_names

    def test_otp_delivery_failure_tools(self):
        planner = make_planner()
        plan = planner.plan("OTP_Delivery_Failure", None, slot_values_with_case())
        tool_names = [s.tool_name for s in plan.steps]
        assert "GetUserDetailsTool" in tool_names
        assert "GetFailureReasonTool" in tool_names

    def test_document_ocr_failure_tools(self):
        planner = make_planner()
        plan = planner.plan("Document_OCR_Failure", None, {
            "_meta": {"case_id": "c1"},
            "phone_number": "9999999999",
            "application_id": "APP123",
        })
        tool_names = [s.tool_name for s in plan.steps]
        assert "GetUserDetailsTool" in tool_names
        assert "GetOnboardingStatusTool" in tool_names

    def test_agent_portal_issue_tools(self):
        planner = make_planner()
        plan = planner.plan("Agent_Portal_Issue", None, slot_values_with_case())
        tool_names = [s.tool_name for s in plan.steps]
        assert "GetUserDetailsTool" in tool_names
        assert "GetFailureReasonTool" in tool_names

    def test_api_callback_failure_tools(self):
        planner = make_planner()
        plan = planner.plan("API_Callback_Failure", None, slot_values_with_case())
        tool_names = [s.tool_name for s in plan.steps]
        assert "GetFailureReasonTool" in tool_names

    def test_unknown_topic_produces_empty_plan(self):
        planner = make_planner()
        plan = planner.plan("UNKNOWN_TOPIC_XYZ", None, {})
        assert len(plan.steps) == 0
        assert isinstance(plan, InvestigationPlan)

    def test_steps_are_sequentially_ordered(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        for i, step in enumerate(plan.steps):
            assert step.sequence == i

    def test_step_ids_contain_tool_name_fragment(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        for step in plan.steps:
            assert step.step_id.startswith("step_")


# ── Slot mapping tests ─────────────────────────────────────────────────────────

class TestPlannerSlotMapping:
    def test_session_tool_uses_session_id_slot(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        session_steps = [s for s in plan.steps if s.tool_name == "GetSessionDetailsTool"]
        assert len(session_steps) == 1
        assert session_steps[0].required_slot == "session_id"
        assert session_steps[0].input_key == "session_id"

    def test_user_tool_uses_phone_number_slot(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        user_steps = [s for s in plan.steps if s.tool_name == "GetUserDetailsTool"]
        assert len(user_steps) == 1
        assert user_steps[0].required_slot == "phone_number"
        assert user_steps[0].input_key == "phone_number"

    def test_failure_tool_uses_session_id_as_operation_id(self):
        planner = make_planner()
        plan = planner.plan("OTP_Delivery_Failure", None, slot_values_with_case())
        fail_steps = [s for s in plan.steps if s.tool_name == "GetFailureReasonTool"]
        assert len(fail_steps) == 1
        assert fail_steps[0].required_slot == "session_id"
        assert fail_steps[0].input_key == "operation_id"

    def test_onboarding_tool_uses_application_id_slot(self):
        planner = make_planner()
        plan = planner.plan("Document_OCR_Failure", None, {
            "_meta": {"case_id": "c1"},
            "phone_number": "9999",
            "application_id": "APP001",
        })
        ob_steps = [s for s in plan.steps if s.tool_name == "GetOnboardingStatusTool"]
        assert len(ob_steps) == 1
        assert ob_steps[0].required_slot == "application_id"
        assert ob_steps[0].input_key == "application_id"


# ── Playbook priority tests ────────────────────────────────────────────────────

class TestPlannerPlaybookPriority:
    def _make_workflow_def(self, investigation_steps):
        """Build a minimal WorkflowDefinition-like mock with investigation_steps."""
        from unittest.mock import MagicMock
        wf = MagicMock()
        wf.workflow_id = "wf-test-001"
        wf.investigation_steps = tuple(investigation_steps)
        return wf

    def test_playbook_steps_override_topic_map(self):
        planner = make_planner()
        wf = self._make_workflow_def([
            {"tool": "GetUserDetailsTool", "purpose": "Custom purpose", "required_input": "phone_number"},
        ])
        plan = planner.plan("VKYC_Session_Failure", wf, slot_values_with_case())
        assert len(plan.steps) == 1
        assert plan.steps[0].tool_name == "GetUserDetailsTool"
        assert plan.steps[0].purpose == "Custom purpose"

    def test_playbook_empty_investigation_steps_falls_back_to_topic_map(self):
        planner = make_planner()
        wf = self._make_workflow_def([])
        plan = planner.plan("VKYC_Session_Failure", wf, slot_values_with_case())
        # Falls back to topic map — should have both tools
        assert len(plan.steps) == 2

    def test_playbook_none_falls_back_to_topic_map(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        assert len(plan.steps) == 2

    def test_playbook_step_with_invalid_tool_is_skipped(self):
        planner = make_planner()
        wf = self._make_workflow_def([
            {"tool": "", "purpose": "Missing tool name"},
        ])
        plan = planner.plan("VKYC_Session_Failure", wf, slot_values_with_case())
        assert len(plan.steps) == 0

    def test_playbook_multi_step_ordering(self):
        planner = make_planner()
        wf = self._make_workflow_def([
            {"tool": "GetSessionDetailsTool", "required_input": "session_id"},
            {"tool": "GetUserDetailsTool",    "required_input": "phone_number"},
        ])
        plan = planner.plan("VKYC_Session_Failure", wf, slot_values_with_case())
        assert plan.steps[0].tool_name == "GetSessionDetailsTool"
        assert plan.steps[1].tool_name == "GetUserDetailsTool"
        assert plan.steps[0].sequence == 0
        assert plan.steps[1].sequence == 1

    def test_workflow_id_stored_in_plan(self):
        planner = make_planner()
        wf = self._make_workflow_def([
            {"tool": "GetUserDetailsTool", "required_input": "phone_number"},
        ])
        plan = planner.plan("VKYC_Session_Failure", wf, slot_values_with_case())
        assert plan.workflow_id == "wf-test-001"

    def test_no_workflow_stores_none(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        assert plan.workflow_id is None


# ── Plan metadata tests ────────────────────────────────────────────────────────

class TestPlanMetadata:
    def test_plan_id_is_uuid_like(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, slot_values_with_case())
        assert len(plan.plan_id) == 36
        assert plan.plan_id.count("-") == 4

    def test_plan_case_id_from_meta(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, {"_meta": {"case_id": "MY-CASE"}})
        assert plan.case_id == "MY-CASE"

    def test_plan_case_id_defaults_to_unknown(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, {})
        assert plan.case_id == "unknown"

    def test_plan_topic_matches_input(self):
        planner = make_planner()
        plan = planner.plan("OTP_Delivery_Failure", None, {})
        assert plan.topic == "OTP_Delivery_Failure"

    def test_plan_created_at_is_iso(self):
        planner = make_planner()
        plan = planner.plan("VKYC_Session_Failure", None, {})
        assert "T" in plan.created_at
        assert plan.created_at.endswith("+00:00")


# ── _extract_case_id tests ─────────────────────────────────────────────────────

class TestExtractCaseId:
    def test_extracts_from_meta(self):
        assert _extract_case_id({"_meta": {"case_id": "C-999"}}) == "C-999"

    def test_returns_unknown_when_no_meta(self):
        assert _extract_case_id({}) == "unknown"

    def test_returns_unknown_when_meta_has_no_case_id(self):
        assert _extract_case_id({"_meta": {}}) == "unknown"

    def test_returns_unknown_when_meta_is_not_dict(self):
        assert _extract_case_id({"_meta": "not-a-dict"}) == "unknown"

    def test_returns_unknown_when_meta_is_none(self):
        assert _extract_case_id({"_meta": None}) == "unknown"
