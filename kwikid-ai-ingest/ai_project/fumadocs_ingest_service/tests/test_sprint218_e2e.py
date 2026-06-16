"""
tests/test_sprint218_e2e.py

Sprint 2.18: End-to-end integration tests for the full Investigation Layer (Part I).

These tests wire together the complete pipeline using real components:
  InvestigationPlanner → EvidenceCollector → RootCauseEngine → ObservationGenerator
  → InvestigationService → InvestigationResult

Tools used: the deterministic MockTools from Sprint 2.17 (GetSessionDetailsTool,
GetUserDetailsTool, GetFailureReasonTool, GetOnboardingStatusTool).

Coverage:
  - All 5 supported topics produce valid InvestigationResult objects
  - Plan steps match the expected tools for each topic
  - EvidenceBundle has items for each step (success or fail, never raises)
  - Root cause category is a valid RootCauseCategory
  - Observation note contains all 5 blueprint sections
  - to_dict() is fully serializable (no non-JSON types)
  - Repeated runs produce independent result_ids (deterministic but not idempotent)
  - Missing required slots produce MISSING_REQUIRED_SLOT evidence (not crash)
  - Empty slot_values run completes safely for unknown topics
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from case_engine.investigation import build_investigation_service
from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationResult,
    RootCauseCategory,
)
from case_engine.tools.mock_tools import (
    GetCaseHistoryTool,
    GetFailureReasonTool,
    GetOnboardingStatusTool,
    GetSessionDetailsTool,
    GetUserDetailsTool,
)
from case_engine.tools.tool_executor import ToolExecutor
from case_engine.tools.tool_registry import ToolRegistry


# ── Full registry / service fixture ───────────────────────────────────────────

@pytest.fixture(scope="module")
def inv_service():
    registry = ToolRegistry()
    for tool_cls in [
        GetSessionDetailsTool,
        GetUserDetailsTool,
        GetFailureReasonTool,
        GetCaseHistoryTool,
        GetOnboardingStatusTool,
    ]:
        registry.register(tool_cls())
    executor = ToolExecutor(registry)
    return build_investigation_service(executor, audit_logger=None)


def _vkyc_slots() -> dict[str, Any]:
    return {"_meta": {"case_id": "e2e-vkyc-001"}, "session_id": "S-VKYC-001", "phone_number": "9876543210"}

def _otp_slots() -> dict[str, Any]:
    return {"_meta": {"case_id": "e2e-otp-001"}, "phone_number": "9876543210", "session_id": "S-OTP-001"}

def _ocr_slots() -> dict[str, Any]:
    return {"_meta": {"case_id": "e2e-ocr-001"}, "phone_number": "9876543210", "application_id": "APP-OCR-001"}

def _portal_slots() -> dict[str, Any]:
    return {"_meta": {"case_id": "e2e-portal-001"}, "phone_number": "9876543210", "session_id": "S-PORTAL-001"}

def _callback_slots() -> dict[str, Any]:
    return {"_meta": {"case_id": "e2e-cb-001"}, "session_id": "S-CB-001"}


# ── Per-topic happy path ───────────────────────────────────────────────────────

class TestE2EVKYCSessionFailure:
    def test_returns_investigation_result(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert isinstance(result, InvestigationResult)

    def test_plan_has_session_and_user_tools(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        tool_names = [s.tool_name for s in result.plan.steps]
        assert "GetSessionDetailsTool" in tool_names
        assert "GetUserDetailsTool" in tool_names

    def test_bundle_has_correct_item_count(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert len(result.bundle.items) == 2

    def test_all_evidence_items_have_ids(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        for ev in result.bundle.items:
            assert ev.evidence_id

    def test_root_cause_category_valid(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert result.root_cause.category in list(RootCauseCategory)

    def test_root_cause_confidence_in_range(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert 0.0 <= result.root_cause.confidence <= 1.0

    def test_observation_has_all_5_sections(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        obs = result.observation
        assert "=== ISSUE SUMMARY ===" in obs
        assert "=== OBSERVED EVIDENCE ===" in obs
        assert "=== ROOT CAUSE ===" in obs
        assert "=== RECOMMENDED ACTION ===" in obs
        assert "=== ESCALATION REQUIRED ===" in obs

    def test_to_dict_is_json_serializable(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        serialized = json.dumps(result.to_dict())
        assert len(serialized) > 0


class TestE2EOTPDeliveryFailure:
    def test_returns_investigation_result(self, inv_service):
        result = inv_service.investigate("OTP_Delivery_Failure", None, _otp_slots())
        assert isinstance(result, InvestigationResult)

    def test_plan_has_user_and_failure_tools(self, inv_service):
        result = inv_service.investigate("OTP_Delivery_Failure", None, _otp_slots())
        tool_names = [s.tool_name for s in result.plan.steps]
        assert "GetUserDetailsTool" in tool_names
        assert "GetFailureReasonTool" in tool_names

    def test_root_cause_is_valid(self, inv_service):
        result = inv_service.investigate("OTP_Delivery_Failure", None, _otp_slots())
        assert result.root_cause.category in list(RootCauseCategory)


class TestE2EDocumentOCRFailure:
    def test_returns_investigation_result(self, inv_service):
        result = inv_service.investigate("Document_OCR_Failure", None, _ocr_slots())
        assert isinstance(result, InvestigationResult)

    def test_plan_has_onboarding_tool(self, inv_service):
        result = inv_service.investigate("Document_OCR_Failure", None, _ocr_slots())
        tool_names = [s.tool_name for s in result.plan.steps]
        assert "GetOnboardingStatusTool" in tool_names


class TestE2EAgentPortalIssue:
    def test_returns_investigation_result(self, inv_service):
        result = inv_service.investigate("Agent_Portal_Issue", None, _portal_slots())
        assert isinstance(result, InvestigationResult)


class TestE2EAPICallbackFailure:
    def test_returns_investigation_result(self, inv_service):
        result = inv_service.investigate("API_Callback_Failure", None, _callback_slots())
        assert isinstance(result, InvestigationResult)

    def test_plan_has_failure_reason_tool(self, inv_service):
        result = inv_service.investigate("API_Callback_Failure", None, _callback_slots())
        tool_names = [s.tool_name for s in result.plan.steps]
        assert "GetFailureReasonTool" in tool_names


# ── Missing slots tests ────────────────────────────────────────────────────────

class TestE2EMissingSlots:
    def test_missing_session_id_produces_failed_evidence(self, inv_service):
        slots  = {"_meta": {"case_id": "e2e-missing-001"}, "phone_number": "9999"}
        result = inv_service.investigate("VKYC_Session_Failure", None, slots)
        session_ev = [ev for ev in result.bundle.items
                      if ev.tool_name == "GetSessionDetailsTool"]
        assert len(session_ev) == 1
        assert session_ev[0].success is False
        assert session_ev[0].error_code == "MISSING_REQUIRED_SLOT"

    def test_all_missing_slots_produces_unknown_rca(self, inv_service):
        result = inv_service.investigate("VKYC_Session_Failure", None, {})
        assert result.root_cause.category == RootCauseCategory.UNKNOWN or result.root_cause.escalate


# ── Repeatability tests ────────────────────────────────────────────────────────

class TestE2ERepeatability:
    def test_two_runs_produce_different_result_ids(self, inv_service):
        r1 = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        r2 = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert r1.result_id != r2.result_id

    def test_two_runs_produce_different_bundle_ids(self, inv_service):
        r1 = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        r2 = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert r1.bundle.bundle_id != r2.bundle.bundle_id

    def test_two_runs_same_root_cause_category(self, inv_service):
        r1 = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        r2 = inv_service.investigate("VKYC_Session_Failure", None, _vkyc_slots())
        assert r1.root_cause.category == r2.root_cause.category


# ── Unknown topic tests ────────────────────────────────────────────────────────

class TestE2EUnknownTopic:
    def test_unknown_topic_never_raises(self, inv_service):
        result = inv_service.investigate("COMPLETELY_UNKNOWN_TOPIC", None, {})
        assert isinstance(result, InvestigationResult)

    def test_unknown_topic_escalates(self, inv_service):
        result = inv_service.investigate("COMPLETELY_UNKNOWN_TOPIC", None, {})
        assert result.root_cause.escalate is True

    def test_unknown_topic_produces_observation(self, inv_service):
        result = inv_service.investigate("COMPLETELY_UNKNOWN_TOPIC", None, {})
        assert isinstance(result.observation, str)
        assert len(result.observation) > 0
