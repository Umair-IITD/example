"""
tests/test_sprint219_workflow_types.py

Sprint 2.19 Part 1: WorkflowStepType.INVESTIGATE and WorkflowExecutionResult
investigation_result field.

Coverage:
  - INVESTIGATE exists in WorkflowStepType
  - INVESTIGATE value is "INVESTIGATE"
  - WorkflowExecutionResult has investigation_result field defaulting to None
  - investigation_result survives to_dict() / from_dict() round-trip
  - investigation_result field with real data round-trips correctly
  - Existing step types are unchanged (backwards compat)
  - WorkflowStepType.INVESTIGATE can be constructed from string value
  - to_dict() includes investigation_result key
  - from_dict() round-trip for None investigation_result
  - from_dict() round-trip for non-None investigation_result
"""
from __future__ import annotations

import pytest

from case_engine.workflows.models import (
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStepType,
)


class TestWorkflowStepTypeInvestigate:
    def test_investigate_exists(self):
        assert hasattr(WorkflowStepType, "INVESTIGATE")

    def test_investigate_value(self):
        assert WorkflowStepType.INVESTIGATE.value == "INVESTIGATE"

    def test_investigate_from_string(self):
        assert WorkflowStepType("INVESTIGATE") == WorkflowStepType.INVESTIGATE

    def test_existing_types_unchanged(self):
        assert WorkflowStepType.CHECK_CONDITION.value    == "CHECK_CONDITION"
        assert WorkflowStepType.PROPOSE_ACTION.value     == "PROPOSE_ACTION"
        assert WorkflowStepType.RESOLVE_CASE.value       == "RESOLVE_CASE"
        assert WorkflowStepType.ESCALATE_CASE.value      == "ESCALATE_CASE"
        assert WorkflowStepType.REQUEST_APPROVAL.value   == "REQUEST_APPROVAL"
        assert WorkflowStepType.COLLECT_INFORMATION.value == "COLLECT_INFORMATION"

    def test_investigate_is_str_subclass(self):
        assert isinstance(WorkflowStepType.INVESTIGATE, str)

    def test_total_step_count(self):
        # Sprint 2.25 added CLARIFY — now 12 step types
        types = list(WorkflowStepType)
        assert len(types) == 12


class TestWorkflowExecutionResultInvestigationField:
    def test_default_investigation_result_is_none(self):
        result = WorkflowExecutionResult()
        assert result.investigation_result is None

    def test_investigation_result_can_be_set(self):
        result = WorkflowExecutionResult()
        result.investigation_result = {"result_id": "r-001", "category": "UNKNOWN"}
        assert result.investigation_result["result_id"] == "r-001"

    def test_to_dict_includes_investigation_result_key(self):
        result = WorkflowExecutionResult()
        d = result.to_dict()
        assert "investigation_result" in d

    def test_to_dict_investigation_result_none_when_not_set(self):
        result = WorkflowExecutionResult()
        d = result.to_dict()
        assert d["investigation_result"] is None

    def test_to_dict_investigation_result_preserved(self):
        payload = {
            "result_id": "r-123",
            "root_cause": {"category": "EXPIRED_SESSION", "confidence": 0.9},
            "observation": "Test note.",
        }
        result = WorkflowExecutionResult()
        result.investigation_result = payload
        d = result.to_dict()
        assert d["investigation_result"] == payload

    def test_from_dict_round_trip_none(self):
        result = WorkflowExecutionResult(
            workflow_id="wf-001",
            workflow_state=WorkflowState.RUNNING,
        )
        restored = WorkflowExecutionResult.from_dict(result.to_dict())
        assert restored.investigation_result is None

    def test_from_dict_round_trip_with_data(self):
        payload = {"result_id": "r-abc", "category": "TIMEOUT"}
        result = WorkflowExecutionResult(
            workflow_id="wf-002",
            workflow_state=WorkflowState.RUNNING,
        )
        result.investigation_result = payload
        restored = WorkflowExecutionResult.from_dict(result.to_dict())
        assert restored.investigation_result == payload

    def test_from_dict_missing_key_defaults_to_none(self):
        d = {
            "run_id": "r",
            "workflow_id": "wf",
            "workflow_state": "RUNNING",
            "step_results": [],
            "started_at": "2026-06-11T00:00:00+00:00",
        }
        result = WorkflowExecutionResult.from_dict(d)
        assert result.investigation_result is None

    def test_existing_fields_unaffected(self):
        result = WorkflowExecutionResult(
            workflow_id="wf-003",
            workflow_state=WorkflowState.COMPLETED,
            resolution_note="All good.",
        )
        result.investigation_result = {"key": "val"}
        d = result.to_dict()
        restored = WorkflowExecutionResult.from_dict(d)
        assert restored.workflow_id == "wf-003"
        assert restored.resolution_note == "All good."
        assert restored.investigation_result == {"key": "val"}
