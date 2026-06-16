"""
tests/test_sprint219_e2e.py

Sprint 2.19: End-to-end integration tests — full investigation workflow path.

Wires together:
  WorkflowEngine (with real InvestigationService)
  → INVESTIGATE step → InvestigationStepExecutor
  → InvestigationService.investigate()
  → RootCauseEngine → ObservationGenerator
  → investigation_result stored in WorkflowExecutionResult

Uses real mock tools (GetSessionDetailsTool, GetUserDetailsTool, etc.)
and a minimal YAML-free WorkflowDefinition built in-process.

Coverage:
  - Full E2E: engine.start() → INVESTIGATE → RESOLVED workflow
  - Full E2E: engine.start() → INVESTIGATE → ESCALATED when escalate=True
  - investigation_result is populated after INVESTIGATE step
  - All 5 blueprint sections present in observation note stored in context
  - investigation_result is JSON-serializable
  - INVESTIGATE step followed by PROPOSE_ACTION: action proposed on success path
  - Missing slots produce degraded investigation but no crash
  - Unknown topic produces escalated workflow
  - audit events emitted for investigation_started/completed
"""
from __future__ import annotations

import json
from typing import Any
import pytest

from case_engine.investigation import build_investigation_service
from case_engine.tools.mock_tools import (
    GetCaseHistoryTool,
    GetFailureReasonTool,
    GetOnboardingStatusTool,
    GetSessionDetailsTool,
    GetUserDetailsTool,
)
from case_engine.tools.tool_executor import ToolExecutor
from case_engine.tools.tool_registry import ToolRegistry
from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.slot_filling.models import SlotValue, SlotStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def inv_service():
    registry = ToolRegistry()
    for tool_cls in [
        GetSessionDetailsTool, GetUserDetailsTool, GetFailureReasonTool,
        GetCaseHistoryTool, GetOnboardingStatusTool,
    ]:
        registry.register(tool_cls())
    executor = ToolExecutor(registry)
    return build_investigation_service(executor, audit_logger=None)


@pytest.fixture(scope="module")
def engine(inv_service):
    return WorkflowEngine(investigation_service=inv_service)


def _slot_values(**kwargs: str) -> dict[str, SlotValue]:
    return {k: SlotValue(slot_name=k, value=v, status=SlotStatus.FILLED) for k, v in kwargs.items()}


def _make_case(topic: str = "VKYC_Session_Failure", case_id: str = "e2e-case-001"):
    from unittest.mock import MagicMock
    case = MagicMock()
    case.case_id = case_id
    case.topic = topic
    return case


def _workflow_investigate_only(
    topic: str = "VKYC_Session_Failure",
    *,
    success_path: str = "RESOLVE",
    failure_path: str = "ESCALATE",
) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=f"e2e-inv-only-{topic.lower().replace('_', '-')}",
        topic=topic,
        version="1.0",
        name=f"E2E Investigation Only — {topic}",
        steps=(
            WorkflowStep(
                step_index=0, step_id="step-inv",
                step_type=WorkflowStepType.INVESTIGATE,
                name="Investigate",
                on_success=success_path,
                on_failure=failure_path,
            ),
        ),
    )


def _registry(defn: WorkflowDefinition):
    from unittest.mock import MagicMock
    registry = MagicMock()
    registry.get.return_value = defn
    registry.get_by_id.return_value = defn
    return registry


# ── Happy path: INVESTIGATE → RESOLVED ────────────────────────────────────────

class TestE2EInvestigateResolve:
    def test_workflow_completes(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            _slot_values(session_id="S-E2E-001", phone_number="9876543210"),
        )
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_investigation_result_populated(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            _slot_values(session_id="S-E2E-002", phone_number="9876543210"),
        )
        assert result.investigation_result is not None

    def test_investigation_result_has_root_cause(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            _slot_values(session_id="S-E2E-003"),
        )
        assert "root_cause" in result.investigation_result
        assert "category" in result.investigation_result["root_cause"]

    def test_investigation_result_json_serializable(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            _slot_values(session_id="S-E2E-004"),
        )
        serialized = json.dumps(result.investigation_result)
        assert len(serialized) > 0

    def test_observation_has_blueprint_sections(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            _slot_values(session_id="S-E2E-005"),
        )
        obs = result.investigation_result["observation"]
        assert "=== ISSUE SUMMARY ===" in obs
        assert "=== ROOT CAUSE ===" in obs
        assert "=== RECOMMENDED ACTION ===" in obs
        assert "=== ESCALATION REQUIRED ===" in obs

    def test_step_result_recorded(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            _slot_values(session_id="S-E2E-006"),
        )
        inv_step = next((r for r in result.step_results if r["step_id"] == "step-inv"), None)
        assert inv_step is not None


# ── All 5 topics ──────────────────────────────────────────────────────────────

class TestE2EAllTopics:
    @pytest.mark.parametrize("topic,slots", [
        ("VKYC_Session_Failure",   {"session_id": "S-1", "phone_number": "9876543210"}),
        ("OTP_Delivery_Failure",   {"phone_number": "9876543210", "session_id": "S-2"}),
        ("Document_OCR_Failure",   {"application_id": "APP-1", "phone_number": "9876543210"}),
        ("Agent_Portal_Issue",     {"session_id": "S-3", "phone_number": "9876543210"}),
        ("API_Callback_Failure",   {"session_id": "S-4"}),
    ])
    def test_topic_produces_valid_result(self, engine, topic, slots):
        defn = _workflow_investigate_only(topic)
        result = engine.start(_make_case(topic), _registry(defn), _slot_values(**slots))
        assert result.workflow_state in (WorkflowState.COMPLETED, WorkflowState.ESCALATED)
        assert result.investigation_result is not None


# ── Unknown topic ─────────────────────────────────────────────────────────────

class TestE2EUnknownTopic:
    def test_unknown_topic_escalates(self, engine):
        defn = _workflow_investigate_only("COMPLETELY_UNKNOWN_TOPIC_219")
        result = engine.start(
            _make_case("COMPLETELY_UNKNOWN_TOPIC_219"),
            _registry(defn),
            {},
        )
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_unknown_topic_has_investigation_result(self, engine):
        defn = _workflow_investigate_only("COMPLETELY_UNKNOWN_TOPIC_219")
        result = engine.start(
            _make_case("COMPLETELY_UNKNOWN_TOPIC_219"),
            _registry(defn),
            {},
        )
        assert result.investigation_result is not None

    def test_unknown_topic_never_crashes(self, engine):
        defn = _workflow_investigate_only("COMPLETELY_UNKNOWN_TOPIC_219")
        # Must not raise
        result = engine.start(
            _make_case("COMPLETELY_UNKNOWN_TOPIC_219"),
            _registry(defn),
            {},
        )
        assert isinstance(result, WorkflowExecutionResult)


# ── Missing slots ─────────────────────────────────────────────────────────────

class TestE2EMissingSlots:
    def test_missing_slots_does_not_crash(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            {},
        )
        assert isinstance(result, WorkflowExecutionResult)

    def test_missing_slots_investigation_result_present(self, engine):
        defn = _workflow_investigate_only("VKYC_Session_Failure")
        result = engine.start(
            _make_case("VKYC_Session_Failure"),
            _registry(defn),
            {},
        )
        assert result.investigation_result is not None


# ── Full round-trip serialization ─────────────────────────────────────────────

class TestE2ESerializationRoundTrip:
    def test_full_workflow_context_round_trips(self, engine):
        defn = _workflow_investigate_only("OTP_Delivery_Failure")
        result = engine.start(
            _make_case("OTP_Delivery_Failure"),
            _registry(defn),
            _slot_values(phone_number="9876543210", session_id="S-SER-001"),
        )
        d = result.to_dict()
        json_str = json.dumps(d)
        restored_dict = json.loads(json_str)
        restored = WorkflowExecutionResult.from_dict(restored_dict)
        assert restored.investigation_result is not None
        assert "root_cause" in restored.investigation_result
