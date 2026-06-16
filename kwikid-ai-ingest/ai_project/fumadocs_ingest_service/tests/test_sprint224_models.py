"""
tests/test_sprint224_models.py

Sprint 2.24: Domain model tests for Investigation Reasoning Engine.

Coverage:
  - ReasoningOutcome enum values
  - ReasoningRecommendation: construction, to_dict, from_dict, frozen
  - ReasoningTrace: construction, to_dict, from_dict, frozen
  - ReasoningBundle: construction, to_dict, from_dict, frozen
  - ReasoningResult: construction, convenience properties, to_dict, from_dict, frozen
  - WorkflowStepType.REASON exists (11th step type)
  - AuditEventType: 4 new Sprint 2.24 values
  - WorkflowExecutionResult: reasoning_result field, to_dict/from_dict
"""
from __future__ import annotations

import pytest

from case_engine.reasoning.models import (
    ReasoningBundle,
    ReasoningOutcome,
    ReasoningRecommendation,
    ReasoningResult,
    ReasoningTrace,
)
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowStepType
from case_engine.models import AuditEventType


# ── ReasoningOutcome ──────────────────────────────────────────────────────────

class TestReasoningOutcome:
    def test_recommend_action_value(self):
        assert ReasoningOutcome.RECOMMEND_ACTION.value == "RECOMMEND_ACTION"

    def test_escalate_value(self):
        assert ReasoningOutcome.ESCALATE.value == "ESCALATE"

    def test_uncertain_value(self):
        assert ReasoningOutcome.UNCERTAIN.value == "UNCERTAIN"

    def test_wait_for_more_evidence_value(self):
        assert ReasoningOutcome.WAIT_FOR_MORE_EVIDENCE.value == "WAIT_FOR_MORE_EVIDENCE"

    def test_is_str_enum(self):
        assert isinstance(ReasoningOutcome.ESCALATE, str)

    def test_four_variants(self):
        assert len(list(ReasoningOutcome)) == 4


# ── ReasoningRecommendation ───────────────────────────────────────────────────

class TestReasoningRecommendation:
    def _make(self, **kw) -> ReasoningRecommendation:
        defaults = dict(
            action_type="RESET_SESSION",
            confidence=0.90,
            rationale="Network failure requires session reset",
            sop_ids_used=(),
            knowledge_ids_used=(),
        )
        defaults.update(kw)
        return ReasoningRecommendation(**defaults)

    def test_basic_construction(self):
        rec = self._make()
        assert rec.action_type == "RESET_SESSION"
        assert rec.confidence == 0.90

    def test_is_frozen(self):
        rec = self._make()
        with pytest.raises((AttributeError, TypeError)):
            rec.action_type = "CHANGED"  # type: ignore

    def test_to_dict_action_type(self):
        d = self._make().to_dict()
        assert d["action_type"] == "RESET_SESSION"

    def test_to_dict_confidence(self):
        d = self._make(confidence=0.75).to_dict()
        assert d["confidence"] == 0.75

    def test_to_dict_rationale(self):
        d = self._make(rationale="test rationale").to_dict()
        assert d["rationale"] == "test rationale"

    def test_to_dict_sop_ids_is_list(self):
        d = self._make(sop_ids_used=("s1", "s2")).to_dict()
        assert d["sop_ids_used"] == ["s1", "s2"]

    def test_to_dict_knowledge_ids_is_list(self):
        d = self._make(knowledge_ids_used=("k1",)).to_dict()
        assert d["knowledge_ids_used"] == ["k1"]

    def test_from_dict_roundtrip(self):
        original = self._make(sop_ids_used=("s1",), knowledge_ids_used=("k1",))
        restored = ReasoningRecommendation.from_dict(original.to_dict())
        assert restored.action_type == original.action_type
        assert restored.confidence == original.confidence
        assert restored.sop_ids_used == original.sop_ids_used
        assert restored.knowledge_ids_used == original.knowledge_ids_used

    def test_from_dict_empty_defaults(self):
        rec = ReasoningRecommendation.from_dict({})
        assert rec.action_type == ""
        assert rec.confidence == 0.0
        assert rec.sop_ids_used == ()
        assert rec.knowledge_ids_used == ()


# ── ReasoningTrace ────────────────────────────────────────────────────────────

class TestReasoningTrace:
    def _make(self, **kw) -> ReasoningTrace:
        defaults = dict(
            rule_applied="rule5_category_rule_table",
            evidence_ids_used=("ev-1",),
            sop_ids_used=("sop-1",),
            knowledge_ids_used=("kb-1",),
            decision_path=("root_cause.category=NETWORK_FAILURE", "rule5 matched"),
            final_recommendation="RESET_SESSION",
            created_at="2026-06-15T00:00:00+00:00",
        )
        defaults.update(kw)
        return ReasoningTrace(**defaults)

    def test_basic_construction(self):
        t = self._make()
        assert t.rule_applied == "rule5_category_rule_table"

    def test_is_frozen(self):
        t = self._make()
        with pytest.raises((AttributeError, TypeError)):
            t.rule_applied = "changed"  # type: ignore

    def test_to_dict_rule_applied(self):
        d = self._make().to_dict()
        assert d["rule_applied"] == "rule5_category_rule_table"

    def test_to_dict_evidence_ids_is_list(self):
        d = self._make().to_dict()
        assert isinstance(d["evidence_ids_used"], list)

    def test_to_dict_decision_path_is_list(self):
        d = self._make().to_dict()
        assert isinstance(d["decision_path"], list)

    def test_to_dict_final_recommendation(self):
        d = self._make().to_dict()
        assert d["final_recommendation"] == "RESET_SESSION"

    def test_from_dict_roundtrip(self):
        original = self._make()
        restored = ReasoningTrace.from_dict(original.to_dict())
        assert restored.rule_applied == original.rule_applied
        assert restored.decision_path == original.decision_path
        assert restored.final_recommendation == original.final_recommendation

    def test_from_dict_empty_defaults(self):
        t = ReasoningTrace.from_dict({})
        assert t.rule_applied == ""
        assert t.evidence_ids_used == ()
        assert t.decision_path == ()


# ── ReasoningBundle ───────────────────────────────────────────────────────────

class TestReasoningBundle:
    def _make_rec(self) -> ReasoningRecommendation:
        return ReasoningRecommendation(
            action_type="RESET_SESSION", confidence=0.90,
            rationale="test", sop_ids_used=(), knowledge_ids_used=(),
        )

    def _make_trace(self) -> ReasoningTrace:
        return ReasoningTrace(
            rule_applied="rule5", evidence_ids_used=(),
            sop_ids_used=(), knowledge_ids_used=(),
            decision_path=(), final_recommendation="RESET_SESSION",
            created_at="2026-06-15T00:00:00+00:00",
        )

    def _make(self, **kw) -> ReasoningBundle:
        defaults = dict(
            bundle_id="bundle-123",
            topic="VKYC_Session_Failure",
            root_cause_category="NETWORK_FAILURE",
            root_cause_confidence=0.85,
            outcome=ReasoningOutcome.RECOMMEND_ACTION,
            recommendation=self._make_rec(),
            trace=self._make_trace(),
            should_escalate=False,
            escalate_reason=None,
            created_at="2026-06-15T00:00:00+00:00",
        )
        defaults.update(kw)
        return ReasoningBundle(**defaults)

    def test_basic_construction(self):
        b = self._make()
        assert b.bundle_id == "bundle-123"
        assert b.root_cause_category == "NETWORK_FAILURE"

    def test_is_frozen(self):
        b = self._make()
        with pytest.raises((AttributeError, TypeError)):
            b.bundle_id = "changed"  # type: ignore

    def test_to_dict_outcome_is_string(self):
        d = self._make().to_dict()
        assert d["outcome"] == "RECOMMEND_ACTION"

    def test_to_dict_recommendation_nested(self):
        d = self._make().to_dict()
        assert "recommendation" in d
        assert d["recommendation"]["action_type"] == "RESET_SESSION"

    def test_to_dict_trace_nested(self):
        d = self._make().to_dict()
        assert "trace" in d
        assert d["trace"]["rule_applied"] == "rule5"

    def test_from_dict_roundtrip(self):
        original = self._make()
        restored = ReasoningBundle.from_dict(original.to_dict())
        assert restored.bundle_id == original.bundle_id
        assert restored.outcome == original.outcome
        assert restored.should_escalate == original.should_escalate

    def test_escalate_reason_stored(self):
        b = self._make(should_escalate=True, escalate_reason="low confidence")
        assert b.escalate_reason == "low confidence"
        assert b.to_dict()["escalate_reason"] == "low confidence"

    def test_escalate_reason_none(self):
        b = self._make(should_escalate=False, escalate_reason=None)
        assert b.to_dict()["escalate_reason"] is None


# ── ReasoningResult ───────────────────────────────────────────────────────────

class TestReasoningResult:
    def _make_bundle(self, **kw) -> ReasoningBundle:
        rec = ReasoningRecommendation(
            action_type="RESET_SESSION", confidence=0.90,
            rationale="test", sop_ids_used=(), knowledge_ids_used=(),
        )
        trace = ReasoningTrace(
            rule_applied="rule5", evidence_ids_used=(),
            sop_ids_used=(), knowledge_ids_used=(),
            decision_path=(), final_recommendation="RESET_SESSION",
            created_at="2026-06-15T00:00:00+00:00",
        )
        defaults = dict(
            bundle_id="b-1",
            topic="test",
            root_cause_category="NETWORK_FAILURE",
            root_cause_confidence=0.85,
            outcome=ReasoningOutcome.RECOMMEND_ACTION,
            recommendation=rec,
            trace=trace,
            should_escalate=False,
            escalate_reason=None,
            created_at="2026-06-15T00:00:00+00:00",
        )
        defaults.update(kw)
        return ReasoningBundle(**defaults)

    def _make(self, **kw) -> ReasoningResult:
        defaults = dict(
            result_id="res-1",
            bundle=self._make_bundle(),
            created_at="2026-06-15T00:00:00+00:00",
        )
        defaults.update(kw)
        return ReasoningResult(**defaults)

    def test_basic_construction(self):
        r = self._make()
        assert r.result_id == "res-1"

    def test_is_frozen(self):
        r = self._make()
        with pytest.raises((AttributeError, TypeError)):
            r.result_id = "changed"  # type: ignore

    def test_recommended_action_property(self):
        r = self._make()
        assert r.recommended_action == "RESET_SESSION"

    def test_root_cause_category_property(self):
        r = self._make()
        assert r.root_cause_category == "NETWORK_FAILURE"

    def test_confidence_property(self):
        r = self._make()
        assert r.confidence == 0.90

    def test_should_escalate_property_false(self):
        r = self._make()
        assert r.should_escalate is False

    def test_should_escalate_property_true(self):
        bundle = self._make_bundle(
            should_escalate=True,
            escalate_reason="test",
            outcome=ReasoningOutcome.ESCALATE,
        )
        r = self._make(bundle=bundle)
        assert r.should_escalate is True

    def test_outcome_property(self):
        r = self._make()
        assert r.outcome == ReasoningOutcome.RECOMMEND_ACTION

    def test_to_dict_has_result_id(self):
        d = self._make().to_dict()
        assert "result_id" in d

    def test_to_dict_flattened_keys(self):
        d = self._make().to_dict()
        assert "recommended_action" in d
        assert "root_cause_category" in d
        assert "confidence" in d
        assert "should_escalate" in d
        assert "outcome" in d

    def test_to_dict_outcome_is_string(self):
        d = self._make().to_dict()
        assert d["outcome"] == "RECOMMEND_ACTION"

    def test_from_dict_roundtrip(self):
        original = self._make()
        restored = ReasoningResult.from_dict(original.to_dict())
        assert restored.result_id == original.result_id
        assert restored.recommended_action == original.recommended_action
        assert restored.should_escalate == original.should_escalate

    def test_from_dict_empty_defaults(self):
        r = ReasoningResult.from_dict({})
        assert r.result_id != ""
        assert r.outcome == ReasoningOutcome.UNCERTAIN


# ── WorkflowStepType.REASON ───────────────────────────────────────────────────

class TestWorkflowStepTypeReason:
    def test_reason_exists(self):
        assert hasattr(WorkflowStepType, "REASON")

    def test_reason_value(self):
        assert WorkflowStepType.REASON.value == "REASON"

    def test_reason_is_11th_type(self):
        types = list(WorkflowStepType)
        assert len(types) >= 11

    def test_reason_in_enum_values(self):
        values = [t.value for t in WorkflowStepType]
        assert "REASON" in values


# ── AuditEventType Sprint 2.24 ────────────────────────────────────────────────

class TestAuditEventTypeSprint224:
    def test_reasoning_started_exists(self):
        assert hasattr(AuditEventType, "REASONING_STARTED")

    def test_reasoning_completed_exists(self):
        assert hasattr(AuditEventType, "REASONING_COMPLETED")

    def test_workflow_reasoning_started_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_REASONING_STARTED")

    def test_workflow_reasoning_completed_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_REASONING_COMPLETED")

    def test_reasoning_started_value(self):
        assert AuditEventType.REASONING_STARTED.value == "REASONING_STARTED"

    def test_reasoning_completed_value(self):
        assert AuditEventType.REASONING_COMPLETED.value == "REASONING_COMPLETED"

    def test_workflow_reasoning_started_value(self):
        assert AuditEventType.WORKFLOW_REASONING_STARTED.value == "WORKFLOW_REASONING_STARTED"

    def test_workflow_reasoning_completed_value(self):
        assert AuditEventType.WORKFLOW_REASONING_COMPLETED.value == "WORKFLOW_REASONING_COMPLETED"


# ── WorkflowExecutionResult.reasoning_result ──────────────────────────────────

class TestWorkflowExecutionResultReasoningResult:
    def test_reasoning_result_default_none(self):
        r = WorkflowExecutionResult()
        assert r.reasoning_result is None

    def test_reasoning_result_can_be_set(self):
        r = WorkflowExecutionResult()
        r.reasoning_result = {"status": "COMPLETED", "recommended_action": "RESET_SESSION"}
        assert r.reasoning_result["recommended_action"] == "RESET_SESSION"

    def test_to_dict_has_reasoning_result(self):
        r = WorkflowExecutionResult()
        d = r.to_dict()
        assert "reasoning_result" in d

    def test_to_dict_reasoning_result_none_by_default(self):
        r = WorkflowExecutionResult()
        assert r.to_dict()["reasoning_result"] is None

    def test_to_dict_reasoning_result_preserved(self):
        r = WorkflowExecutionResult()
        r.reasoning_result = {"status": "COMPLETED"}
        assert r.to_dict()["reasoning_result"]["status"] == "COMPLETED"

    def test_from_dict_reasoning_result_preserved(self):
        r = WorkflowExecutionResult()
        r.reasoning_result = {"status": "COMPLETED", "recommended_action": "RESEND_OTP"}
        restored = WorkflowExecutionResult.from_dict(r.to_dict())
        assert restored.reasoning_result["recommended_action"] == "RESEND_OTP"

    def test_from_dict_reasoning_result_missing_defaults_none(self):
        r = WorkflowExecutionResult.from_dict({})
        assert r.reasoning_result is None
