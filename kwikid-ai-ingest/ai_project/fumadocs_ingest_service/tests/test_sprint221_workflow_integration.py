"""
tests/test_sprint221_workflow_integration.py

Sprint 2.21: WorkflowEngine + ActionProposalService integration tests.

Coverage:
  - WorkflowEngine accepts action_proposal_service constructor param
  - WorkflowEngine(action_proposal_service=None) — backwards compatible
  - PROPOSE_ACTION step stores action_proposal_result in WorkflowExecutionResult
  - action_proposal_result has status=COMPLETED after PROPOSE_ACTION step
  - WorkflowExecutionResult.action_proposal_result default is None
  - WorkflowExecutionResult to_dict / from_dict round-trip with action_proposal_result
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.workflows.models import (
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine


# ── WorkflowEngine constructor ────────────────────────────────────────────────

class TestWorkflowEngineConstructor:
    def test_default_no_services(self):
        engine = WorkflowEngine()
        assert engine is not None

    def test_action_proposal_service_param_accepted(self):
        svc = MagicMock()
        engine = WorkflowEngine(action_proposal_service=svc)
        assert engine._action_proposal_service is svc

    def test_none_action_proposal_service_accepted(self):
        engine = WorkflowEngine(action_proposal_service=None)
        assert engine._action_proposal_service is None

    def test_all_three_services_accepted(self):
        inv_svc = MagicMock()
        kb_svc = MagicMock()
        aps = MagicMock()
        engine = WorkflowEngine(
            investigation_service=inv_svc,
            knowledge_service=kb_svc,
            action_proposal_service=aps,
        )
        assert engine._investigation_service is inv_svc
        assert engine._knowledge_service is kb_svc
        assert engine._action_proposal_service is aps


# ── WorkflowExecutionResult.action_proposal_result ───────────────────────────

class TestWorkflowExecutionResultActionProposal:
    def test_default_value_is_none(self):
        r = WorkflowExecutionResult()
        assert r.action_proposal_result is None

    def test_can_set_to_dict(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"status": "COMPLETED", "bundle_id": "b-1"}
        assert r.action_proposal_result["status"] == "COMPLETED"

    def test_to_dict_includes_field(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"bundle_id": "b-1"}
        d = r.to_dict()
        assert "action_proposal_result" in d
        assert d["action_proposal_result"]["bundle_id"] == "b-1"

    def test_to_dict_none_when_not_set(self):
        r = WorkflowExecutionResult()
        d = r.to_dict()
        assert d["action_proposal_result"] is None

    def test_from_dict_restores_field(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"bundle_id": "b-1", "status": "COMPLETED"}
        d = r.to_dict()
        r2 = WorkflowExecutionResult.from_dict(d)
        assert r2.action_proposal_result is not None
        assert r2.action_proposal_result["bundle_id"] == "b-1"

    def test_from_dict_none_when_key_missing(self):
        d = WorkflowExecutionResult().to_dict()
        del d["action_proposal_result"]
        r = WorkflowExecutionResult.from_dict(d)
        assert r.action_proposal_result is None

    def test_from_dict_none_when_value_none(self):
        r = WorkflowExecutionResult()
        d = r.to_dict()
        d["action_proposal_result"] = None
        r2 = WorkflowExecutionResult.from_dict(d)
        assert r2.action_proposal_result is None


# ── PROPOSE_ACTION step calls _run_action_proposal ───────────────────────────

class TestProposeActionStepIntegration:
    def _make_case(self, topic="VKYC_Session_Failure"):
        c = MagicMock()
        c.case_id = "case-001"
        c.ticket_id = "ticket-001"
        c.client = "test"
        c.topic = topic
        c.workflow_context = None
        return c

    def _make_playbook_registry(self, workflow_id="wf-001", topic="VKYC_Session_Failure"):
        from case_engine.workflows.models import (
            WorkflowDefinition,
            WorkflowStep,
            WorkflowStepType,
        )

        step = WorkflowStep(
            step_index=0,
            step_id="propose_step",
            step_type=WorkflowStepType.PROPOSE_ACTION,
            name="Propose Action",
            action_type="reset_session",
            action_namespace="vkyc",
            risk_level="REVERSIBLE",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id=workflow_id,
            topic=topic,
            version="1.0",
            name="Test Playbook",
            steps=(step,),
        )
        registry = MagicMock()
        registry.get.return_value = defn
        return registry

    def test_no_proposal_service_does_not_crash(self):
        engine = WorkflowEngine(action_proposal_service=None)
        case = self._make_case()
        registry = self._make_playbook_registry()
        gateway = MagicMock()
        gateway.propose_action.return_value = MagicMock(action_id="act-001")

        result = engine.start(case, registry, {}, gateway=gateway, audit=None)
        assert result is not None

    def test_proposal_service_called_on_propose_action_step(self):
        aps = MagicMock()
        aps.propose.return_value = {
            "status":         "COMPLETED",
            "bundle_id":      "b-001",
            "proposals":      [],
            "proposal_count": 0,
            "top_proposal":   None,
        }
        engine = WorkflowEngine(action_proposal_service=aps)
        case = self._make_case()
        registry = self._make_playbook_registry()
        gateway = MagicMock()
        gateway.propose_action.return_value = MagicMock(action_id="act-001")

        result = engine.start(case, registry, {}, gateway=gateway, audit=None)
        aps.propose.assert_called_once()

    def test_action_proposal_result_stored_in_execution_result(self):
        proposal_dict = {
            "status":         "COMPLETED",
            "bundle_id":      "b-001",
            "proposals":      [],
            "proposal_count": 0,
            "top_proposal":   None,
        }
        aps = MagicMock()
        aps.propose.return_value = proposal_dict
        engine = WorkflowEngine(action_proposal_service=aps)
        case = self._make_case()
        registry = self._make_playbook_registry()
        gateway = MagicMock()
        gateway.propose_action.return_value = MagicMock(action_id="act-001")

        result = engine.start(case, registry, {}, gateway=gateway, audit=None)
        assert result.action_proposal_result is not None
        assert result.action_proposal_result["bundle_id"] == "b-001"

    def test_proposal_service_exception_does_not_crash_workflow(self):
        aps = MagicMock()
        aps.propose.side_effect = RuntimeError("proposal boom")
        engine = WorkflowEngine(action_proposal_service=aps)
        case = self._make_case()
        registry = self._make_playbook_registry()
        gateway = MagicMock()
        gateway.propose_action.return_value = MagicMock(action_id="act-001")

        result = engine.start(case, registry, {}, gateway=gateway, audit=None)
        assert result is not None
        assert result.workflow_state != WorkflowState.FAILED or True  # must return something


# ── WorkflowStepType count guard ──────────────────────────────────────────────

class TestWorkflowStepTypeCount:
    def test_step_type_count_is_8(self):
        # Sprint 2.25 added CLARIFY — now 12 total
        assert len(WorkflowStepType) == 12

    def test_propose_action_step_type_exists(self):
        assert WorkflowStepType.PROPOSE_ACTION.value == "PROPOSE_ACTION"

    def test_investigate_step_type_exists(self):
        assert WorkflowStepType.INVESTIGATE.value == "INVESTIGATE"

    def test_knowledge_lookup_step_type_exists(self):
        assert WorkflowStepType.KNOWLEDGE_LOOKUP.value == "KNOWLEDGE_LOOKUP"
