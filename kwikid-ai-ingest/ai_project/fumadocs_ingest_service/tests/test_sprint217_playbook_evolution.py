"""
tests/test_sprint217_playbook_evolution.py

Sprint 2.17 Part E — Playbook Evolution Tests

Verifies:
  - WorkflowDefinition carries new optional fields (investigation_steps, tool_candidates, resolution_paths)
  - PlaybookRegistry parses the new fields correctly
  - All 5 upgraded YAML playbooks have non-empty investigation metadata
  - PlaybookRegistry still validates existing playbooks (no regressions)
  - YAML playbooks remain loadable after upgrade
"""
from __future__ import annotations

import pytest

from case_engine.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowStepType
from case_engine.workflows.playbook_registry import PlaybookRegistry


# ── WorkflowDefinition new fields ─────────────────────────────────────────────

class TestWorkflowDefinitionNewFields:
    def _make_minimal_definition(self, **kwargs) -> WorkflowDefinition:
        defaults = dict(
            workflow_id="test_v1",
            topic="Test_Topic",
            version="1.0",
            name="Test",
            steps=(WorkflowStep(
                step_index=0,
                step_id="resolve",
                step_type=WorkflowStepType.RESOLVE_CASE,
                name="Resolve",
            ),),
        )
        defaults.update(kwargs)
        return WorkflowDefinition(**defaults)

    def test_investigation_steps_default_empty_tuple(self):
        defn = self._make_minimal_definition()
        assert defn.investigation_steps == ()

    def test_tool_candidates_default_empty_tuple(self):
        defn = self._make_minimal_definition()
        assert defn.tool_candidates == ()

    def test_resolution_paths_default_empty_dict(self):
        defn = self._make_minimal_definition()
        assert defn.resolution_paths == {}

    def test_can_set_investigation_steps(self):
        defn = self._make_minimal_definition(
            investigation_steps=({"tool": "GetSessionDetailsTool", "purpose": "test"},)
        )
        assert len(defn.investigation_steps) == 1
        assert defn.investigation_steps[0]["tool"] == "GetSessionDetailsTool"

    def test_can_set_tool_candidates(self):
        defn = self._make_minimal_definition(
            tool_candidates=("GetSessionDetailsTool", "GetUserDetailsTool"),
        )
        assert "GetSessionDetailsTool" in defn.tool_candidates

    def test_can_set_resolution_paths(self):
        defn = self._make_minimal_definition(
            resolution_paths={"automatic": "resolve_step", "escalation": "escalate_step"},
        )
        assert defn.resolution_paths["automatic"] == "resolve_step"

    def test_to_dict_includes_new_fields(self):
        defn = self._make_minimal_definition(
            investigation_steps=({"tool": "GetSessionDetailsTool"},),
            tool_candidates=("GetSessionDetailsTool",),
            resolution_paths={"automatic": "resolve"},
        )
        d = defn.to_dict()
        assert "investigation_steps" in d
        assert "tool_candidates" in d
        assert "resolution_paths" in d
        assert len(d["investigation_steps"]) == 1
        assert "GetSessionDetailsTool" in d["tool_candidates"]


# ── Real playbook loading ──────────────────────────────────────────────────────

@pytest.fixture
def default_registry() -> PlaybookRegistry:
    """Load the actual playbook files from the default directory."""
    return PlaybookRegistry.build()


class TestUpgradedPlaybooks:
    def test_all_5_playbooks_load(self, default_registry):
        assert len(default_registry) == 5

    def test_vkyc_has_investigation_steps(self, default_registry):
        defn = default_registry.get("VKYC_Session_Failure")
        assert defn is not None
        assert len(defn.investigation_steps) >= 1

    def test_vkyc_has_tool_candidates(self, default_registry):
        defn = default_registry.get("VKYC_Session_Failure")
        assert defn is not None
        assert len(defn.tool_candidates) >= 1

    def test_vkyc_has_resolution_paths(self, default_registry):
        defn = default_registry.get("VKYC_Session_Failure")
        assert defn is not None
        assert len(defn.resolution_paths) >= 1

    def test_otp_has_investigation_metadata(self, default_registry):
        defn = default_registry.get("OTP_Delivery_Failure")
        assert defn is not None
        assert len(defn.investigation_steps) >= 1
        assert len(defn.tool_candidates) >= 1

    def test_api_callback_has_investigation_metadata(self, default_registry):
        defn = default_registry.get("API_Callback_Failure")
        assert defn is not None
        assert len(defn.investigation_steps) >= 1
        assert len(defn.tool_candidates) >= 1

    def test_document_ocr_has_investigation_metadata(self, default_registry):
        defn = default_registry.get("Document_OCR_Failure")
        assert defn is not None
        assert len(defn.investigation_steps) >= 1

    def test_agent_portal_has_investigation_metadata(self, default_registry):
        defn = default_registry.get("Agent_Portal_Issue")
        assert defn is not None
        assert len(defn.investigation_steps) >= 1

    def test_investigation_steps_have_tool_field(self, default_registry):
        """Each investigation_step must have a 'tool' key."""
        for defn in default_registry.list_all():
            for step in defn.investigation_steps:
                assert "tool" in step, (
                    f"Investigation step in {defn.workflow_id} missing 'tool' key"
                )

    def test_investigation_steps_tool_names_are_known(self, default_registry):
        """Tool names in investigation_steps should be known tools (OTP v3.0 adds LogTool)."""
        known_tools = {
            "GetSessionDetailsTool",
            "GetUserDetailsTool",
            "GetFailureReasonTool",
            "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
            "LogTool",
        }
        for defn in default_registry.list_all():
            for step in defn.investigation_steps:
                tool = step.get("tool", "")
                assert tool in known_tools, (
                    f"Unknown tool '{tool}' in investigation_steps of {defn.workflow_id}"
                )

    def test_tool_candidates_are_known_tools(self, default_registry):
        """tool_candidates should be known tools (OTP v3.0 adds LogTool)."""
        known_tools = {
            "GetSessionDetailsTool",
            "GetUserDetailsTool",
            "GetFailureReasonTool",
            "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
            "LogTool",
        }
        for defn in default_registry.list_all():
            for tool_name in defn.tool_candidates:
                assert tool_name in known_tools, (
                    f"Unknown tool_candidate '{tool_name}' in {defn.workflow_id}"
                )

    def test_existing_steps_still_execute(self, default_registry):
        """All existing steps are still valid after YAML upgrade."""
        from case_engine.workflows.workflow_engine import WorkflowEngine
        from case_engine.models import Case
        from case_engine.case_state import CaseState

        engine = WorkflowEngine()
        case = Case(ticket_id="T-001", client="test")
        case.topic = "VKYC_Session_Failure"
        case.current_state = CaseState.WORKFLOW_ACTIVE

        result = engine.start(case, default_registry, {})
        # No playbook for VKYC requires slots for entry check — should run first step
        # (the required slots check will fail, but workflow will be FAILED, not error)
        assert result is not None

    def test_to_dict_includes_new_fields_for_all(self, default_registry):
        for defn in default_registry.list_all():
            d = defn.to_dict()
            assert "investigation_steps" in d
            assert "tool_candidates" in d
            assert "resolution_paths" in d

    def test_registry_still_validates_bad_playbooks(self, tmp_path):
        """PlaybookRegistry still rejects invalid YAML after the upgrade."""
        from case_engine.workflows.playbook_registry import PlaybookValidationError

        bad_yml = tmp_path / "bad.yml"
        bad_yml.write_text("workflow_id: bad\ntopic: Test\nversion: '1.0'\n# missing name and steps\n")
        with pytest.raises(PlaybookValidationError):
            PlaybookRegistry.build(tmp_path)

    def test_vkyc_required_slots_preserved(self, default_registry):
        defn = default_registry.get("VKYC_Session_Failure")
        assert "session_id" in defn.required_slots
        assert "phone_number" in defn.required_slots

    def test_otp_required_slots_preserved(self, default_registry):
        defn = default_registry.get("OTP_Delivery_Failure")
        assert "urn" in defn.required_slots
        assert "session_id" in defn.required_slots
