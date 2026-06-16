"""
tests/test_sprint224_e2e.py

Sprint 2.24: End-to-end tests — Investigation → Knowledge → Reasoning → Proposal chain.

Coverage:
  - InvestigationReasoningEngine produces ReasoningResult from raw investigation data
  - ReasoningService wraps engine correctly (COMPLETED status)
  - to_dict() / from_dict() roundtrip for full ReasoningResult
  - Full chain: INVESTIGATE result → REASON → ReasoningResult used in PROPOSE_ACTION
  - All 14 root cause categories produce valid, non-raising results end-to-end
  - SOP-based override flows end-to-end
  - Exception in any stage produces safe result
  - Determinism: 10 identical runs produce identical recommended_action
  - WorkflowExecutionResult.reasoning_result persists across to_dict/from_dict
  - Build functions import cleanly from top-level package
"""
from __future__ import annotations

import pytest

from case_engine.reasoning import (
    InvestigationReasoningEngine,
    ReasoningOutcome,
    ReasoningResult,
    ReasoningService,
    build_reasoning_engine,
    build_reasoning_service,
)
from case_engine.reasoning.models import ReasoningBundle, ReasoningRecommendation, ReasoningTrace
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowStepType


# ── Helpers ───────────────────────────────────────────────────────────────────

ALL_CATEGORIES = [
    "NETWORK_FAILURE",
    "TIMEOUT",
    "EXPIRED_SESSION",
    "REPEATED_FAILURE",
    "QUOTA_EXCEEDED",
    "LIVENESS_FAILURE",
    "DOCUMENT_FAILURE",
    "VALIDATION_FAILURE",
    "KYC_REJECTED",
    "SMS_DELIVERY_FAILURE",
    "CALLBACK_FAILURE",
    "ONBOARDING_BLOCKED",
    "PORTAL_UNAVAILABLE",
    "UNKNOWN",
]


def _inv(category: str, confidence: float = 0.85, escalate: bool = False) -> dict:
    return {
        "topic":        "VKYC_Session_Failure",
        "evidence_ids": ["ev-1"],
        "root_cause":   {
            "category":   category,
            "confidence": confidence,
            "escalate":   escalate,
        },
    }


def _kb(sop_match_found: bool = False, sop_title: str = "") -> dict:
    if not sop_match_found:
        return {"sop_match_found": False}
    return {
        "sop_match_found": True,
        "sop_match": {
            "entry": {"entry_id": "sop-1", "title": sop_title},
            "relevance_score": 0.92,
        },
    }


# ── Package imports ───────────────────────────────────────────────────────────

class TestPackageImports:
    def test_investigation_reasoning_engine_importable(self):
        from case_engine.reasoning import InvestigationReasoningEngine
        assert InvestigationReasoningEngine

    def test_reasoning_service_importable(self):
        from case_engine.reasoning import ReasoningService
        assert ReasoningService

    def test_reasoning_result_importable(self):
        from case_engine.reasoning import ReasoningResult
        assert ReasoningResult

    def test_build_reasoning_engine_importable(self):
        from case_engine.reasoning import build_reasoning_engine
        assert callable(build_reasoning_engine)

    def test_build_reasoning_service_importable(self):
        from case_engine.reasoning import build_reasoning_service
        assert callable(build_reasoning_service)

    def test_reasoning_outcome_importable(self):
        from case_engine.reasoning import ReasoningOutcome
        assert ReasoningOutcome


# ── Full engine → service chain ───────────────────────────────────────────────

class TestEngineToServiceChain:
    def test_engine_produces_result_for_network_failure(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("NETWORK_FAILURE"))
        assert isinstance(r, ReasoningResult)
        assert r.recommended_action == "RESET_SESSION"

    def test_service_wraps_engine_result(self):
        svc = build_reasoning_service()
        r   = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        assert r["status"] == "COMPLETED"
        assert r["recommended_action"] == "RESET_SESSION"

    def test_service_result_has_bundle(self):
        svc = build_reasoning_service()
        r   = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        assert "bundle" in r
        assert isinstance(r["bundle"], dict)

    def test_service_result_roundtrip(self):
        svc     = build_reasoning_service()
        r_dict  = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        bundle  = r_dict.get("bundle", {})
        # Reconstruct from nested dict
        restored = ReasoningResult.from_dict(r_dict)
        assert restored.recommended_action == "RESET_SESSION"


# ── All categories end-to-end ─────────────────────────────────────────────────

class TestAllCategoriesE2E:
    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_category_does_not_raise(self, category):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv(category, confidence=0.85))
        assert isinstance(r, ReasoningResult)

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_category_service_does_not_raise(self, category):
        svc = build_reasoning_service()
        r   = svc.reason(investigation_result=_inv(category, confidence=0.85))
        assert isinstance(r, dict)
        assert "status" in r

    def test_all_categories_produce_non_empty_recommended_action(self):
        eng = build_reasoning_engine()
        for category in ALL_CATEGORIES:
            r = eng.reason(_inv(category, confidence=0.85))
            assert r.recommended_action != "", f"Category {category} produced empty action"


# ── SOP override E2E ──────────────────────────────────────────────────────────

class TestSopOverrideE2E:
    def test_session_reset_sop_overrides_network_failure(self):
        eng = build_reasoning_engine()
        r   = eng.reason(
            _inv("NETWORK_FAILURE", confidence=0.85),
            _kb(sop_match_found=True, sop_title="session reset procedure"),
        )
        assert r.recommended_action == "RESET_SESSION"

    def test_resend_otp_sop_overrides_sms_failure(self):
        eng = build_reasoning_engine()
        r   = eng.reason(
            _inv("SMS_DELIVERY_FAILURE", confidence=0.85),
            _kb(sop_match_found=True, sop_title="resend otp for failed delivery"),
        )
        assert r.recommended_action == "RESEND_OTP"

    def test_service_propagates_sop_override(self):
        svc = build_reasoning_service()
        r   = svc.reason(
            investigation_result=_inv("NETWORK_FAILURE", confidence=0.85),
            knowledge_result=_kb(sop_match_found=True, sop_title="session reset procedure"),
        )
        assert r["recommended_action"] == "RESET_SESSION"


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminismE2E:
    def test_10_identical_runs_produce_same_action(self):
        eng    = build_reasoning_engine()
        inv    = _inv("NETWORK_FAILURE", confidence=0.85)
        kb     = _kb(sop_match_found=False)
        results = [eng.reason(inv, kb).recommended_action for _ in range(10)]
        assert len(set(results)) == 1

    def test_deterministic_outcome_across_categories(self):
        eng = build_reasoning_engine()
        for category in ALL_CATEGORIES:
            r1 = eng.reason(_inv(category, confidence=0.85))
            r2 = eng.reason(_inv(category, confidence=0.85))
            assert r1.recommended_action == r2.recommended_action, f"Non-deterministic for {category}"


# ── to_dict / from_dict roundtrip ─────────────────────────────────────────────

class TestReasoningResultRoundtrip:
    def test_engine_result_roundtrips_via_dict(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("SMS_DELIVERY_FAILURE", confidence=0.90))
        d   = r.to_dict()
        r2  = ReasoningResult.from_dict(d)
        assert r2.recommended_action == r.recommended_action
        assert r2.should_escalate == r.should_escalate
        assert r2.outcome == r.outcome

    def test_bundle_nested_in_to_dict(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("NETWORK_FAILURE"))
        d   = r.to_dict()
        assert "bundle" in d
        assert "recommendation" in d["bundle"]
        assert "trace" in d["bundle"]

    def test_decision_path_preserved_in_roundtrip(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("NETWORK_FAILURE"))
        d   = r.to_dict()
        r2  = ReasoningResult.from_dict(d)
        assert len(r2.bundle.trace.decision_path) > 0


# ── WorkflowExecutionResult persistence ───────────────────────────────────────

class TestWorkflowReasoningResultPersistence:
    def test_reasoning_result_persists_to_dict_from_dict(self):
        svc  = build_reasoning_service()
        r    = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        wer  = WorkflowExecutionResult(workflow_id="test-wf")
        wer.reasoning_result = r
        d    = wer.to_dict()
        wer2 = WorkflowExecutionResult.from_dict(d)
        assert wer2.reasoning_result is not None
        assert wer2.reasoning_result["recommended_action"] == "RESET_SESSION"

    def test_reasoning_result_dict_roundtrip_preserves_status(self):
        svc  = build_reasoning_service()
        r    = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        wer  = WorkflowExecutionResult(workflow_id="test-wf")
        wer.reasoning_result = r
        d    = wer.to_dict()
        wer2 = WorkflowExecutionResult.from_dict(d)
        assert wer2.reasoning_result["status"] == "COMPLETED"


# ── Exception safety E2E ──────────────────────────────────────────────────────

class TestExceptionSafetyE2E:
    def test_none_investigation_result_from_service(self):
        svc = build_reasoning_service()
        r   = svc.reason(investigation_result=None)
        assert r["status"] == "BLOCKED"

    def test_corrupt_investigation_result_from_engine(self):
        eng = build_reasoning_engine()
        r   = eng.reason({"root_cause": []})
        assert isinstance(r, ReasoningResult)

    def test_corrupt_knowledge_result_from_engine(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("NETWORK_FAILURE"), knowledge_result="bad")  # type: ignore
        assert isinstance(r, ReasoningResult)

    def test_zero_confidence_escalates(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("NETWORK_FAILURE", confidence=0.0))
        assert r.should_escalate is True
        assert r.outcome == ReasoningOutcome.UNCERTAIN

    def test_escalate_flag_in_investigation_overrides_all(self):
        eng = build_reasoning_engine()
        r   = eng.reason(_inv("NETWORK_FAILURE", confidence=1.0, escalate=True))
        assert r.outcome == ReasoningOutcome.ESCALATE


# ── WorkflowStepType.REASON in workflow context ────────────────────────────────

class TestReasonStepTypeInWorkflowContext:
    def test_reason_step_type_exists(self):
        assert WorkflowStepType.REASON in list(WorkflowStepType)

    def test_reason_step_count(self):
        assert len(list(WorkflowStepType)) >= 11

    def test_all_legacy_step_types_still_present(self):
        legacy = [
            "COLLECT_INFORMATION", "CHECK_CONDITION", "PROPOSE_ACTION",
            "REQUEST_APPROVAL", "RESOLVE_CASE", "ESCALATE_CASE",
            "INVESTIGATE", "KNOWLEDGE_LOOKUP", "ACTION_GATEWAY", "EXECUTE",
        ]
        values = [t.value for t in WorkflowStepType]
        for name in legacy:
            assert name in values, f"Legacy step type {name} missing"
