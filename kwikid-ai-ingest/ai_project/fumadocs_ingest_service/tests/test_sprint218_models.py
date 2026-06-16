"""
tests/test_sprint218_models.py

Sprint 2.18: Evidence domain model tests (Part A).

Coverage:
  - All Evidence subclasses (UserEvidence, SessionEvidence, LogEvidence,
    SummaryEvidence, VideoEvidence, MetricEvidence)
  - InvestigationStep and InvestigationPlan (frozen dataclasses)
  - EvidenceBundle (successful_items, get_by_type, get_by_source, evidence_ids)
  - RootCauseAnalysis to_dict
  - InvestigationResult to_dict
  - Enum values for EvidenceType, EvidenceSource, RootCauseCategory, RecommendedAction
"""
from __future__ import annotations

import pytest

from case_engine.investigation.models import (
    Evidence,
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    InvestigationPlan,
    InvestigationResult,
    InvestigationStep,
    LogEvidence,
    MetricEvidence,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
    SessionEvidence,
    SummaryEvidence,
    UserEvidence,
    VideoEvidence,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_user_evidence(success: bool = True) -> UserEvidence:
    return UserEvidence(
        evidence_id="ev-user-001",
        evidence_type=EvidenceType.USER,
        source=EvidenceSource.GET_USER_DETAILS,
        tool_name="GetUserDetailsTool",
        payload={"kyc_status": "PARTIAL", "account_state": "ACTIVE"} if success else {},
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-001",
        success=success,
        error_code=None if success else "TOOL_ERROR",
        error_message=None if success else "Connection refused",
    )


def make_session_evidence(success: bool = True) -> SessionEvidence:
    return SessionEvidence(
        evidence_id="ev-session-001",
        evidence_type=EvidenceType.SESSION,
        source=EvidenceSource.GET_SESSION_DETAILS,
        tool_name="GetSessionDetailsTool",
        payload={"session_status": "FAILED", "failure_code": "SESSION_TIMEOUT"} if success else {},
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-002",
        success=success,
    )


def make_bundle(items: list[Evidence] | None = None) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="bundle-001",
        case_id="case-001",
        topic="VKYC_Session_Failure",
        plan_id="plan-001",
        items=items or [],
        collected_at="2026-06-10T00:00:00+00:00",
    )


def make_rca(
    category: RootCauseCategory = RootCauseCategory.EXPIRED_SESSION,
    escalate: bool = False,
) -> RootCauseAnalysis:
    return RootCauseAnalysis(
        analysis_id="rca-001",
        case_id="case-001",
        topic="VKYC_Session_Failure",
        category=category,
        confidence=0.9,
        explanation="Session expired.",
        evidence_ids=["ev-001"],
        recommended_action=RecommendedAction.SESSION_RESET,
        escalate=escalate,
        analysed_at="2026-06-10T00:00:00+00:00",
    )


# ── EvidenceType tests ─────────────────────────────────────────────────────────

class TestEvidenceType:
    def test_all_values_are_strings(self):
        for et in EvidenceType:
            assert isinstance(et.value, str)

    def test_expected_members(self):
        values = {et.value for et in EvidenceType}
        assert "USER" in values
        assert "SESSION" in values
        assert "LOG" in values
        assert "SUMMARY" in values
        assert "VIDEO" in values
        assert "METRIC" in values


class TestEvidenceSource:
    def test_source_values_match_tool_names(self):
        assert EvidenceSource.GET_USER_DETAILS.value == "GetUserDetailsTool"
        assert EvidenceSource.GET_SESSION_DETAILS.value == "GetSessionDetailsTool"
        assert EvidenceSource.GET_FAILURE_REASON.value == "GetFailureReasonTool"
        assert EvidenceSource.GET_CASE_HISTORY.value == "GetCaseHistoryTool"
        assert EvidenceSource.GET_ONBOARDING_STATUS.value == "GetOnboardingStatusTool"


class TestRootCauseCategory:
    def test_all_expected_categories_present(self):
        categories = {rc.value for rc in RootCauseCategory}
        required = {
            "NETWORK_FAILURE", "TIMEOUT", "QUOTA_EXCEEDED",
            "EXPIRED_SESSION", "REPEATED_FAILURE", "LIVENESS_FAILURE",
            "DOCUMENT_FAILURE", "VALIDATION_FAILURE", "KYC_REJECTED",
            "SMS_DELIVERY_FAILURE", "CALLBACK_FAILURE",
            "ONBOARDING_BLOCKED", "PORTAL_UNAVAILABLE", "UNKNOWN",
        }
        assert required.issubset(categories)


class TestRecommendedAction:
    def test_all_expected_actions_present(self):
        actions = {ra.value for ra in RecommendedAction}
        required = {
            "SESSION_RESET", "OTP_RESEND", "PORTAL_REFRESH",
            "CALLBACK_RETRY", "AUTO_ADVANCE", "MANUAL_REVIEW",
            "ESCALATE", "RETRY",
        }
        assert required.issubset(actions)


# ── Evidence subclass tests ────────────────────────────────────────────────────

class TestUserEvidence:
    def test_successful_evidence_to_dict(self):
        ev = make_user_evidence(success=True)
        d = ev.to_dict()
        assert d["evidence_id"] == "ev-user-001"
        assert d["evidence_type"] == "USER"
        assert d["source"] == "GetUserDetailsTool"
        assert d["success"] is True
        assert d["error_code"] is None

    def test_failed_evidence_to_dict(self):
        ev = make_user_evidence(success=False)
        d = ev.to_dict()
        assert d["success"] is False
        assert d["error_code"] == "TOOL_ERROR"
        assert d["payload"] == {}

    def test_is_evidence_subclass(self):
        assert isinstance(make_user_evidence(), Evidence)


class TestSessionEvidence:
    def test_session_type(self):
        ev = make_session_evidence()
        assert ev.evidence_type == EvidenceType.SESSION
        assert ev.source == EvidenceSource.GET_SESSION_DETAILS

    def test_to_dict_has_all_keys(self):
        d = make_session_evidence().to_dict()
        required_keys = {
            "evidence_id", "evidence_type", "source", "tool_name",
            "payload", "collected_at", "invocation_id", "success",
            "error_code", "error_message",
        }
        assert required_keys.issubset(d.keys())


class TestLogEvidence:
    def test_log_evidence_construction(self):
        ev = LogEvidence(
            evidence_id="ev-log-001",
            evidence_type=EvidenceType.LOG,
            source=EvidenceSource.GET_FAILURE_REASON,
            tool_name="GetFailureReasonTool",
            payload={"failure_category": "TIMEOUT"},
            collected_at="2026-06-10T00:00:00+00:00",
            invocation_id="inv-003",
            success=True,
        )
        assert ev.evidence_type == EvidenceType.LOG
        assert isinstance(ev, Evidence)


class TestSummaryEvidence:
    def test_summary_evidence_to_dict(self):
        ev = SummaryEvidence(
            evidence_id="ev-sum-001",
            evidence_type=EvidenceType.SUMMARY,
            source=EvidenceSource.GET_CASE_HISTORY,
            tool_name="GetCaseHistoryTool",
            payload={"case_count": 3},
            collected_at="2026-06-10T00:00:00+00:00",
            invocation_id="inv-004",
            success=True,
        )
        d = ev.to_dict()
        assert d["evidence_type"] == "SUMMARY"
        assert d["payload"]["case_count"] == 3


class TestVideoEvidence:
    def test_video_evidence_is_evidence(self):
        ev = VideoEvidence(
            evidence_id="ev-vid-001",
            evidence_type=EvidenceType.VIDEO,
            source=EvidenceSource.GET_SESSION_DETAILS,
            tool_name="VideoAnalysisTool",
            payload={},
            collected_at="2026-06-10T00:00:00+00:00",
            invocation_id="inv-005",
            success=False,
            error_code="NOT_IMPLEMENTED",
        )
        assert isinstance(ev, Evidence)


class TestMetricEvidence:
    def test_metric_evidence_is_evidence(self):
        ev = MetricEvidence(
            evidence_id="ev-met-001",
            evidence_type=EvidenceType.METRIC,
            source=EvidenceSource.GET_FAILURE_REASON,
            tool_name="MetricsTool",
            payload={},
            collected_at="2026-06-10T00:00:00+00:00",
            invocation_id="inv-006",
            success=False,
            error_code="NOT_IMPLEMENTED",
        )
        assert isinstance(ev, Evidence)


# ── InvestigationStep tests ────────────────────────────────────────────────────

class TestInvestigationStep:
    def test_step_is_frozen(self):
        step = InvestigationStep(
            step_id="step_00_getsession",
            sequence=0,
            tool_name="GetSessionDetailsTool",
            purpose="Get session",
            required_slot="session_id",
            input_key="session_id",
        )
        with pytest.raises((AttributeError, TypeError)):
            step.sequence = 99  # type: ignore[misc]

    def test_step_to_dict(self):
        step = InvestigationStep(
            step_id="step_00_getsession",
            sequence=0,
            tool_name="GetSessionDetailsTool",
            purpose="Get session details",
            required_slot="session_id",
            input_key="session_id",
        )
        d = step.to_dict()
        assert d["step_id"] == "step_00_getsession"
        assert d["sequence"] == 0
        assert d["tool_name"] == "GetSessionDetailsTool"
        assert d["required_slot"] == "session_id"
        assert d["input_key"] == "session_id"


# ── InvestigationPlan tests ────────────────────────────────────────────────────

class TestInvestigationPlan:
    def _make_plan(self, steps=None):
        return InvestigationPlan(
            plan_id="plan-001",
            case_id="case-001",
            topic="VKYC_Session_Failure",
            workflow_id=None,
            steps=steps or (),
            created_at="2026-06-10T00:00:00+00:00",
        )

    def test_plan_is_frozen(self):
        plan = self._make_plan()
        with pytest.raises((AttributeError, TypeError)):
            plan.topic = "other"  # type: ignore[misc]

    def test_plan_to_dict_empty_steps(self):
        d = self._make_plan().to_dict()
        assert d["step_count"] == 0
        assert d["steps"] == []
        assert d["workflow_id"] is None

    def test_plan_to_dict_with_steps(self):
        step = InvestigationStep(
            step_id="s0", sequence=0,
            tool_name="GetSessionDetailsTool",
            purpose="p", required_slot="session_id", input_key="session_id",
        )
        d = self._make_plan(steps=(step,)).to_dict()
        assert d["step_count"] == 1
        assert len(d["steps"]) == 1


# ── EvidenceBundle tests ───────────────────────────────────────────────────────

class TestEvidenceBundle:
    def test_empty_bundle(self):
        b = make_bundle()
        assert b.successful_items == []
        assert b.evidence_ids() == []

    def test_successful_items_filter(self):
        ok  = make_user_evidence(success=True)
        bad = make_session_evidence(success=False)
        b   = make_bundle(items=[ok, bad])
        assert len(b.successful_items) == 1
        assert b.successful_items[0].evidence_id == "ev-user-001"

    def test_get_by_type(self):
        ok  = make_user_evidence(success=True)
        bad = make_session_evidence(success=False)
        b   = make_bundle(items=[ok, bad])
        user_ev = b.get_by_type(EvidenceType.USER)
        assert len(user_ev) == 1

    def test_get_by_source_returns_first_successful(self):
        ok  = make_user_evidence(success=True)
        bad = make_user_evidence(success=False)
        b   = make_bundle(items=[ok, bad])
        result = b.get_by_source(EvidenceSource.GET_USER_DETAILS)
        assert result is not None
        assert result.evidence_id == "ev-user-001"

    def test_get_by_source_returns_none_when_none_succeed(self):
        bad = make_user_evidence(success=False)
        b   = make_bundle(items=[bad])
        assert b.get_by_source(EvidenceSource.GET_USER_DETAILS) is None

    def test_evidence_ids_returns_only_successful(self):
        ok  = make_user_evidence(success=True)
        bad = make_session_evidence(success=False)
        b   = make_bundle(items=[ok, bad])
        ids = b.evidence_ids()
        assert "ev-user-001" in ids
        assert "ev-session-001" not in ids

    def test_to_dict_structure(self):
        b = make_bundle(items=[make_user_evidence()])
        d = b.to_dict()
        assert d["bundle_id"] == "bundle-001"
        assert d["total_items"] == 1
        assert d["success_count"] == 1
        assert len(d["items"]) == 1


# ── RootCauseAnalysis tests ────────────────────────────────────────────────────

class TestRootCauseAnalysis:
    def test_to_dict_all_fields(self):
        rca = make_rca()
        d   = rca.to_dict()
        assert d["analysis_id"] == "rca-001"
        assert d["category"] == "EXPIRED_SESSION"
        assert d["confidence"] == 0.9
        assert d["recommended_action"] == "SESSION_RESET"
        assert d["escalate"] is False

    def test_escalate_flag(self):
        rca = make_rca(escalate=True)
        assert rca.to_dict()["escalate"] is True


# ── InvestigationResult tests ──────────────────────────────────────────────────

class TestInvestigationResult:
    def test_to_dict_contains_all_layers(self):
        plan = InvestigationPlan(
            plan_id="plan-001", case_id="case-001", topic="VKYC_Session_Failure",
            workflow_id=None, steps=(), created_at="2026-06-10T00:00:00+00:00",
        )
        bundle = make_bundle()
        rca    = make_rca()
        result = InvestigationResult(
            result_id="result-001",
            case_id="case-001",
            plan=plan,
            bundle=bundle,
            root_cause=rca,
            observation="Test observation text.",
            completed_at="2026-06-10T00:00:00+00:00",
        )
        d = result.to_dict()
        assert d["result_id"] == "result-001"
        assert "plan" in d
        assert "evidence" in d
        assert "root_cause" in d
        assert d["observation"] == "Test observation text."
