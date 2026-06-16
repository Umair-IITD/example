"""
tests/test_sprint225_models.py

Sprint 2.25: Domain model tests.

Coverage:
  - ClarificationStatus enum values
  - MissingSlotInfo frozen dataclass construction, to_dict, from_dict roundtrip
  - ClarificationResult frozen dataclass construction, to_dict, from_dict roundtrip
  - WorkflowStepType.CLARIFY (12th step type, total == 12)
  - AuditEventType: 4 new clarification events present
  - WorkflowExecutionResult.clarification_result field present, persists in to_dict/from_dict
"""
from __future__ import annotations

import pytest

from case_engine.clarification.models import (
    ClarificationResult,
    ClarificationStatus,
    MissingSlotInfo,
)
from case_engine.models import AuditEventType
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowStepType


# ── ClarificationStatus ───────────────────────────────────────────────────────

class TestClarificationStatus:
    def test_ready_value(self):
        assert ClarificationStatus.READY.value == "READY"

    def test_needs_clarification_value(self):
        assert ClarificationStatus.NEEDS_CLARIFICATION.value == "NEEDS_CLARIFICATION"

    def test_escalate_value(self):
        assert ClarificationStatus.ESCALATE.value == "ESCALATE"

    def test_three_statuses(self):
        assert len(ClarificationStatus) == 3

    def test_is_str_enum(self):
        assert isinstance(ClarificationStatus.READY, str)
        assert ClarificationStatus.READY == "READY"

    def test_from_value(self):
        assert ClarificationStatus("READY") is ClarificationStatus.READY
        assert ClarificationStatus("NEEDS_CLARIFICATION") is ClarificationStatus.NEEDS_CLARIFICATION
        assert ClarificationStatus("ESCALATE") is ClarificationStatus.ESCALATE


# ── MissingSlotInfo ───────────────────────────────────────────────────────────

class TestMissingSlotInfo:
    def test_basic_construction(self):
        info = MissingSlotInfo(slot_name="session_id", prompt_text="Please provide session ID.")
        assert info.slot_name == "session_id"
        assert info.prompt_text == "Please provide session ID."
        assert info.attempt_count == 0
        assert info.max_attempts == 2
        assert info.valid_values is None

    def test_with_valid_values(self):
        info = MissingSlotInfo(
            slot_name="channel",
            prompt_text="Choose channel.",
            valid_values=("SMS", "EMAIL", "VOICE"),
        )
        assert info.valid_values == ("SMS", "EMAIL", "VOICE")

    def test_frozen(self):
        info = MissingSlotInfo(slot_name="session_id", prompt_text="P")
        with pytest.raises((AttributeError, TypeError)):
            info.slot_name = "other"  # type: ignore[misc]

    def test_to_dict_basic(self):
        info = MissingSlotInfo(slot_name="session_id", prompt_text="Please provide session ID.")
        d = info.to_dict()
        assert d["slot_name"] == "session_id"
        assert d["prompt_text"] == "Please provide session ID."
        assert d["attempt_count"] == 0
        assert d["max_attempts"] == 2
        assert "valid_values" not in d

    def test_to_dict_with_valid_values(self):
        info = MissingSlotInfo(
            slot_name="channel",
            prompt_text="Choose.",
            valid_values=("SMS", "EMAIL"),
        )
        d = info.to_dict()
        assert d["valid_values"] == ["SMS", "EMAIL"]

    def test_from_dict_roundtrip(self):
        info = MissingSlotInfo(
            slot_name="phone_number",
            prompt_text="Please provide phone.",
            attempt_count=1,
            max_attempts=3,
        )
        restored = MissingSlotInfo.from_dict(info.to_dict())
        assert restored.slot_name == info.slot_name
        assert restored.prompt_text == info.prompt_text
        assert restored.attempt_count == info.attempt_count
        assert restored.max_attempts == info.max_attempts
        assert restored.valid_values is None

    def test_from_dict_with_valid_values_roundtrip(self):
        info = MissingSlotInfo(
            slot_name="channel",
            prompt_text="Choose.",
            valid_values=("SMS", "EMAIL", "VOICE"),
        )
        restored = MissingSlotInfo.from_dict(info.to_dict())
        assert restored.valid_values == ("SMS", "EMAIL", "VOICE")

    def test_from_dict_empty(self):
        info = MissingSlotInfo.from_dict({})
        assert info.slot_name == ""
        assert info.attempt_count == 0
        assert info.max_attempts == 2


# ── ClarificationResult ───────────────────────────────────────────────────────

class TestClarificationResult:
    def _ready(self) -> ClarificationResult:
        return ClarificationResult(
            result_id="rid-1",
            status=ClarificationStatus.READY,
            missing_slots=(),
            next_question=None,
            clarification_message="All required information is available.",
            ready_to_continue=True,
        )

    def _needs(self) -> ClarificationResult:
        q = MissingSlotInfo(slot_name="session_id", prompt_text="Please provide session ID.")
        return ClarificationResult(
            result_id="rid-2",
            status=ClarificationStatus.NEEDS_CLARIFICATION,
            missing_slots=("session_id",),
            next_question=q,
            clarification_message="Please provide session ID.",
            ready_to_continue=False,
        )

    def test_ready_construction(self):
        r = self._ready()
        assert r.status is ClarificationStatus.READY
        assert r.ready_to_continue is True
        assert r.missing_slots == ()
        assert r.next_question is None

    def test_needs_construction(self):
        r = self._needs()
        assert r.status is ClarificationStatus.NEEDS_CLARIFICATION
        assert r.ready_to_continue is False
        assert "session_id" in r.missing_slots
        assert r.next_question is not None

    def test_frozen(self):
        r = self._ready()
        with pytest.raises((AttributeError, TypeError)):
            r.status = ClarificationStatus.ESCALATE  # type: ignore[misc]

    def test_to_dict_ready(self):
        d = self._ready().to_dict()
        assert d["status"] == "READY"
        assert d["ready_to_continue"] is True
        assert d["missing_slots"] == []
        assert d["next_question"] is None
        assert "clarification_message" in d
        assert "result_id" in d
        assert "created_at" in d

    def test_to_dict_needs(self):
        d = self._needs().to_dict()
        assert d["status"] == "NEEDS_CLARIFICATION"
        assert d["ready_to_continue"] is False
        assert "session_id" in d["missing_slots"]
        assert d["next_question"] is not None
        assert d["next_question"]["slot_name"] == "session_id"

    def test_from_dict_ready_roundtrip(self):
        r = self._ready()
        restored = ClarificationResult.from_dict(r.to_dict())
        assert restored.status is ClarificationStatus.READY
        assert restored.ready_to_continue is True
        assert restored.next_question is None

    def test_from_dict_needs_roundtrip(self):
        r = self._needs()
        restored = ClarificationResult.from_dict(r.to_dict())
        assert restored.status is ClarificationStatus.NEEDS_CLARIFICATION
        assert "session_id" in restored.missing_slots
        assert restored.next_question is not None
        assert restored.next_question.slot_name == "session_id"

    def test_escalate_result(self):
        r = ClarificationResult(
            result_id="rid-3",
            status=ClarificationStatus.ESCALATE,
            missing_slots=("session_id",),
            next_question=None,
            clarification_message="Max attempts exceeded.",
            ready_to_continue=False,
        )
        assert r.status is ClarificationStatus.ESCALATE
        d = r.to_dict()
        assert d["status"] == "ESCALATE"
        restored = ClarificationResult.from_dict(d)
        assert restored.status is ClarificationStatus.ESCALATE

    def test_created_at_populated(self):
        r = self._ready()
        assert r.created_at
        assert "T" in r.created_at  # ISO format

    def test_from_dict_empty(self):
        r = ClarificationResult.from_dict({})
        assert isinstance(r.result_id, str)
        assert r.status is ClarificationStatus.READY


# ── WorkflowStepType.CLARIFY ──────────────────────────────────────────────────

class TestWorkflowStepTypeClarify:
    def test_clarify_exists(self):
        assert hasattr(WorkflowStepType, "CLARIFY")
        assert WorkflowStepType.CLARIFY.value == "CLARIFY"

    def test_clarify_is_str(self):
        assert isinstance(WorkflowStepType.CLARIFY, str)
        assert WorkflowStepType.CLARIFY == "CLARIFY"

    def test_total_is_twelve(self):
        assert len(WorkflowStepType) == 12

    def test_all_original_types_present(self):
        types = {t.value for t in WorkflowStepType}
        for expected in [
            "COLLECT_INFORMATION", "CHECK_CONDITION", "PROPOSE_ACTION",
            "REQUEST_APPROVAL", "RESOLVE_CASE", "ESCALATE_CASE",
            "INVESTIGATE", "KNOWLEDGE_LOOKUP", "ACTION_GATEWAY",
            "EXECUTE", "REASON", "CLARIFY",
        ]:
            assert expected in types


# ── AuditEventType clarification events ──────────────────────────────────────

class TestAuditEventTypeClarification:
    def test_clarification_started_exists(self):
        assert hasattr(AuditEventType, "CLARIFICATION_STARTED")
        assert AuditEventType.CLARIFICATION_STARTED.value == "CLARIFICATION_STARTED"

    def test_clarification_completed_exists(self):
        assert hasattr(AuditEventType, "CLARIFICATION_COMPLETED")
        assert AuditEventType.CLARIFICATION_COMPLETED.value == "CLARIFICATION_COMPLETED"

    def test_workflow_clarification_started_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_CLARIFICATION_STARTED")
        assert AuditEventType.WORKFLOW_CLARIFICATION_STARTED.value == "WORKFLOW_CLARIFICATION_STARTED"

    def test_workflow_clarification_completed_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_CLARIFICATION_COMPLETED")
        assert AuditEventType.WORKFLOW_CLARIFICATION_COMPLETED.value == "WORKFLOW_CLARIFICATION_COMPLETED"


# ── WorkflowExecutionResult clarification_result field ───────────────────────

class TestWorkflowExecutionResultClarificationField:
    def test_field_exists_and_is_none(self):
        r = WorkflowExecutionResult()
        assert hasattr(r, "clarification_result")
        assert r.clarification_result is None

    def test_field_survives_to_dict(self):
        r = WorkflowExecutionResult()
        r.clarification_result = {"status": "READY", "ready_to_continue": True}
        d = r.to_dict()
        assert "clarification_result" in d
        assert d["clarification_result"]["status"] == "READY"

    def test_field_survives_from_dict(self):
        r = WorkflowExecutionResult()
        r.clarification_result = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["session_id"]}
        restored = WorkflowExecutionResult.from_dict(r.to_dict())
        assert restored.clarification_result is not None
        assert restored.clarification_result["status"] == "NEEDS_CLARIFICATION"

    def test_none_clarification_result_in_dict(self):
        r = WorkflowExecutionResult()
        d = r.to_dict()
        assert d["clarification_result"] is None

    def test_roundtrip_with_all_result_fields(self):
        r = WorkflowExecutionResult()
        r.clarification_result = {
            "status": "ESCALATE",
            "result_id": "abc",
            "missing_slots": ["session_id", "phone_number"],
            "clarification_message": "Max attempts exceeded.",
            "ready_to_continue": False,
            "next_question": None,
        }
        restored = WorkflowExecutionResult.from_dict(r.to_dict())
        assert restored.clarification_result["status"] == "ESCALATE"
        assert "session_id" in restored.clarification_result["missing_slots"]
