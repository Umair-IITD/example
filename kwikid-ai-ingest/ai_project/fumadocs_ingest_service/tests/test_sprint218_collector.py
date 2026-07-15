"""
tests/test_sprint218_collector.py

Sprint 2.18: EvidenceCollector tests (Part C).

Coverage:
  - Successful tool invocation produces correctly typed Evidence
  - Failed tool invocation records error_code and error_message
  - Missing required slot produces MISSING_REQUIRED_SLOT evidence (no crash)
  - SlotValue object resolution (status=FILLED vs status=PENDING)
  - Raw string slot resolution
  - Unknown tool falls back to LogEvidence + GET_FAILURE_REASON
  - EvidenceBundle metadata (bundle_id, case_id, topic, plan_id, collected_at)
  - All evidence is collected even when some steps fail (never raises)
  - _resolve_slot handles None, empty string, unfilled SlotValue
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_engine.investigation._collector_sprint218 import EvidenceCollector, _resolve_slot
from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    InvestigationPlan,
    InvestigationStep,
    LogEvidence,
    SessionEvidence,
    UserEvidence,
)
from case_engine.tools.tool_models import ToolResult


# ── Factories ──────────────────────────────────────────────────────────────────

def make_tool_result(success: bool, payload: dict | None = None, error_code: str | None = None) -> ToolResult:
    return ToolResult(
        invocation_id="inv-001",
        tool_name="SomeTool",
        success=success,
        payload=payload or {},
        executed_at="2026-06-10T00:00:00+00:00",
        error_code=error_code,
        error_message=f"Error: {error_code}" if error_code else None,
    )


def make_step(
    tool_name: str,
    required_slot: str = "session_id",
    input_key: str = "session_id",
    seq: int = 0,
) -> InvestigationStep:
    return InvestigationStep(
        step_id=f"step_{seq:02d}_{tool_name.lower()[:10]}",
        sequence=seq,
        tool_name=tool_name,
        purpose="Test step",
        required_slot=required_slot,
        input_key=input_key,
    )


def make_plan(steps: list[InvestigationStep], case_id: str = "case-001") -> InvestigationPlan:
    return InvestigationPlan(
        plan_id="plan-001",
        case_id=case_id,
        topic="VKYC_Session_Failure",
        workflow_id=None,
        steps=tuple(steps),
        created_at="2026-06-10T00:00:00+00:00",
    )


def make_executor(return_result: ToolResult) -> MagicMock:
    executor = MagicMock()
    executor.execute.return_value = return_result
    return executor


# ── _resolve_slot tests ────────────────────────────────────────────────────────

class TestResolveSlot:
    def test_plain_string_resolved(self):
        assert _resolve_slot("session_id", {"session_id": "S123"}) == "S123"

    def test_empty_string_returns_none(self):
        assert _resolve_slot("session_id", {"session_id": ""}) is None

    def test_whitespace_only_returns_none(self):
        assert _resolve_slot("session_id", {"session_id": "   "}) is None

    def test_missing_key_returns_none(self):
        assert _resolve_slot("session_id", {}) is None

    def test_strips_whitespace(self):
        assert _resolve_slot("session_id", {"session_id": "  S123  "}) == "S123"

    def test_slot_value_filled_resolved(self):
        from unittest.mock import MagicMock
        from case_engine.slot_filling.models import SlotStatus
        sv = MagicMock()
        sv.status = SlotStatus.FILLED
        sv.value = "S999"
        assert _resolve_slot("session_id", {"session_id": sv}) == "S999"

    def test_slot_value_pending_returns_none(self):
        from unittest.mock import MagicMock
        from case_engine.slot_filling.models import SlotStatus
        sv = MagicMock()
        sv.status = SlotStatus.PENDING
        sv.value = None
        assert _resolve_slot("session_id", {"session_id": sv}) is None

    def test_slot_value_filled_but_none_value_returns_none(self):
        from unittest.mock import MagicMock
        from case_engine.slot_filling.models import SlotStatus
        sv = MagicMock()
        sv.status = SlotStatus.FILLED
        sv.value = None
        assert _resolve_slot("session_id", {"session_id": sv}) is None


# ── EvidenceCollector — successful collection ──────────────────────────────────

class TestEvidenceCollectorSuccess:
    def test_session_tool_produces_session_evidence(self):
        result   = make_tool_result(success=True, payload={"session_status": "FAILED"})
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        step  = make_step("GetSessionDetailsTool", "session_id", "session_id")
        plan  = make_plan([step])
        bundle = collector.collect(plan, {"session_id": "S123"})
        assert len(bundle.items) == 1
        ev = bundle.items[0]
        assert isinstance(ev, SessionEvidence)
        assert ev.evidence_type == EvidenceType.SESSION
        assert ev.source == EvidenceSource.GET_SESSION_DETAILS
        assert ev.success is True
        assert ev.payload == {"session_status": "FAILED"}

    def test_user_tool_produces_user_evidence(self):
        result   = make_tool_result(success=True, payload={"kyc_status": "PARTIAL"})
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        step  = make_step("GetUserDetailsTool", "phone_number", "phone_number")
        plan  = make_plan([step])
        bundle = collector.collect(plan, {"phone_number": "9999"})
        ev = bundle.items[0]
        assert isinstance(ev, UserEvidence)
        assert ev.evidence_type == EvidenceType.USER

    def test_executor_called_with_correct_arguments(self):
        result   = make_tool_result(success=True)
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        step  = make_step("GetSessionDetailsTool", "session_id", "session_id")
        plan  = make_plan([step])
        collector.collect(plan, {"session_id": "SESSION-999"})
        executor.execute.assert_called_once_with(
            "GetSessionDetailsTool",
            {"session_id": "SESSION-999"},
            requested_by="investigation_collector",
        )

    def test_bundle_metadata_correct(self):
        result   = make_tool_result(success=True)
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        plan  = make_plan([make_step("GetSessionDetailsTool")])
        bundle = collector.collect(plan, {"session_id": "S1"})
        assert bundle.case_id == "case-001"
        assert bundle.topic == "VKYC_Session_Failure"
        assert bundle.plan_id == "plan-001"
        assert bundle.bundle_id  # non-empty UUID

    def test_multiple_steps_produce_multiple_evidence(self):
        result   = make_tool_result(success=True)
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        steps = [
            make_step("GetSessionDetailsTool", "session_id", "session_id", seq=0),
            make_step("GetUserDetailsTool", "phone_number", "phone_number", seq=1),
        ]
        plan   = make_plan(steps)
        bundle = collector.collect(plan, {"session_id": "S1", "phone_number": "9999"})
        assert len(bundle.items) == 2

    def test_evidence_ids_are_unique(self):
        result   = make_tool_result(success=True)
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        steps = [
            make_step("GetSessionDetailsTool", "session_id", "session_id", seq=0),
            make_step("GetUserDetailsTool", "phone_number", "phone_number", seq=1),
        ]
        plan   = make_plan(steps)
        bundle = collector.collect(plan, {"session_id": "S1", "phone_number": "9999"})
        ids = [ev.evidence_id for ev in bundle.items]
        assert len(ids) == len(set(ids))


# ── EvidenceCollector — failure handling ──────────────────────────────────────

class TestEvidenceCollectorFailure:
    def test_failed_tool_result_records_error(self):
        result   = make_tool_result(success=False, error_code="CONNECTION_REFUSED")
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        step  = make_step("GetSessionDetailsTool")
        plan  = make_plan([step])
        bundle = collector.collect(plan, {"session_id": "S1"})
        ev = bundle.items[0]
        assert ev.success is False
        assert ev.error_code == "CONNECTION_REFUSED"
        assert ev.payload == {}

    def test_missing_slot_produces_error_evidence(self):
        executor  = MagicMock()
        collector = EvidenceCollector(executor)
        step  = make_step("GetSessionDetailsTool", required_slot="session_id")
        plan  = make_plan([step])
        # slot_values does not contain session_id
        bundle = collector.collect(plan, {"phone_number": "9999"})
        ev = bundle.items[0]
        assert ev.success is False
        assert ev.error_code == "MISSING_REQUIRED_SLOT"
        # executor was NOT called
        executor.execute.assert_not_called()

    def test_missing_slot_error_message_mentions_slot_name(self):
        executor  = MagicMock()
        collector = EvidenceCollector(executor)
        step  = make_step("GetSessionDetailsTool", required_slot="session_id")
        plan  = make_plan([step])
        bundle = collector.collect(plan, {})
        assert "session_id" in bundle.items[0].error_message

    def test_all_steps_collected_even_when_some_fail(self):
        def side_effect(tool_name, inputs, **kwargs):
            if tool_name == "GetSessionDetailsTool":
                return make_tool_result(success=False, error_code="ERR")
            return make_tool_result(success=True, payload={"kyc_status": "OK"})

        executor = MagicMock()
        executor.execute.side_effect = side_effect
        collector = EvidenceCollector(executor)
        steps = [
            make_step("GetSessionDetailsTool", "session_id", "session_id", seq=0),
            make_step("GetUserDetailsTool",    "phone_number", "phone_number", seq=1),
        ]
        plan  = make_plan(steps)
        bundle = collector.collect(plan, {"session_id": "S1", "phone_number": "9999"})
        assert len(bundle.items) == 2
        assert bundle.items[0].success is False
        assert bundle.items[1].success is True

    def test_unknown_tool_falls_back_to_log_evidence(self):
        result   = make_tool_result(success=True, payload={"some_key": "val"})
        executor = make_executor(result)
        collector = EvidenceCollector(executor)
        step  = make_step("UnknownToolXYZ", "session_id", "session_id")
        plan  = make_plan([step])
        bundle = collector.collect(plan, {"session_id": "S1"})
        ev = bundle.items[0]
        assert isinstance(ev, LogEvidence)
        assert ev.evidence_type == EvidenceType.LOG

    def test_empty_plan_produces_empty_bundle(self):
        executor  = MagicMock()
        collector = EvidenceCollector(executor)
        plan   = make_plan([])
        bundle = collector.collect(plan, {})
        assert len(bundle.items) == 0
        executor.execute.assert_not_called()
