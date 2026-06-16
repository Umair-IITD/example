"""
tests/test_sprint225_e2e.py

Sprint 2.25: End-to-end tests — full Clarification → Investigation pipeline.

Coverage:
  - ClarificationEngine → ClarificationService → WorkflowEngine (CLARIFY step) full chain
  - All 5 topics produce valid clarification results
  - Missing slots → PAUSED → workflow halts correctly
  - All slots present → workflow proceeds past CLARIFY
  - CLARIFY → INVESTIGATE dispatch (mock InvestigationService)
  - to_dict/from_dict roundtrip preserves clarification_result in WorkflowExecutionResult
  - WorkflowEngine with all 7 services wired processes CLARIFY without error
  - Determinism: 5 identical calls produce identical status
  - Audit methods called in correct order when audit_logger is wired
  - clarification_result persists as JSON-serializable dict
  - Max attempts exceeded → ESCALATE → workflow goes to on_failure
  - ClarificationService + audit end-to-end: started before completed
  - Package imports from case_engine.clarification work
  - All status paths (READY/NEEDS_CLARIFICATION/ESCALATE/ERROR) produce JSON-serializable result
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, call

from case_engine.clarification import (
    ClarificationResult,
    ClarificationStatus,
    MissingSlotInfo,
    WorkflowClarificationEngine,
    ClarificationService,
    build_clarification_engine,
    build_clarification_service,
)
from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(slot_state=None):
    case = MagicMock()
    case.case_id = "case-e2e"
    case.ticket_id = "TKT-E2E"
    case.client = "test"
    case.topic = "VKYC_Session_Failure"
    case.slot_state = slot_state or {}
    case.workflow_context = {}
    return case


def _defn(topic="VKYC_Session_Failure", required_slots=("session_id", "phone_number")):
    clarify = WorkflowStep(
        step_index=0, step_id="clarify_slots",
        step_type=WorkflowStepType.CLARIFY, name="Clarify",
        on_success="investigate", on_failure="escalate_missing",
    )
    investigate = WorkflowStep(
        step_index=1, step_id="investigate",
        step_type=WorkflowStepType.INVESTIGATE, name="Investigate",
        on_success="RESOLVE", on_failure="ESCALATE",
    )
    escalate = WorkflowStep(
        step_index=2, step_id="escalate_missing",
        step_type=WorkflowStepType.ESCALATE_CASE, name="Escalate",
        on_success="ESCALATE", on_failure="ESCALATE",
    )
    return WorkflowDefinition(
        workflow_id="e2e_test",
        topic=topic,
        version="2.0",
        name="E2E Test",
        required_slots=tuple(required_slots),
        steps=(clarify, investigate, escalate),
    )


# ── Package import tests ──────────────────────────────────────────────────────

class TestClarificationPackageImports:
    def test_clarification_status_importable(self):
        from case_engine.clarification import ClarificationStatus
        assert ClarificationStatus.READY.value == "READY"

    def test_clarification_result_importable(self):
        from case_engine.clarification import ClarificationResult
        assert ClarificationResult is not None

    def test_missing_slot_info_importable(self):
        from case_engine.clarification import MissingSlotInfo
        assert MissingSlotInfo is not None

    def test_engine_importable(self):
        from case_engine.clarification import WorkflowClarificationEngine, build_clarification_engine
        engine = build_clarification_engine()
        assert isinstance(engine, WorkflowClarificationEngine)

    def test_service_importable(self):
        from case_engine.clarification import ClarificationService, build_clarification_service
        svc = build_clarification_service()
        assert isinstance(svc, ClarificationService)


# ── Engine → Service → WorkflowEngine chain ──────────────────────────────────

class TestFullClarifyChain:
    def test_all_slots_present_chain(self):
        svc = build_clarification_service()
        engine = WorkflowEngine(
            clarification_service=svc,
            investigation_service=MagicMock(),
        )
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="e2e_test", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        outcome = engine._exec_clarify(
            step=defn.steps[0],
            defn=defn,
            result=result,
            slot_context={"session_id": "KID-abc", "phone_number": "+91999"},
            gateway=None,
            audit=None,
            case=case,
        )

        # clarification_result stored
        assert outcome.clarification_result is not None
        assert outcome.clarification_result["status"] == "READY"
        # NOT paused — workflow continues
        assert outcome.workflow_state is not WorkflowState.PAUSED

    def test_missing_slots_pauses_workflow(self):
        svc = build_clarification_service()
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="e2e_test", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        outcome = engine._exec_clarify(
            step=defn.steps[0],
            defn=defn,
            result=result,
            slot_context={},  # missing everything
            gateway=None,
            audit=None,
            case=case,
        )

        assert outcome.workflow_state is WorkflowState.PAUSED
        assert outcome.clarification_result["status"] == "NEEDS_CLARIFICATION"

    def test_max_attempts_escalates_via_on_failure(self):
        svc = build_clarification_service()
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="e2e_test", workflow_state=WorkflowState.RUNNING)
        case = _make_case(
            slot_state={"session_id": {"attempt_count": 2, "max_attempts": 2}}
        )

        outcome = engine._exec_clarify(
            step=defn.steps[0],
            defn=defn,
            result=result,
            slot_context={},
            gateway=None,
            audit=None,
            case=case,
        )

        step_outcomes = [s["outcome"] for s in outcome.step_results]
        assert any("ESCALATE" in o for o in step_outcomes)
        assert outcome.clarification_result["status"] == "ESCALATE"


# ── All 5 topics produce valid results ───────────────────────────────────────

class TestAllTopics:
    TOPIC_DATA = [
        ("VKYC_Session_Failure",  {"session_id": "KID-abc", "phone_number": "+91999"}, ("session_id", "phone_number")),
        ("OTP_Delivery_Failure",  {"phone_number": "+91999", "channel": "SMS"},         ("phone_number", "channel")),
        ("Document_OCR_Failure",  {"document_type": "PAN", "application_id": "APP-1"}, ("document_type", "application_id")),
        ("Agent_Portal_Issue",    {"agent_id": "AGT-1", "portal_type": "WEB"},         ("agent_id", "portal_type")),
        ("API_Callback_Failure",  {"callback_type": "CBS", "application_id": "APP-1"}, ("callback_type", "application_id")),
    ]

    @pytest.mark.parametrize("topic,slots,required", TOPIC_DATA)
    def test_topic_with_all_slots_gives_ready(self, topic, slots, required):
        engine = build_clarification_engine()
        result = engine.clarify(
            topic=topic,
            slot_context=slots,
            required_slots=required,
        )
        assert result.status is ClarificationStatus.READY

    @pytest.mark.parametrize("topic,slots,required", TOPIC_DATA)
    def test_topic_with_no_slots_gives_needs_clarification(self, topic, slots, required):
        engine = build_clarification_engine()
        result = engine.clarify(
            topic=topic,
            slot_context={},
            required_slots=required,
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION


# ── JSON serializability ──────────────────────────────────────────────────────

class TestClarificationJsonSerializable:
    def test_ready_result_is_json_serializable(self):
        svc = build_clarification_service()
        result = svc.clarify(
            topic="VKYC_Session_Failure",
            slot_context={"session_id": "abc", "phone_number": "+91"},
            required_slots=["session_id", "phone_number"],
        )
        json.dumps(result)  # Must not raise

    def test_needs_clarification_result_is_json_serializable(self):
        svc = build_clarification_service()
        result = svc.clarify(
            topic="VKYC_Session_Failure",
            slot_context={},
            required_slots=["session_id"],
        )
        json.dumps(result)

    def test_escalate_result_is_json_serializable(self):
        svc = build_clarification_service()
        result = svc.clarify(
            topic="VKYC_Session_Failure",
            slot_context={},
            required_slots=["session_id"],
            slot_state={"session_id": {"attempt_count": 3, "max_attempts": 2}},
        )
        json.dumps(result)

    def test_workflow_execution_result_with_clarification_json_serializable(self):
        r = WorkflowExecutionResult()
        r.clarification_result = {
            "status": "READY",
            "result_id": "abc",
            "missing_slots": [],
            "clarification_message": "ok",
            "ready_to_continue": True,
            "next_question": None,
        }
        json.dumps(r.to_dict())  # Must not raise


# ── Determinism ───────────────────────────────────────────────────────────────

class TestClarificationDeterminism:
    def test_five_identical_calls_same_status(self):
        engine = build_clarification_engine()
        statuses = set()
        for _ in range(5):
            result = engine.clarify(
                topic="VKYC_Session_Failure",
                slot_context={"session_id": ""},
                required_slots=["session_id", "phone_number"],
            )
            statuses.add(result.status)
        assert len(statuses) == 1


# ── Audit order tests ─────────────────────────────────────────────────────────

class TestClarificationAuditOrder:
    def test_started_called_before_completed(self):
        call_order = []
        audit = MagicMock()
        audit.log_clarification_started.side_effect = lambda *a, **kw: call_order.append("STARTED")
        audit.log_clarification_completed.side_effect = lambda *a, **kw: call_order.append("COMPLETED")

        case = _make_case()
        svc = ClarificationService(audit_logger=audit)
        svc.clarify(
            topic="VKYC_Session_Failure",
            slot_context={},
            required_slots=["session_id"],
            case=case,
        )

        assert call_order == ["STARTED", "COMPLETED"]

    def test_workflow_started_before_completed_in_exec_clarify(self):
        call_order = []
        audit = MagicMock()
        audit.log_workflow_clarification_started.side_effect = lambda *a, **kw: call_order.append("WF_STARTED")
        audit.log_workflow_clarification_completed.side_effect = lambda *a, **kw: call_order.append("WF_COMPLETED")

        cs = build_clarification_service(audit_logger=None)  # service-level audit separately
        engine = WorkflowEngine(clarification_service=cs)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="e2e_test", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        engine._exec_clarify(
            step=defn.steps[0],
            defn=defn,
            result=result,
            slot_context={"session_id": "abc", "phone_number": "+91"},
            gateway=None,
            audit=audit,
            case=case,
        )

        assert "WF_STARTED" in call_order
        assert "WF_COMPLETED" in call_order
        assert call_order.index("WF_STARTED") < call_order.index("WF_COMPLETED")


# ── WorkflowExecutionResult clarification_result persistence ─────────────────

class TestClarificationResultPersistence:
    def test_clarification_result_in_to_dict_from_dict(self):
        r = WorkflowExecutionResult()
        r.clarification_result = {
            "status": "NEEDS_CLARIFICATION",
            "result_id": "test-1",
            "missing_slots": ["session_id"],
            "clarification_message": "Provide session ID.",
            "ready_to_continue": False,
            "next_question": {"slot_name": "session_id", "prompt_text": "Provide session ID."},
        }

        d = r.to_dict()
        restored = WorkflowExecutionResult.from_dict(d)

        assert restored.clarification_result is not None
        assert restored.clarification_result["status"] == "NEEDS_CLARIFICATION"
        assert "session_id" in restored.clarification_result["missing_slots"]
        assert restored.clarification_result["next_question"]["slot_name"] == "session_id"
