"""
tests/test_sprint11_telemetry.py

Sprint 1.1 telemetry improvements — unit tests.

Covers:
- Case.sla_breach_at field defaults to None
- Case.to_db_row() includes sla_breach_at (None when unset, ISO string when set)
- Case.from_db_row() round-trips sla_breach_at
- log_rag_call() records cited_sop_ids in action_detail
- log_rag_call() with no cited_sop_ids stores empty list
- cited_sop_ids flows through evaluate_rag_result → log_rag_call
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from case_engine.audit import AuditLogger
from case_engine.case_state import CaseState
from case_engine.models import AuditEntry, AuditEventType, Case
from case_engine.service import build_case_service


def _now():
    return datetime.now(tz=timezone.utc)


class TestSlaBreach:
    def test_sla_breach_at_defaults_none(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        assert case.sla_breach_at is None

    def test_to_db_row_sla_breach_none(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        row  = case.to_db_row()
        assert row["sla_breach_at"] is None

    def test_to_db_row_sla_breach_iso_string(self):
        ts   = _now()
        case = Case(ticket_id="TKT-001", client="unity_bank", sla_breach_at=ts)
        row  = case.to_db_row()
        assert isinstance(row["sla_breach_at"], str)
        assert "T" in row["sla_breach_at"]  # ISO 8601 format

    def test_from_db_row_roundtrip(self):
        ts   = _now().replace(microsecond=0)
        case = Case(ticket_id="TKT-001", client="unity_bank", sla_breach_at=ts)
        row  = case.to_db_row()
        # Simulate DB round-trip via from_db_row
        restored = Case.from_db_row(row)
        assert restored.sla_breach_at is not None
        # Compare to second precision (isoformat loses no significant precision here)
        assert restored.sla_breach_at.isoformat() == ts.isoformat()

    def test_from_db_row_sla_breach_none(self):
        case = Case(ticket_id="TKT-001", client="unity_bank")
        row  = case.to_db_row()
        restored = Case.from_db_row(row)
        assert restored.sla_breach_at is None


class TestCitedSopIds:
    def _capture_rag(self, al: AuditLogger) -> list[AuditEntry]:
        captured: list[AuditEntry] = []
        al._write = lambda entry: captured.append(entry)
        return captured

    def test_cited_sop_ids_in_log_rag_call(self):
        al      = AuditLogger()
        case    = Case(ticket_id="TKT-001", client="unity_bank")
        captured = self._capture_rag(al)

        al.log_rag_call(
            case,
            match_type="exact_match",
            confidence="high",
            chunks_count=3,
            requires_human=False,
            cited_sop_ids=["SOP-001", "SOP-002"],
        )
        assert captured[0].action_detail["cited_sop_ids"] == ["SOP-001", "SOP-002"]

    def test_no_cited_sop_ids_stores_empty_list(self):
        al      = AuditLogger()
        case    = Case(ticket_id="TKT-001", client="unity_bank")
        captured = self._capture_rag(al)

        al.log_rag_call(
            case,
            match_type="no_match",
            confidence="low",
            chunks_count=0,
            requires_human=False,
        )
        assert captured[0].action_detail["cited_sop_ids"] == []

    def test_cited_sop_ids_flows_through_service(self):
        """cited_sop_ids passed to evaluate_rag_result reaches log_rag_call."""
        svc      = build_case_service(supabase_client=None)
        case     = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "OTP not received on mobile number.")

        captured: list[AuditEntry] = []
        svc._audit._write = lambda entry: captured.append(entry)

        svc.evaluate_rag_result(
            case,
            match_type="exact_match",
            confidence="high",
            requires_human=False,
            chunks_count=3,
            cited_sop_ids=["SOP-OTP-001"],
        )

        rag_entries = [e for e in captured if e.action_type == AuditEventType.RAG_CALLED]
        assert len(rag_entries) == 1
        assert rag_entries[0].action_detail["cited_sop_ids"] == ["SOP-OTP-001"]
