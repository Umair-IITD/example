"""
tests/test_sprint216_workflow_models.py

Sprint 2.16: Workflow domain model unit tests.

Scenarios:
  1.  WorkflowStepType enum coverage
  2.  WorkflowState enum + TERMINAL_WORKFLOW_STATES
  3.  WorkflowCondition.evaluate — eq / neq operators
  4.  WorkflowCondition.evaluate — exists / not_exists
  5.  WorkflowCondition.evaluate — in / not_in
  6.  WorkflowCondition.evaluate — missing field returns False (non-exists ops)
  7.  WorkflowCondition.evaluate — case-insensitive comparison
  8.  WorkflowCondition.to_dict round-trip
  9.  WorkflowStep immutability (frozen dataclass)
  10. WorkflowStep to_dict structure
  11. WorkflowDefinition.step_by_id found / not found
  12. WorkflowDefinition.first_step
  13. WorkflowDefinition.to_dict structure
  14. WorkflowExecutionResult defaults + record_step
  15. WorkflowExecutionResult.is_terminal
  16. WorkflowExecutionResult.to_dict / from_dict round-trip
  17. WorkflowExecutionResult.from_dict — completed_at None preserved
  18. WorkflowExecutionResult.from_dict — missing keys get defaults
  19. WorkflowCondition frozen — cannot mutate
  20. WorkflowDefinition frozen — cannot add steps at runtime
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from case_engine.workflows.models import (
    TERMINAL_WORKFLOW_STATES,
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_condition(field="session_id", operator="exists", value=None, description="") -> WorkflowCondition:
    return WorkflowCondition(field=field, operator=operator, value=value, description=description)


def _make_step(
    step_id="step_1",
    step_type=WorkflowStepType.CHECK_CONDITION,
    on_success="RESOLVE",
    on_failure="ESCALATE",
    conditions=(),
) -> WorkflowStep:
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=step_type,
        name="Test Step",
        description="test",
        conditions=conditions,
        on_success=on_success,
        on_failure=on_failure,
    )


def _make_definition(steps=None, workflow_id="wf_test_v1", topic="Test_Topic") -> WorkflowDefinition:
    if steps is None:
        steps = (_make_step("s1"), _make_step("s2"))
    return WorkflowDefinition(
        workflow_id=workflow_id,
        topic=topic,
        version="1.0",
        name="Test Workflow",
        description="test",
        required_slots=("session_id",),
        steps=tuple(steps),
    )


# ── Test 1: WorkflowStepType enum ─────────────────────────────────────────────

class TestWorkflowStepType:
    def test_all_values_accessible(self):
        assert WorkflowStepType.COLLECT_INFORMATION.value == "COLLECT_INFORMATION"
        assert WorkflowStepType.CHECK_CONDITION.value == "CHECK_CONDITION"
        assert WorkflowStepType.PROPOSE_ACTION.value == "PROPOSE_ACTION"
        assert WorkflowStepType.REQUEST_APPROVAL.value == "REQUEST_APPROVAL"
        assert WorkflowStepType.RESOLVE_CASE.value == "RESOLVE_CASE"
        assert WorkflowStepType.ESCALATE_CASE.value == "ESCALATE_CASE"

    def test_six_step_types(self):
        # Sprint 2.25 added CLARIFY — now 12 step types
        assert len(WorkflowStepType) == 12

    def test_is_str_enum(self):
        assert isinstance(WorkflowStepType.CHECK_CONDITION, str)
        assert WorkflowStepType.CHECK_CONDITION == "CHECK_CONDITION"


# ── Test 2: WorkflowState + TERMINAL_WORKFLOW_STATES ─────────────────────────

class TestWorkflowState:
    def test_all_states_present(self):
        states = {s.value for s in WorkflowState}
        assert states == {"PENDING", "RUNNING", "PAUSED", "COMPLETED", "ESCALATED", "FAILED"}

    def test_terminal_states_are_correct(self):
        assert WorkflowState.COMPLETED in TERMINAL_WORKFLOW_STATES
        assert WorkflowState.ESCALATED in TERMINAL_WORKFLOW_STATES
        assert WorkflowState.FAILED in TERMINAL_WORKFLOW_STATES

    def test_non_terminal_states_excluded(self):
        assert WorkflowState.PENDING not in TERMINAL_WORKFLOW_STATES
        assert WorkflowState.RUNNING not in TERMINAL_WORKFLOW_STATES
        assert WorkflowState.PAUSED not in TERMINAL_WORKFLOW_STATES

    def test_is_str_enum(self):
        assert isinstance(WorkflowState.RUNNING, str)
        assert WorkflowState.RUNNING == "RUNNING"


# ── Test 3: WorkflowCondition.evaluate — eq / neq ────────────────────────────

class TestConditionEqNeq:
    def test_eq_match(self):
        c = _make_condition(field="channel", operator="eq", value="SMS")
        assert c.evaluate({"channel": "SMS"}) is True

    def test_eq_mismatch(self):
        c = _make_condition(field="channel", operator="eq", value="SMS")
        assert c.evaluate({"channel": "EMAIL"}) is False

    def test_neq_match(self):
        c = _make_condition(field="channel", operator="neq", value="SMS")
        assert c.evaluate({"channel": "EMAIL"}) is True

    def test_neq_mismatch(self):
        c = _make_condition(field="channel", operator="neq", value="SMS")
        assert c.evaluate({"channel": "SMS"}) is False


# ── Test 4: WorkflowCondition.evaluate — exists / not_exists ─────────────────

class TestConditionExists:
    def test_exists_when_key_present(self):
        c = _make_condition(field="session_id", operator="exists")
        assert c.evaluate({"session_id": "KID-12345"}) is True

    def test_exists_when_key_absent(self):
        c = _make_condition(field="session_id", operator="exists")
        assert c.evaluate({}) is False

    def test_not_exists_when_absent(self):
        c = _make_condition(field="session_id", operator="not_exists")
        assert c.evaluate({}) is True

    def test_not_exists_when_present(self):
        c = _make_condition(field="session_id", operator="not_exists")
        assert c.evaluate({"session_id": "KID-12345"}) is False


# ── Test 5: WorkflowCondition.evaluate — in / not_in ─────────────────────────

class TestConditionIn:
    def test_in_match(self):
        c = _make_condition(field="status", operator="in", value=["FAILED", "EXPIRED"])
        assert c.evaluate({"status": "FAILED"}) is True

    def test_in_no_match(self):
        c = _make_condition(field="status", operator="in", value=["FAILED", "EXPIRED"])
        assert c.evaluate({"status": "SUCCESS"}) is False

    def test_not_in_match(self):
        c = _make_condition(field="status", operator="not_in", value=["FAILED", "EXPIRED"])
        assert c.evaluate({"status": "SUCCESS"}) is True

    def test_not_in_no_match(self):
        c = _make_condition(field="status", operator="not_in", value=["FAILED", "EXPIRED"])
        assert c.evaluate({"status": "FAILED"}) is False

    def test_in_empty_list_returns_false(self):
        c = _make_condition(field="status", operator="in", value=[])
        assert c.evaluate({"status": "FAILED"}) is False


# ── Test 6: evaluate — missing field returns False for non-exists ops ─────────

class TestConditionMissingField:
    def test_eq_missing_field_returns_false(self):
        c = _make_condition(field="missing_field", operator="eq", value="anything")
        assert c.evaluate({}) is False

    def test_neq_missing_field_returns_false(self):
        c = _make_condition(field="missing_field", operator="neq", value="anything")
        assert c.evaluate({}) is False

    def test_in_missing_field_returns_false(self):
        c = _make_condition(field="missing_field", operator="in", value=["x"])
        assert c.evaluate({}) is False


# ── Test 7: case-insensitive comparison ───────────────────────────────────────

class TestConditionCaseInsensitive:
    def test_eq_case_insensitive(self):
        c = _make_condition(field="channel", operator="eq", value="sms")
        assert c.evaluate({"channel": "SMS"}) is True
        assert c.evaluate({"channel": "sms"}) is True
        assert c.evaluate({"channel": "Sms"}) is True

    def test_in_case_insensitive(self):
        c = _make_condition(field="status", operator="in", value=["failed"])
        assert c.evaluate({"status": "FAILED"}) is True

    def test_neq_case_insensitive(self):
        c = _make_condition(field="ch", operator="neq", value="SMS")
        assert c.evaluate({"ch": "sms"}) is False


# ── Test 8: WorkflowCondition.to_dict ─────────────────────────────────────────

class TestConditionToDict:
    def test_to_dict_contains_all_fields(self):
        c = WorkflowCondition(
            field="session_id",
            operator="eq",
            value="KID-001",
            description="Session must exist",
        )
        d = c.to_dict()
        assert d["field"] == "session_id"
        assert d["operator"] == "eq"
        assert d["value"] == "KID-001"
        assert d["description"] == "Session must exist"


# ── Test 9: WorkflowStep immutability ────────────────────────────────────────

class TestWorkflowStepImmutability:
    def test_cannot_mutate_step(self):
        step = _make_step()
        with pytest.raises((AttributeError, TypeError)):
            step.step_id = "new_id"  # type: ignore[misc]

    def test_conditions_is_tuple(self):
        cond = _make_condition()
        step = _make_step(conditions=(cond,))
        assert isinstance(step.conditions, tuple)


# ── Test 10: WorkflowStep.to_dict ─────────────────────────────────────────────

class TestWorkflowStepToDict:
    def test_to_dict_structure(self):
        step = _make_step(step_id="check_session", step_type=WorkflowStepType.CHECK_CONDITION)
        d = step.to_dict()
        assert d["step_id"] == "check_session"
        assert d["step_type"] == "CHECK_CONDITION"
        assert d["step_index"] == 0
        assert "on_success" in d
        assert "on_failure" in d
        assert "conditions" in d
        assert isinstance(d["conditions"], list)

    def test_to_dict_with_action_fields(self):
        step = WorkflowStep(
            step_index=1,
            step_id="propose_reset",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="Propose Reset",
            action_type="vkyc_session_reset",
            action_namespace="kwikid.vkyc",
            risk_level="REVERSIBLE",
            rollback_action_type="vkyc_session_restore",
        )
        d = step.to_dict()
        assert d["action_type"] == "vkyc_session_reset"
        assert d["action_namespace"] == "kwikid.vkyc"
        assert d["risk_level"] == "REVERSIBLE"


# ── Test 11: WorkflowDefinition.step_by_id ───────────────────────────────────

class TestWorkflowDefinitionStepById:
    def test_found(self):
        step = _make_step("my_step")
        defn = _make_definition(steps=[step])
        found = defn.step_by_id("my_step")
        assert found is not None
        assert found.step_id == "my_step"

    def test_not_found_returns_none(self):
        defn = _make_definition()
        assert defn.step_by_id("nonexistent") is None


# ── Test 12: WorkflowDefinition.first_step ───────────────────────────────────

class TestWorkflowDefinitionFirstStep:
    def test_first_step_returns_index_0(self):
        s1 = _make_step("first")
        s2 = _make_step("second")
        defn = _make_definition(steps=[s1, s2])
        assert defn.first_step().step_id == "first"

    def test_empty_steps_returns_none(self):
        defn = WorkflowDefinition(
            workflow_id="empty_v1",
            topic="Empty",
            version="1.0",
            name="Empty",
            steps=(),
        )
        assert defn.first_step() is None


# ── Test 13: WorkflowDefinition.to_dict ──────────────────────────────────────

class TestWorkflowDefinitionToDict:
    def test_to_dict_has_required_keys(self):
        defn = _make_definition()
        d = defn.to_dict()
        for key in ("workflow_id", "topic", "version", "name", "description", "step_count", "steps"):
            assert key in d

    def test_step_count_matches(self):
        defn = _make_definition()
        d = defn.to_dict()
        assert d["step_count"] == len(defn.steps)
        assert len(d["steps"]) == len(defn.steps)


# ── Test 14: WorkflowExecutionResult defaults + record_step ──────────────────

class TestWorkflowExecutionResultDefaults:
    def test_default_state_is_pending(self):
        result = WorkflowExecutionResult()
        assert result.workflow_state == WorkflowState.PENDING

    def test_run_id_is_uuid(self):
        result = WorkflowExecutionResult()
        uuid.UUID(result.run_id)  # raises if not valid UUID

    def test_record_step_appends(self):
        result = WorkflowExecutionResult()
        result.record_step("step_1", "PASS", {"info": "ok"})
        assert len(result.step_results) == 1
        assert result.step_results[0]["step_id"] == "step_1"
        assert result.step_results[0]["outcome"] == "PASS"
        assert result.step_results[0]["detail"] == {"info": "ok"}

    def test_record_step_none_detail(self):
        result = WorkflowExecutionResult()
        result.record_step("step_1", "FAIL")
        assert result.step_results[0]["detail"] == {}

    def test_multiple_record_steps(self):
        result = WorkflowExecutionResult()
        result.record_step("s1", "PASS")
        result.record_step("s2", "FAIL")
        assert len(result.step_results) == 2
        assert result.step_results[0]["step_id"] == "s1"
        assert result.step_results[1]["step_id"] == "s2"


# ── Test 15: WorkflowExecutionResult.is_terminal ─────────────────────────────

class TestWorkflowExecutionResultIsTerminal:
    def test_completed_is_terminal(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.COMPLETED)
        assert result.is_terminal() is True

    def test_escalated_is_terminal(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.ESCALATED)
        assert result.is_terminal() is True

    def test_failed_is_terminal(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.FAILED)
        assert result.is_terminal() is True

    def test_running_is_not_terminal(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.RUNNING)
        assert result.is_terminal() is False

    def test_paused_is_not_terminal(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.PAUSED)
        assert result.is_terminal() is False


# ── Test 16: WorkflowExecutionResult.to_dict / from_dict round-trip ──────────

class TestWorkflowExecutionResultSerialization:
    def test_to_dict_contains_required_keys(self):
        result = WorkflowExecutionResult(
            workflow_id="vkyc_session_failure_v1",
            workflow_state=WorkflowState.RUNNING,
        )
        d = result.to_dict()
        for key in ("run_id", "workflow_id", "workflow_state", "current_step_id",
                    "step_results", "pending_action_id", "started_at",
                    "completed_at", "resolution_note", "escalation_reason"):
            assert key in d

    def test_from_dict_round_trip(self):
        result = WorkflowExecutionResult(
            workflow_id="otp_v1",
            workflow_state=WorkflowState.PAUSED,
            current_step_id="check_channel",
            pending_action_id="act-abc",
            resolution_note=None,
            escalation_reason=None,
        )
        result.record_step("step_a", "PASS")
        d = result.to_dict()
        restored = WorkflowExecutionResult.from_dict(d)
        assert restored.run_id == result.run_id
        assert restored.workflow_id == result.workflow_id
        assert restored.workflow_state == WorkflowState.PAUSED
        assert restored.current_step_id == "check_channel"
        assert restored.pending_action_id == "act-abc"
        assert len(restored.step_results) == 1

    def test_workflow_state_value_stored_as_string(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.COMPLETED)
        d = result.to_dict()
        assert isinstance(d["workflow_state"], str)
        assert d["workflow_state"] == "COMPLETED"

    def test_started_at_stored_as_isoformat(self):
        result = WorkflowExecutionResult()
        d = result.to_dict()
        assert isinstance(d["started_at"], str)
        datetime.fromisoformat(d["started_at"])  # must not raise


# ── Test 17: from_dict — completed_at None preserved ─────────────────────────

class TestWorkflowExecutionResultFromDictNone:
    def test_completed_at_none_preserved(self):
        result = WorkflowExecutionResult(workflow_state=WorkflowState.RUNNING)
        d = result.to_dict()
        assert d["completed_at"] is None
        restored = WorkflowExecutionResult.from_dict(d)
        assert restored.completed_at is None

    def test_resolution_note_none_preserved(self):
        result = WorkflowExecutionResult()
        restored = WorkflowExecutionResult.from_dict(result.to_dict())
        assert restored.resolution_note is None

    def test_escalation_reason_none_preserved(self):
        result = WorkflowExecutionResult()
        restored = WorkflowExecutionResult.from_dict(result.to_dict())
        assert restored.escalation_reason is None


# ── Test 18: from_dict — missing keys get defaults ───────────────────────────

class TestWorkflowExecutionResultFromDictDefaults:
    def test_empty_dict_gets_defaults(self):
        result = WorkflowExecutionResult.from_dict({})
        assert result.workflow_id == ""
        assert result.workflow_state == WorkflowState.PENDING
        assert result.step_results == []
        assert result.pending_action_id is None

    def test_partial_dict_fills_missing_keys(self):
        d = {"workflow_id": "test_v1", "workflow_state": "RUNNING"}
        result = WorkflowExecutionResult.from_dict(d)
        assert result.workflow_id == "test_v1"
        assert result.workflow_state == WorkflowState.RUNNING
        assert result.step_results == []


# ── Test 19: WorkflowCondition frozen ────────────────────────────────────────

class TestWorkflowConditionFrozen:
    def test_cannot_mutate_field(self):
        c = _make_condition()
        with pytest.raises((AttributeError, TypeError)):
            c.field = "new_field"  # type: ignore[misc]

    def test_cannot_mutate_operator(self):
        c = _make_condition()
        with pytest.raises((AttributeError, TypeError)):
            c.operator = "neq"  # type: ignore[misc]


# ── Test 20: WorkflowDefinition frozen ───────────────────────────────────────

class TestWorkflowDefinitionFrozen:
    def test_cannot_mutate_topic(self):
        defn = _make_definition()
        with pytest.raises((AttributeError, TypeError)):
            defn.topic = "New_Topic"  # type: ignore[misc]

    def test_cannot_replace_steps(self):
        defn = _make_definition()
        with pytest.raises((AttributeError, TypeError)):
            defn.steps = ()  # type: ignore[misc]
