"""
tests/test_sprint225_validation.py

Sprint 2.25: Workflow structure validation tests.

Coverage:
  - _validate_workflow_structure: PROPOSE_ACTION without ACTION_GATEWAY → WARNING
  - _validate_workflow_structure: KNOWLEDGE_LOOKUP without REASON → DEBUG (no warning)
  - _validate_workflow_structure: REASON without PROPOSE_ACTION → DEBUG (no warning)
  - _validate_workflow_structure: ACTION_GATEWAY without EXECUTE → DEBUG (no warning)
  - _validate_workflow_structure: EXECUTE without ACTION_GATEWAY → WARNING
  - _validate_workflow_structure: INVESTIGATE without KNOWLEDGE_LOOKUP → DEBUG (no warning)
  - _validate_workflow_structure: full pipeline → no warnings
  - _validate_workflow_structure: never raises
  - All legacy-style workflows (CHECK_CONDITION → PROPOSE_ACTION) don't produce warnings
  - Empty workflow (no steps) doesn't produce warnings
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _defn_with_types(*step_types: WorkflowStepType) -> WorkflowDefinition:
    steps = tuple(
        WorkflowStep(
            step_index=i,
            step_id=f"step_{i}",
            step_type=t,
            name=f"Step {i}",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        for i, t in enumerate(step_types)
    )
    return WorkflowDefinition(
        workflow_id="test_validation",
        topic="VKYC_Session_Failure",
        version="1.0",
        name="Validation Test",
        steps=steps,
    )


def _engine() -> WorkflowEngine:
    return WorkflowEngine()


def _warnings_from_validate(defn: WorkflowDefinition, caplog) -> list[str]:
    engine = _engine()
    with caplog.at_level(logging.WARNING, logger="case_engine.workflows.workflow_engine"):
        engine._validate_workflow_structure(defn)
    return [r.message for r in caplog.records if r.levelno >= logging.WARNING]


# ── PROPOSE_ACTION without ACTION_GATEWAY → WARNING ───────────────────────────

class TestProposeWithoutGateway:
    def test_propose_alone_triggers_warning(self, caplog):
        defn = _defn_with_types(WorkflowStepType.PROPOSE_ACTION)
        warnings = _warnings_from_validate(defn, caplog)
        assert any("ACTION_GATEWAY" in w for w in warnings)

    def test_propose_with_gateway_no_warning(self, caplog):
        defn = _defn_with_types(
            WorkflowStepType.PROPOSE_ACTION,
            WorkflowStepType.ACTION_GATEWAY,
            WorkflowStepType.EXECUTE,
        )
        warnings = _warnings_from_validate(defn, caplog)
        assert not any("PROPOSE_ACTION" in w and "ACTION_GATEWAY" in w for w in warnings)

    def test_warning_message_mentions_blueprint(self, caplog):
        defn = _defn_with_types(WorkflowStepType.PROPOSE_ACTION)
        engine = _engine()
        with caplog.at_level(logging.WARNING, logger="case_engine.workflows.workflow_engine"):
            engine._validate_workflow_structure(defn)
        warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("blueprint" in w.lower() or "ACTION_GATEWAY" in w for w in warning_texts)


# ── EXECUTE without ACTION_GATEWAY → WARNING ──────────────────────────────────

class TestExecuteWithoutGateway:
    def test_execute_without_gateway_triggers_warning(self, caplog):
        defn = _defn_with_types(WorkflowStepType.EXECUTE)
        warnings = _warnings_from_validate(defn, caplog)
        assert any("EXECUTE" in w or "ACTION_GATEWAY" in w for w in warnings)

    def test_execute_with_gateway_no_execute_without_gateway_warning(self, caplog):
        defn = _defn_with_types(
            WorkflowStepType.PROPOSE_ACTION,
            WorkflowStepType.ACTION_GATEWAY,
            WorkflowStepType.EXECUTE,
        )
        warnings = _warnings_from_validate(defn, caplog)
        assert not any("EXECUTE" in w and "without" in w.lower() for w in warnings)


# ── KNOWLEDGE_LOOKUP without REASON → DEBUG only ─────────────────────────────

class TestKnowledgeLookupWithoutReason:
    def test_no_warning_for_knowledge_without_reason(self, caplog):
        defn = _defn_with_types(WorkflowStepType.KNOWLEDGE_LOOKUP)
        warnings = _warnings_from_validate(defn, caplog)
        # No WARNING level log expected (only DEBUG)
        assert len(warnings) == 0

    def test_debug_log_emitted(self, caplog):
        defn = _defn_with_types(WorkflowStepType.KNOWLEDGE_LOOKUP)
        engine = _engine()
        with caplog.at_level(logging.DEBUG, logger="case_engine.workflows.workflow_engine"):
            engine._validate_workflow_structure(defn)
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("KNOWLEDGE" in m or "REASON" in m for m in debug_msgs)


# ── REASON without PROPOSE_ACTION → DEBUG only ───────────────────────────────

class TestReasonWithoutPropose:
    def test_no_warning_for_reason_without_propose(self, caplog):
        defn = _defn_with_types(WorkflowStepType.REASON)
        warnings = _warnings_from_validate(defn, caplog)
        assert len(warnings) == 0


# ── ACTION_GATEWAY without EXECUTE → DEBUG only ───────────────────────────────

class TestGatewayWithoutExecute:
    def test_no_warning_for_gateway_without_execute(self, caplog):
        defn = _defn_with_types(
            WorkflowStepType.PROPOSE_ACTION,
            WorkflowStepType.ACTION_GATEWAY,
        )
        warnings = _warnings_from_validate(defn, caplog)
        # PROPOSE_ACTION without ACTION_GATEWAY warning should not fire here
        # (gateway IS present), and ACTION_GATEWAY without EXECUTE is DEBUG only
        assert not any("ACTION_GATEWAY" in w and "without" in w.lower() for w in warnings)


# ── Full pipeline — no warnings ───────────────────────────────────────────────

class TestFullPipelineNoWarnings:
    def test_full_sprint225_pipeline_zero_warnings(self, caplog):
        defn = _defn_with_types(
            WorkflowStepType.CLARIFY,
            WorkflowStepType.INVESTIGATE,
            WorkflowStepType.KNOWLEDGE_LOOKUP,
            WorkflowStepType.REASON,
            WorkflowStepType.PROPOSE_ACTION,
            WorkflowStepType.ACTION_GATEWAY,
            WorkflowStepType.EXECUTE,
            WorkflowStepType.RESOLVE_CASE,
        )
        warnings = _warnings_from_validate(defn, caplog)
        assert len(warnings) == 0

    def test_no_steps_no_warnings(self, caplog):
        defn = _defn_with_types()
        warnings = _warnings_from_validate(defn, caplog)
        assert len(warnings) == 0


# ── Legacy workflows don't trigger warnings ───────────────────────────────────

class TestLegacyWorkflowsNoWarnings:
    def test_check_condition_resolve_escalate_no_warning(self, caplog):
        defn = _defn_with_types(
            WorkflowStepType.CHECK_CONDITION,
            WorkflowStepType.RESOLVE_CASE,
            WorkflowStepType.ESCALATE_CASE,
        )
        warnings = _warnings_from_validate(defn, caplog)
        assert len(warnings) == 0

    def test_resolve_only_no_warning(self, caplog):
        defn = _defn_with_types(WorkflowStepType.RESOLVE_CASE)
        warnings = _warnings_from_validate(defn, caplog)
        assert len(warnings) == 0


# ── Never raises ──────────────────────────────────────────────────────────────

class TestValidationNeverRaises:
    def test_none_steps_does_not_raise(self):
        defn = WorkflowDefinition(
            workflow_id="t",
            topic="T",
            version="1.0",
            name="T",
            steps=(),
        )
        engine = _engine()
        engine._validate_workflow_structure(defn)  # Must not raise

    def test_all_step_types_no_crash(self, caplog):
        defn = _defn_with_types(*list(WorkflowStepType))
        engine = _engine()
        engine._validate_workflow_structure(defn)  # Must not raise
