"""
tests/test_sprint218_service.py

Sprint 2.18: InvestigationService and build_investigation_service factory tests (Part F).

Coverage:
  - Full pipeline (plan → collect → rca → observation → result)
  - Result contains plan, bundle, root_cause, observation, result_id, completed_at
  - Audit events emitted when case is present
  - No crash when case is None (admin invocation)
  - Error result produced when pipeline raises unexpectedly
  - build_investigation_service factory wiring
  - InvestigationService.investigate never raises to callers
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_engine.investigation import build_investigation_service, InvestigationService
from case_engine.investigation._collector_sprint218 import EvidenceCollector
from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceType,
    InvestigationResult,
    RecommendedAction,
    RootCauseCategory,
)
from case_engine.investigation._observation_sprint218 import ObservationGenerator
from case_engine.investigation.planner import InvestigationPlanner
from case_engine.investigation._root_cause_sprint218 import RootCauseEngine
from case_engine.tools.mock_tools import (
    GetFailureReasonTool,
    GetSessionDetailsTool,
    GetUserDetailsTool,
)
from case_engine.tools.tool_executor import ToolExecutor
from case_engine.tools.tool_registry import ToolRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _build_real_service() -> InvestigationService:
    registry = ToolRegistry()
    registry.register(GetSessionDetailsTool())
    registry.register(GetUserDetailsTool())
    registry.register(GetFailureReasonTool())
    executor = ToolExecutor(registry)
    return build_investigation_service(executor, audit_logger=None)


def _slot_values_vkyc() -> dict:
    return {
        "_meta":       {"case_id": "case-svc-001"},
        "session_id":  "SESSION-TEST-123",
        "phone_number": "9999999999",
    }


def _slot_values_otp() -> dict:
    return {
        "_meta":        {"case_id": "case-svc-002"},
        "phone_number": "9999999999",
        "session_id":   "SESSION-OTP-001",
    }


# ── Full pipeline integration tests ───────────────────────────────────────────

class TestInvestigationServicePipeline:
    def test_vkyc_pipeline_returns_result(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert isinstance(result, InvestigationResult)

    def test_result_has_non_empty_result_id(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert len(result.result_id) == 36

    def test_result_case_id_matches_meta(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert result.case_id == "case-svc-001"

    def test_plan_contains_correct_tools(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        tool_names = [s.tool_name for s in result.plan.steps]
        assert "GetSessionDetailsTool" in tool_names

    def test_bundle_has_items(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert len(result.bundle.items) > 0

    def test_root_cause_category_is_set(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert result.root_cause.category in RootCauseCategory.__members__.values()

    def test_observation_is_non_empty_string(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert isinstance(result.observation, str)
        assert len(result.observation) > 100

    def test_completed_at_is_iso(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert "T" in result.completed_at

    def test_otp_pipeline_runs_correctly(self):
        svc    = _build_real_service()
        result = svc.investigate("OTP_Delivery_Failure", None, _slot_values_otp())
        assert isinstance(result, InvestigationResult)

    def test_to_dict_complete(self):
        svc    = _build_real_service()
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        d = result.to_dict()
        assert "plan" in d
        assert "evidence" in d
        assert "root_cause" in d
        assert "observation" in d

    def test_unknown_topic_still_returns_result(self):
        svc    = _build_real_service()
        result = svc.investigate("UNKNOWN_TOPIC_XYZ", None, {})
        assert isinstance(result, InvestigationResult)
        assert result.root_cause.category == RootCauseCategory.UNKNOWN


# ── Audit integration tests ───────────────────────────────────────────────────

class TestInvestigationServiceAudit:
    def _make_case(self, case_id: str = "c-001") -> MagicMock:
        case = MagicMock()
        case.case_id  = case_id
        case.ticket_id = "T-001"
        case.client    = "test"
        return case

    def test_audit_investigation_started_called_when_case_present(self):
        audit = MagicMock()
        svc   = _build_real_service()
        svc._audit = audit
        svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc(), case=self._make_case())
        audit.log_investigation_started.assert_called_once()

    def test_audit_investigation_completed_called_when_case_present(self):
        audit = MagicMock()
        svc   = _build_real_service()
        svc._audit = audit
        svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc(), case=self._make_case())
        audit.log_investigation_completed.assert_called_once()

    def test_no_audit_calls_when_case_is_none(self):
        audit = MagicMock()
        svc   = _build_real_service()
        svc._audit = audit
        svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc(), case=None)
        audit.log_investigation_started.assert_not_called()
        audit.log_investigation_completed.assert_not_called()

    def test_no_audit_calls_when_audit_logger_is_none(self):
        svc = _build_real_service()
        svc._audit = None
        # Must not raise
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc(), case=self._make_case())
        assert isinstance(result, InvestigationResult)


# ── Error recovery tests ───────────────────────────────────────────────────────

class TestInvestigationServiceErrorRecovery:
    def test_investigate_never_raises_on_collector_crash(self):
        planner   = InvestigationPlanner()
        collector = MagicMock()
        collector.collect.side_effect = RuntimeError("Simulated DB crash")
        rca = RootCauseEngine()
        obs = ObservationGenerator()
        svc = InvestigationService(planner, collector, rca, obs, audit_logger=None)
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert isinstance(result, InvestigationResult)
        assert result.root_cause.category == RootCauseCategory.UNKNOWN
        assert result.root_cause.escalate is True

    def test_investigate_never_raises_on_rca_crash(self):
        svc = _build_real_service()
        svc._rca = MagicMock()
        svc._rca.analyse.side_effect = RuntimeError("RCA crash")
        result = svc.investigate("VKYC_Session_Failure", None, _slot_values_vkyc())
        assert isinstance(result, InvestigationResult)

    def test_error_result_has_escalate_true(self):
        svc = _build_real_service()
        svc._collector = MagicMock()
        svc._collector.collect.side_effect = ValueError("Unexpected error")
        result = svc.investigate("VKYC_Session_Failure", None, {})
        assert result.root_cause.escalate is True

    def test_error_result_observation_mentions_error(self):
        svc = _build_real_service()
        svc._collector = MagicMock()
        svc._collector.collect.side_effect = ValueError("Unexpected crash in pipeline")
        result = svc.investigate("VKYC_Session_Failure", None, {})
        assert "error" in result.observation.lower() or "manual" in result.observation.lower()


# ── build_investigation_service factory ───────────────────────────────────────

class TestBuildInvestigationServiceFactory:
    def test_factory_returns_investigation_service(self):
        executor = MagicMock()
        svc = build_investigation_service(executor)
        assert isinstance(svc, InvestigationService)

    def test_factory_wires_all_components(self):
        executor = MagicMock()
        svc = build_investigation_service(executor)
        assert isinstance(svc._planner, InvestigationPlanner)
        assert isinstance(svc._collector, EvidenceCollector)
        assert isinstance(svc._rca, RootCauseEngine)
        assert isinstance(svc._obs, ObservationGenerator)

    def test_factory_audit_logger_is_none_by_default(self):
        executor = MagicMock()
        svc = build_investigation_service(executor)
        assert svc._audit is None

    def test_factory_accepts_audit_logger(self):
        executor = MagicMock()
        audit    = MagicMock()
        svc = build_investigation_service(executor, audit_logger=audit)
        assert svc._audit is audit
