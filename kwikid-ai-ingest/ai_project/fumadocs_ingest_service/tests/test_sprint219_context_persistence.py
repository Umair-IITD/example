"""
tests/test_sprint219_context_persistence.py

Sprint 2.19 Part 8: Investigation result persisted to workflow context.

Coverage:
  - investigation_result in WorkflowExecutionResult.to_dict() is fully JSON-serializable
  - root_cause metadata accessible via investigation_result dict
  - observation accessible via investigation_result dict
  - investigation_result round-trips through from_dict() without data loss
  - investigation_result survives JSONB-style encode/decode (json.dumps + json.loads)
  - confidence value preserved
  - escalation flag preserved
  - recommended_action preserved
  - category preserved
  - to_dict() result compatible with WorkflowExecutionResult.from_dict()
"""
from __future__ import annotations

import json
import pytest

from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState
from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationPlan,
    InvestigationResult,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)


def _make_full_inv_result(
    *,
    escalate: bool = False,
    category: RootCauseCategory = RootCauseCategory.EXPIRED_SESSION,
    confidence: float = 0.87,
) -> InvestigationResult:
    now = "2026-06-11T00:00:00+00:00"
    plan = InvestigationPlan(
        plan_id="p-ctx", case_id="c-ctx", topic="VKYC_Session_Failure",
        workflow_id="wf-ctx", steps=(), created_at=now,
    )
    bundle = EvidenceBundle(
        bundle_id="b-ctx", case_id="c-ctx", topic="VKYC_Session_Failure",
        plan_id="p-ctx", items=[], collected_at=now,
    )
    rca = RootCauseAnalysis(
        analysis_id="a-ctx", case_id="c-ctx", topic="VKYC_Session_Failure",
        category=category, confidence=confidence, explanation="Context test.",
        evidence_ids=["ev-1", "ev-2"],
        recommended_action=RecommendedAction.SESSION_RESET,
        escalate=escalate, analysed_at=now,
    )
    return InvestigationResult(
        result_id="r-ctx-001", case_id="c-ctx", plan=plan, bundle=bundle,
        root_cause=rca,
        observation="=== ISSUE SUMMARY ===\nVKYC failed.\n=== ROOT CAUSE ===\nExpired session.",
        completed_at=now,
    )


class TestInvestigationResultPersistence:
    def _wer_with_inv(self, **kwargs) -> WorkflowExecutionResult:
        inv = _make_full_inv_result(**kwargs)
        wer = WorkflowExecutionResult(
            workflow_id="wf-ctx-test",
            workflow_state=WorkflowState.RUNNING,
        )
        wer.investigation_result = inv.to_dict()
        return wer

    def test_investigation_result_json_serializable(self):
        wer = self._wer_with_inv()
        serialized = json.dumps(wer.to_dict())
        assert len(serialized) > 0

    def test_investigation_result_round_trip(self):
        wer = self._wer_with_inv()
        original_dict = wer.investigation_result
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.investigation_result == original_dict

    def test_jsonb_encode_decode_round_trip(self):
        wer = self._wer_with_inv()
        encoded = json.dumps(wer.to_dict())
        decoded = json.loads(encoded)
        restored = WorkflowExecutionResult.from_dict(decoded)
        assert restored.investigation_result is not None

    def test_category_preserved(self):
        wer = self._wer_with_inv(category=RootCauseCategory.TIMEOUT)
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.investigation_result["root_cause"]["category"] == "TIMEOUT"

    def test_confidence_preserved(self):
        wer = self._wer_with_inv(confidence=0.72)
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert abs(restored.investigation_result["root_cause"]["confidence"] - 0.72) < 1e-6

    def test_escalate_flag_preserved(self):
        wer = self._wer_with_inv(escalate=True)
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.investigation_result["root_cause"]["escalate"] is True

    def test_observation_preserved(self):
        wer = self._wer_with_inv()
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        obs = restored.investigation_result["observation"]
        assert "ISSUE SUMMARY" in obs

    def test_recommended_action_preserved(self):
        wer = self._wer_with_inv()
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.investigation_result["root_cause"]["recommended_action"] == "SESSION_RESET"

    def test_result_id_preserved(self):
        wer = self._wer_with_inv()
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.investigation_result["result_id"] == "r-ctx-001"

    def test_plan_id_accessible(self):
        wer = self._wer_with_inv()
        assert wer.investigation_result["plan"]["plan_id"] == "p-ctx"

    def test_bundle_id_accessible(self):
        wer = self._wer_with_inv()
        assert wer.investigation_result["evidence"]["bundle_id"] == "b-ctx"
