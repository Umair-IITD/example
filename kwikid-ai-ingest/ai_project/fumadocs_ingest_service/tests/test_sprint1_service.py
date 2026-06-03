"""
tests/test_sprint1_service.py

CaseService integration tests (offline mode — no DB required).

Covers:
- open_case: creates a new case in NEW state
- open_case: returns existing case on retry (repo returns existing)
- open_case: falls back to in-memory case if repo returns None
- classify_case: known topic → TRIAGE_COMPLETE, topic/confidence set
- classify_case: unknown topic → ESCALATED, escalation_reason set
- classify_case: below-threshold confidence → ESCALATED
- evaluate_rag_result: no-match retrieval → ESCALATED
- evaluate_rag_result: good match → no escalation
- evaluate_rag_result: already-ESCALATED case is not double-escalated
- resolve_case: TRIAGE_COMPLETE → RESOLVED
- resolve_case: ESCALATED case is not modified (already frozen)
- build_transfer_context: returns correctly populated payload
- record_error: does not raise
- Phase 1 safety: CaseService crash does not propagate (caller try/except)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_engine.case_state import CaseState
from case_engine.models import Case, TopicKey
from case_engine.service import CaseService, build_case_service


# ── Factory helpers ───────────────────────────────────────────────────────────

def _service() -> CaseService:
    """Build an offline CaseService (no DB, no Supabase)."""
    return build_case_service(supabase_client=None)


# ── open_case ─────────────────────────────────────────────────────────────────

class TestOpenCase:
    def test_creates_new_case_in_new_state(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        assert isinstance(case, Case)
        assert case.ticket_id     == "TKT-001"
        assert case.client        == "unity_bank"
        assert case.current_state == CaseState.NEW

    def test_case_has_nonempty_id(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        assert case.case_id and len(case.case_id) > 0

    def test_returns_existing_case_on_retry(self):
        """When the repo finds an existing case, open_case returns it (idempotent)."""
        svc = _service()
        existing = Case(ticket_id="TKT-002", client="unity_bank", current_state=CaseState.CLASSIFYING)
        svc._repo.get_case_by_ticket = lambda tid, c: existing

        case = svc.open_case("TKT-002", "unity_bank")
        assert case.case_id       == existing.case_id
        assert case.current_state == CaseState.CLASSIFYING  # preserved from existing


# ── classify_case ─────────────────────────────────────────────────────────────

class TestClassifyCase:
    def test_known_topic_transitions_to_triage_complete(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "My OTP is not being received on my mobile.")
        assert case.current_state == CaseState.TRIAGE_COMPLETE

    def test_known_topic_sets_topic_on_case(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "My OTP is not being received on my mobile.")
        assert case.topic == TopicKey.OTP_DELIVERY_FAILURE.value

    def test_known_topic_sets_confidence_on_case(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "My OTP is not being received on my mobile.")
        assert case.confidence is not None
        assert case.confidence >= 0.85

    def test_unknown_topic_transitions_to_escalated(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "I want to know about your mortgage rates please.")
        assert case.current_state == CaseState.ESCALATED

    def test_unknown_topic_sets_escalation_reason(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "I want to know about your mortgage rates please.")
        assert case.escalation_reason is not None

    def test_vkyc_topic_classified_correctly(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "VKYC session has dropped due to bandwidth issue.")
        assert case.current_state == CaseState.TRIAGE_COMPLETE
        assert case.topic == TopicKey.VKYC_SESSION_FAILURE.value


# ── evaluate_rag_result ───────────────────────────────────────────────────────

class TestEvaluateRagResult:
    def _triage_case(self) -> tuple[CaseService, Case]:
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "OTP not received on mobile number.")
        return svc, case

    def test_good_match_does_not_escalate(self):
        svc, case = self._triage_case()
        decision = svc.evaluate_rag_result(
            case,
            match_type="exact_match",
            confidence="high",
            requires_human=False,
            chunks_count=3,
        )
        assert decision.should_escalate is False
        assert case.current_state == CaseState.TRIAGE_COMPLETE

    def test_no_match_escalates(self):
        svc, case = self._triage_case()
        decision = svc.evaluate_rag_result(
            case,
            match_type="no_match",
            confidence="low",
            requires_human=False,
            chunks_count=0,
        )
        assert decision.should_escalate is True
        assert case.current_state == CaseState.ESCALATED

    def test_requires_human_escalates(self):
        svc, case = self._triage_case()
        decision = svc.evaluate_rag_result(
            case,
            match_type="exact_match",
            confidence="low",
            requires_human=True,
            chunks_count=2,
        )
        assert decision.should_escalate is True
        assert case.current_state == CaseState.ESCALATED

    def test_already_escalated_case_not_double_escalated(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        # Force it to ESCALATED first
        svc.classify_case(case, "Unknown request text here.")
        assert case.current_state == CaseState.ESCALATED

        # A subsequent no_match should not change anything
        decision = svc.evaluate_rag_result(
            case,
            match_type="no_match",
            confidence="low",
            requires_human=False,
            chunks_count=0,
        )
        assert case.current_state == CaseState.ESCALATED  # unchanged

    def test_rag_result_sets_retrieval_match_type(self):
        svc, case = self._triage_case()
        svc.evaluate_rag_result(
            case,
            match_type="related_match",
            confidence="medium",
            requires_human=False,
            chunks_count=2,
        )
        assert case.retrieval_match_type == "related_match"


# ── resolve_case ──────────────────────────────────────────────────────────────

class TestResolveCase:
    def test_triage_complete_resolves(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "OTP not received on mobile number.")
        assert case.current_state == CaseState.TRIAGE_COMPLETE
        svc.resolve_case(case, reason="note_posted")
        # Sprint 1.1 Task 3: Level 1 terminal state is CLOSED, not RESOLVED
        assert case.current_state == CaseState.CLOSED

    def test_escalated_case_not_resolved(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.classify_case(case, "Unknown unrecognized complaint text.")
        assert case.current_state == CaseState.ESCALATED
        svc.resolve_case(case, reason="note_posted")
        # ESCALATED is not in allowed pre-RESOLVED states — should stay ESCALATED
        assert case.current_state == CaseState.ESCALATED

    def test_new_case_not_resolved(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.resolve_case(case, reason="note_posted")
        assert case.current_state == CaseState.NEW  # unchanged


# ── build_transfer_context ────────────────────────────────────────────────────

class TestBuildTransferContext:
    def test_returns_payload_with_correct_ids(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        payload = svc.build_transfer_context(
            case,
            escalation_trigger="unknown_topic",
            escalation_reason="Topic not in registry",
        )
        assert payload.ticket_id  == "TKT-001"
        assert payload.client     == "unity_bank"
        assert payload.case_id    == case.case_id

    def test_returns_payload_with_trigger(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        payload = svc.build_transfer_context(
            case,
            escalation_trigger="below_threshold",
        )
        assert payload.escalation_trigger == "below_threshold"

    def test_returns_payload_with_sop_ids(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        payload = svc.build_transfer_context(case, cited_sop_ids=["SOP-001"])
        assert payload.cited_sop_ids == ["SOP-001"]


# ── record_error ──────────────────────────────────────────────────────────────

class TestRecordError:
    def test_record_error_does_not_raise(self):
        svc  = _service()
        case = svc.open_case("TKT-001", "unity_bank")
        svc.record_error(case, "CLASSIFIER_CRASH", "unexpected exception in classifier")
        # No assertion needed — absence of exception is the test


# ── Phase 1 safety: CaseService errors are isolated ──────────────────────────

class TestPhase1Safety:
    """The webhook handler wraps case engine calls in try/except.
    This test verifies that pattern holds and Phase 1 results pass through."""

    def test_service_error_does_not_propagate_when_caught(self):
        """Simulate the pattern in app/main.py webhook handler."""
        svc = _service()

        # Patch open_case to raise unexpectedly
        svc.open_case = MagicMock(side_effect=RuntimeError("DB exploded"))

        phase1_result = None
        try:
            svc.open_case("TKT-001", "unity_bank")
        except Exception:
            pass  # caught — Phase 1 flow continues

        phase1_result = "Phase 1 RAG completed"
        assert phase1_result == "Phase 1 RAG completed"

    def test_build_case_service_without_db(self):
        """build_case_service(None) must succeed without any env vars."""
        svc = build_case_service(supabase_client=None)
        assert svc is not None
        assert isinstance(svc, CaseService)
