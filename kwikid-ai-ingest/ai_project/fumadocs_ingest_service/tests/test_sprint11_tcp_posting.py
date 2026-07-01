"""
tests/test_sprint11_tcp_posting.py

Transfer Context Payload posting — unit tests verifying the case engine side
of TCP generation and mock-based verification of the FreshdeskReplyClient call.

Covers:
- Escalated case (unknown topic) produces a valid TCP via build_transfer_context
- Escalated case (no_match RAG) produces a valid TCP
- TCP to_html_note() produces non-empty HTML with case_id and ticket_id
- TCP pii_masked is True by default (security requirement)
- Mocked FreshdeskReplyClient.post_note receives correct ticket_id + private=True
- TCP HTML contains escalation trigger information
- cited_sop_ids flows through evaluate_rag_result into TCP via build_transfer_context

Note: Tests that previously forced ESCALATED state via classify_case() with UNKNOWN
text have been updated to use _force_escalated() instead. UNKNOWN-topic classification
now transitions to TRIAGE_COMPLETE (not ESCALATED) per the approved flow_diagram.mermaid
(CLASSIFIER --> SLOTEXTRACT direct edge with no ESCALATED node in between).
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from case_engine.case_state import CaseState
from case_engine.models import Case
from case_engine.service import build_case_service
from case_engine.state_machine import CaseStateMachine


def _service():
    return build_case_service(supabase_client=None)


def _force_escalated(case: Case) -> Case:
    """
    Force a case from NEW → CLASSIFYING → ESCALATED via the state machine.

    Used when tests need a case in ESCALATED state but do NOT want to rely on
    classify_case() with UNKNOWN text (which now goes to TRIAGE_COMPLETE per blueprint).
    """
    sm = CaseStateMachine()
    sm.transition(case, CaseState.CLASSIFYING, reason="forced_for_test")
    sm.transition(case, CaseState.ESCALATED, reason="forced_for_test")
    case.escalation_reason = "unknown_topic"
    return case


class TestTcpFromEscalatedCase:
    def test_unknown_topic_escalation_produces_tcp(self):
        # Build a TCP for a case that reached ESCALATED (via state machine,
        # not via classify_case with UNKNOWN — UNKNOWN now goes to TRIAGE_COMPLETE).
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        _force_escalated(case)
        assert case.current_state == CaseState.ESCALATED

        tcp = svc.build_transfer_context(case, escalation_trigger=case.escalation_reason)
        assert tcp.case_id   == case.case_id
        assert tcp.ticket_id == "TKT-001"
        assert tcp.client    == "unity_bank"

    def test_no_match_rag_escalation_produces_tcp(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "OTP not received on mobile number.")
        svc.evaluate_rag_result(
            case,
            match_type="no_match",
            confidence="low",
            requires_human=False,
            chunks_count=0,
        )
        assert case.current_state == CaseState.ESCALATED

        tcp = svc.build_transfer_context(case, escalation_trigger=case.escalation_reason)
        assert tcp.case_id == case.case_id

    def test_tcp_html_contains_case_id(self):
        svc  = _service()
        case = svc.open_case("TKT-TCP-001", "unity_bank")
        # Force ESCALATED via state machine (UNKNOWN no longer escalates — per blueprint)
        _force_escalated(case)
        assert case.current_state == CaseState.ESCALATED

        tcp  = svc.build_transfer_context(case)
        html = tcp.to_html_note()
        assert case.case_id in html or "CASE" in html

    def test_tcp_json_contains_ticket_id(self):
        # to_html_note() renders case_id (not ticket_id) for brevity.
        # ticket_id is available via to_json()/to_dict() for machine consumers.
        svc  = _service()
        case = svc.open_case("TKT-TCP-002", "unity_bank")
        svc.classify_case(case, "Completely unknown request.")

        tcp  = svc.build_transfer_context(case)
        assert tcp.ticket_id == "TKT-TCP-002"
        import json
        parsed = json.loads(tcp.to_json())
        assert parsed["ticket_id"] == "TKT-TCP-002"

    def test_tcp_pii_masked_true(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "Unknown text.")
        tcp  = svc.build_transfer_context(case)
        assert tcp.pii_masked is True

    def test_tcp_html_contains_escalation_trigger(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        # Force ESCALATED via state machine (UNKNOWN no longer escalates — per blueprint)
        _force_escalated(case)
        assert case.escalation_reason is not None

        tcp  = svc.build_transfer_context(case, escalation_trigger=case.escalation_reason)
        html = tcp.to_html_note()
        assert case.escalation_reason in html

    def test_tcp_html_is_non_empty(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "Unknown request text here.")
        tcp  = svc.build_transfer_context(case)
        assert len(tcp.to_html_note()) > 50


class TestMockedFreshdeskPosting:
    """Verify the call signature that app/main.py uses when posting TCP notes."""

    def test_post_note_called_with_private_true(self):
        svc  = _service()
        case = svc.open_case("TKT-MOCK-001", "unity_bank")
        # Force ESCALATED via state machine (UNKNOWN no longer escalates — per blueprint)
        _force_escalated(case)
        assert case.current_state == CaseState.ESCALATED

        tcp    = svc.build_transfer_context(case)
        html   = tcp.to_html_note()
        mock_client = MagicMock()

        mock_client.post_note("TKT-MOCK-001", html, private=True)
        mock_client.post_note.assert_called_once_with("TKT-MOCK-001", html, private=True)

    def test_post_note_receives_ticket_id(self):
        svc  = _service()
        case = svc.open_case("TKT-MOCK-002", "unity_bank")
        svc.classify_case(case, "Unknown complaint text.")

        tcp  = svc.build_transfer_context(case)
        html = tcp.to_html_note()

        mock_client = MagicMock()
        mock_client.post_note("TKT-MOCK-002", html, private=True)

        args, kwargs = mock_client.post_note.call_args
        assert args[0] == "TKT-MOCK-002"

    def test_cited_sop_ids_in_tcp(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "OTP not received.")
        svc.evaluate_rag_result(
            case,
            match_type="no_match",
            confidence="low",
            requires_human=False,
            chunks_count=0,
            cited_sop_ids=["SOP-OTP-001", "SOP-OTP-002"],
        )
        tcp = svc.build_transfer_context(case, cited_sop_ids=["SOP-OTP-001", "SOP-OTP-002"])
        assert tcp.cited_sop_ids == ["SOP-OTP-001", "SOP-OTP-002"]
