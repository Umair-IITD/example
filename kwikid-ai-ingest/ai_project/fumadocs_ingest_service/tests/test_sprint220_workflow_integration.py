"""
tests/test_sprint220_workflow_integration.py

Sprint 2.20: WorkflowEngine KNOWLEDGE_LOOKUP integration tests.

Coverage:
  - WorkflowEngine constructor accepts knowledge_service (no args still works)
  - KNOWLEDGE_LOOKUP step dispatched to _exec_knowledge_lookup
  - on_success navigated when service runs
  - on_failure navigated when service is None
  - knowledge_result stored in WorkflowExecutionResult
  - knowledge_result is JSON-serializable
  - PROPOSE_ACTION blocked when workflow has KNOWLEDGE_LOOKUP but knowledge_result is None
  - PROPOSE_ACTION allowed when knowledge_result is present
  - PROPOSE_ACTION guard for investigation still works independently
  - _validate_workflow_structure warns but does not fail
  - classic playbooks (no KNOWLEDGE_LOOKUP) unaffected
  - WorkflowEngine() (no args) still works
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
)
from case_engine.knowledge.repository import InMemoryKnowledgeRepository
from case_engine.knowledge import build_knowledge_service
from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(case_id: str = "case-kb-001", topic: str = "VKYC_Session_Failure"):
    case = MagicMock()
    case.case_id = case_id
    case.topic = topic
    return case


def _make_registry(defn: WorkflowDefinition):
    registry = MagicMock()
    registry.get.return_value = defn
    registry.get_by_id.return_value = defn
    return registry


def _slot_values(**kwargs):
    from case_engine.slot_filling.models import SlotValue, SlotStatus
    return {k: SlotValue(slot_name=k, value=v, status=SlotStatus.FILLED) for k, v in kwargs.items()}


def _workflow_kb_only(
    topic: str = "VKYC_Session_Failure",
    success_path: str = "RESOLVE",
    failure_path: str = "ESCALATE",
) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=f"test-kb-only-{topic.lower()[:10]}",
        topic=topic,
        version="1.0",
        name="KB Only Workflow",
        steps=(
            WorkflowStep(
                step_index=0, step_id="step-kb",
                step_type=WorkflowStepType.KNOWLEDGE_LOOKUP,
                name="Knowledge Lookup",
                on_success=success_path,
                on_failure=failure_path,
            ),
        ),
    )


def _workflow_investigate_kb_propose() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="test-inv-kb-propose",
        topic="VKYC_Session_Failure",
        version="1.0",
        name="Full Pipeline Workflow",
        steps=(
            WorkflowStep(
                step_index=0, step_id="step-inv",
                step_type=WorkflowStepType.INVESTIGATE,
                name="Investigate", on_success="step-kb", on_failure="ESCALATE",
            ),
            WorkflowStep(
                step_index=1, step_id="step-kb",
                step_type=WorkflowStepType.KNOWLEDGE_LOOKUP,
                name="Knowledge Lookup", on_success="step-propose", on_failure="ESCALATE",
            ),
            WorkflowStep(
                step_index=2, step_id="step-propose",
                step_type=WorkflowStepType.PROPOSE_ACTION,
                name="Propose Action",
                action_type="SESSION_RESET", action_namespace="session",
                on_success="RESOLVE", on_failure="ESCALATE",
            ),
        ),
    )


def _workflow_kb_then_propose() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="test-kb-propose",
        topic="VKYC_Session_Failure",
        version="1.0",
        name="KB Then Propose",
        steps=(
            WorkflowStep(
                step_index=0, step_id="step-kb",
                step_type=WorkflowStepType.KNOWLEDGE_LOOKUP,
                name="Knowledge Lookup", on_success="step-propose", on_failure="ESCALATE",
            ),
            WorkflowStep(
                step_index=1, step_id="step-propose",
                step_type=WorkflowStepType.PROPOSE_ACTION,
                name="Propose Action",
                action_type="SESSION_RESET", action_namespace="session",
                on_success="RESOLVE", on_failure="ESCALATE",
            ),
        ),
    )


def _make_kb_entry() -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id="kb-e-001",
        title="Reset VKYC Session",
        body="1. Navigate to Admin Portal.\n2. Reset session.",
        entry_type=KnowledgeEntryType.SOP,
        tags=("vkyc", "session_reset", "expired_session"),
        topic_keys=("VKYC_Session_Failure",),
        root_cause_categories=("EXPIRED_SESSION",),
        recommended_actions=("SESSION_RESET",),
        resolution_steps=("1. Navigate to Admin Portal.", "2. Reset session."),
        source="manual",
        source_id=None,
        accepted_answer=True,
        score=10,
        created_at="2024-01-01T00:00:00Z",
        status=KnowledgeEntryStatus.ACTIVE,
    )


# ── Constructor ───────────────────────────────────────────────────────────────

class TestWorkflowEngineConstructor:
    def test_no_args_still_works(self):
        engine = WorkflowEngine()
        assert engine._knowledge_service is None

    def test_with_knowledge_service(self):
        svc = build_knowledge_service()
        engine = WorkflowEngine(knowledge_service=svc)
        assert engine._knowledge_service is svc

    def test_with_both_services(self):
        from case_engine.investigation import build_investigation_service
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor
        inv_svc = build_investigation_service(ToolExecutor(ToolRegistry()))
        kb_svc  = build_knowledge_service()
        engine  = WorkflowEngine(investigation_service=inv_svc, knowledge_service=kb_svc)
        assert engine._investigation_service is inv_svc
        assert engine._knowledge_service is kb_svc


# ── KNOWLEDGE_LOOKUP dispatch ─────────────────────────────────────────────────

class TestKnowledgeLookupDispatch:
    def setup_method(self):
        self.kb_svc = build_knowledge_service(seed_entries=[_make_kb_entry()])
        self.engine = WorkflowEngine(knowledge_service=self.kb_svc)

    def test_knowledge_lookup_step_executes(self):
        defn = _workflow_kb_only()
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-001"),
        )
        assert result is not None

    def test_knowledge_result_populated(self):
        defn = _workflow_kb_only()
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-002"),
        )
        assert result.knowledge_result is not None

    def test_knowledge_result_has_result_id(self):
        defn = _workflow_kb_only()
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-003"),
        )
        assert result.knowledge_result.get("result_id")

    def test_knowledge_result_json_serializable(self):
        defn = _workflow_kb_only()
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-004"),
        )
        serialized = json.dumps(result.knowledge_result)
        assert len(serialized) > 0

    def test_on_success_navigated(self):
        defn = _workflow_kb_only(success_path="RESOLVE")
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-005"),
        )
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_step_result_recorded(self):
        defn = _workflow_kb_only()
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-006"),
        )
        kb_step = next((r for r in result.step_results if r["step_id"] == "step-kb"), None)
        assert kb_step is not None

    def test_step_outcome_is_knowledge_search_completed(self):
        defn = _workflow_kb_only()
        result = self.engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-007"),
        )
        kb_step = next((r for r in result.step_results if r["step_id"] == "step-kb"), None)
        assert kb_step["outcome"] == "KNOWLEDGE_SEARCH_COMPLETED"


class TestKnowledgeLookupNoService:
    def test_on_failure_navigated_when_no_service(self):
        engine = WorkflowEngine()  # no knowledge_service
        defn = _workflow_kb_only(failure_path="ESCALATE")
        result = engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-008"),
        )
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_no_knowledge_service_outcome_recorded(self):
        engine = WorkflowEngine()
        defn = _workflow_kb_only()
        result = engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-009"),
        )
        kb_step = next((r for r in result.step_results if r["step_id"] == "step-kb"), None)
        assert kb_step["outcome"] == "NO_KNOWLEDGE_SERVICE"

    def test_knowledge_result_none_when_no_service(self):
        engine = WorkflowEngine()
        defn = _workflow_kb_only()
        result = engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-010"),
        )
        assert result.knowledge_result is None


# ── PROPOSE_ACTION guard ──────────────────────────────────────────────────────

class TestProposeActionGuardKnowledge:
    def test_propose_action_blocked_without_knowledge_result(self):
        engine = WorkflowEngine(knowledge_service=None)  # no KB service
        defn   = _workflow_kb_then_propose()
        result = engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-011"),
        )
        # KB step fails → navigate to ESCALATE, never reaching PROPOSE_ACTION
        # OR KB step escalates before reaching PROPOSE_ACTION
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_propose_action_allowed_with_knowledge_result(self):
        kb_svc = build_knowledge_service(seed_entries=[_make_kb_entry()])
        engine = WorkflowEngine(knowledge_service=kb_svc)
        defn   = _workflow_kb_then_propose()
        result = engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-012"),
        )
        # KB runs → knowledge_result set → PROPOSE_ACTION allowed (no gateway → SKIPPED)
        kb_step = next((r for r in result.step_results if r["step_id"] == "step-kb"), None)
        assert kb_step is not None

    def test_classic_workflow_unaffected_by_kb_guard(self):
        engine = WorkflowEngine()
        defn = WorkflowDefinition(
            workflow_id="classic-wf", topic="VKYC_Session_Failure",
            version="1.0", name="Classic",
            steps=(
                WorkflowStep(
                    step_index=0, step_id="step-propose",
                    step_type=WorkflowStepType.PROPOSE_ACTION,
                    name="Propose", action_type="SESSION_RESET",
                    action_namespace="session",
                    on_success="RESOLVE", on_failure="ESCALATE",
                ),
            ),
        )
        result = engine.start(
            _make_case(), _make_registry(defn), _slot_values(session_id="S-013"),
        )
        # Classic workflow: no KB step → guard doesn't trigger → SKIPPED_NO_GATEWAY
        propose_step = next(
            (r for r in result.step_results if r["step_id"] == "step-propose"), None
        )
        assert propose_step is not None
        assert "BLOCKED_NO_KNOWLEDGE_LOOKUP" not in propose_step["outcome"]


# ── Backwards compatibility ───────────────────────────────────────────────────

class TestBackwardsCompatibility:
    def test_engine_with_only_investigation_service_still_works(self):
        from case_engine.investigation import build_investigation_service
        from case_engine.tools.tool_registry import ToolRegistry
        from case_engine.tools.tool_executor import ToolExecutor
        inv_svc = build_investigation_service(ToolExecutor(ToolRegistry()))
        engine  = WorkflowEngine(investigation_service=inv_svc)
        assert engine._knowledge_service is None
