"""
tests/test_sprint215_case_service_slot.py

Sprint 2.15: Tests for CaseService slot-filling methods.
Covers receive_message and get_slot_state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from case_engine.case_state import CaseState
from case_engine.models import Case, TopicKey
from case_engine.service import CaseService, ReceiveMessageResult
from case_engine.slot_filling.models import SlotStatus, SlotValue


# ── Test infrastructure ───────────────────────────────────────────────────────

@dataclass
class _FakeRepo:
    _updated: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        self._updated = []

    def get_case(self, case_id: str):
        return None

    def create_case(self, ticket_id: str, client: str):
        return Case(ticket_id=ticket_id, client=client)

    def get_case_by_ticket(self, ticket_id: str, client: str):
        return None

    def update_case_state(self, case: Case, state: CaseState) -> bool:
        self._updated.append(state.value)
        return True

    def record_transition(self, transition: Any) -> bool:
        return True

    def write_audit_entry(self, entry: Any) -> bool:
        return True


class _FakeAudit:
    def log_transition(self, *a, **k): pass
    def log_classification(self, *a, **k): pass
    def log_rag_call(self, *a, **k): pass
    def log_note_posted(self, *a, **k): pass
    def log_escalation(self, *a, **k): pass
    def log_error(self, *a, **k): pass
    def log_slot_filled(self, *a, **k): pass
    def log_action_proposed(self, *a, **k): pass
    def log_action_executed(self, *a, **k): pass
    def log_action_rejected(self, *a, **k): pass
    def log_security_event(self, *a, **k): pass


def _make_service() -> tuple[CaseService, _FakeRepo]:
    repo = _FakeRepo()
    svc = CaseService(repository=repo, audit_logger=_FakeAudit())  # type: ignore[arg-type]
    return svc, repo


def _make_case(topic: str | None = TopicKey.VKYC_SESSION_FAILURE.value, state: CaseState = CaseState.TRIAGE_COMPLETE) -> Case:
    c = Case(ticket_id="TKT-001", client="bank_alpha")
    c.topic = topic
    c.current_state = state
    return c


# ── get_slot_state ─────────────────────────────────────────────────────────────

class TestGetSlotState:
    def test_empty_state_returns_empty_dict(self):
        svc, _ = _make_service()
        case = _make_case()
        assert svc.get_slot_state(case) == {}

    def test_deserializes_existing_slot_state(self):
        svc, _ = _make_service()
        case = _make_case()
        case.slot_state = {
            "session_id": {"status": "FILLED", "value": "KID-X", "attempt_count": 0}
        }
        slots = svc.get_slot_state(case)
        assert slots["session_id"].status == SlotStatus.FILLED
        assert slots["session_id"].value == "KID-X"


# ── receive_message: unknown topic ────────────────────────────────────────────

class TestReceiveMessageUnknownTopic:
    def test_unknown_topic_returns_no_question(self):
        svc, _ = _make_service()
        case = _make_case(topic=None)
        result = svc.receive_message(case, "my VKYC failed")
        assert result.next_question is None
        assert result.state == CaseState.TRIAGE_COMPLETE

    def test_unknown_topic_key_string(self):
        svc, _ = _make_service()
        case = _make_case(topic="UNKNOWN")
        result = svc.receive_message(case, "anything")
        assert result.next_question is None


# ── receive_message: text extraction ──────────────────────────────────────────

class TestReceiveMessageExtraction:
    def test_extracts_enum_slot_from_text(self):
        svc, repo = _make_service()
        case = _make_case(topic=TopicKey.OTP_DELIVERY_FAILURE.value)
        result = svc.receive_message(case, "The SMS OTP is not working for my number")

        assert result.slot_values.get("channel", {}).get("status") == "FILLED"
        assert result.next_question is not None
        assert result.next_question["slot_name"] == "phone_number"

    def test_first_question_returned_when_nothing_extracted(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value)
        result = svc.receive_message(case, "VKYC not working")
        # No enum slots in VKYC — extraction won't fill session_id
        assert result.next_question is not None
        assert result.next_question["slot_name"] == "session_id"


# ── receive_message: explicit slot filling ────────────────────────────────────

class TestReceiveMessageExplicit:
    def test_fills_explicit_slot_and_asks_next(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value)
        result = svc.receive_message(
            case, "", slot_name="session_id", slot_value_str="KID-AB12CD34"
        )
        assert result.slot_values["session_id"]["status"] == "FILLED"
        assert result.next_question is not None
        assert result.next_question["slot_name"] == "phone_number"

    def test_invalid_slot_value_marks_invalid(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.OTP_DELIVERY_FAILURE.value)
        result = svc.receive_message(
            case, "", slot_name="channel", slot_value_str="FAX"
        )
        assert result.slot_values["channel"]["status"] == "INVALID"
        assert result.next_question is not None
        assert result.next_question["slot_name"] == "phone_number"  # first required is phone_number

    def test_all_slots_filled_sets_flag(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value)
        case.slot_state = {
            "session_id": {"status": "FILLED", "value": "KID-X", "attempt_count": 0}
        }
        result = svc.receive_message(
            case, "", slot_name="phone_number", slot_value_str="5678"
        )
        assert result.all_slots_filled is True
        assert result.next_question is None
        assert result.state == CaseState.WORKFLOW_ACTIVE

    def test_all_slots_filled_transitions_to_workflow_active(self):
        svc, repo = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value)
        case.slot_state = {
            "session_id": {"status": "FILLED", "value": "KID-X", "attempt_count": 0}
        }
        svc.receive_message(case, "", slot_name="phone_number", slot_value_str="5678")
        assert CaseState.WORKFLOW_ACTIVE.value in repo._updated


# ── receive_message: max attempts exceeded ────────────────────────────────────

class TestReceiveMessageEscalation:
    def test_max_attempts_exceeded_escalates(self):
        svc, repo = _make_service()
        case = _make_case(topic=TopicKey.OTP_DELIVERY_FAILURE.value)
        # Simulate 2 failed attempts on phone_number (max_attempts=2)
        case.slot_state = {
            "phone_number": {"status": "INVALID", "value": None, "attempt_count": 2}
        }
        result = svc.receive_message(case, "", slot_name="phone_number", slot_value_str="bad")
        assert result.escalated is True
        assert CaseState.ESCALATED.value in repo._updated

    def test_max_attempts_not_exceeded_does_not_escalate(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.OTP_DELIVERY_FAILURE.value)
        # attempt_count=0: one invalid submission → count=1, below max_attempts=2
        case.slot_state = {
            "phone_number": {"status": "INVALID", "value": None, "attempt_count": 0}
        }
        result = svc.receive_message(case, "", slot_name="phone_number", slot_value_str="bad")
        assert result.escalated is False


# ── receive_message: state transitions ────────────────────────────────────────

class TestReceiveMessageStateTransitions:
    def test_transitions_to_awaiting_input_when_slots_missing(self):
        svc, repo = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value, state=CaseState.TRIAGE_COMPLETE)
        svc.receive_message(case, "help")
        assert CaseState.AWAITING_INPUT.value in repo._updated

    def test_already_in_awaiting_input_stays(self):
        svc, repo = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value, state=CaseState.AWAITING_INPUT)
        svc.receive_message(case, "help")
        # Should not try to transition away since already there
        assert all(s != CaseState.ESCALATED.value for s in repo._updated)

    def test_slot_state_updated_on_case(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value)
        svc.receive_message(case, "", slot_name="session_id", slot_value_str="KID-NEW")
        assert case.slot_state["session_id"]["status"] == "FILLED"


# ── ReceiveMessageResult ──────────────────────────────────────────────────────

class TestReceiveMessageResult:
    def test_result_fields(self):
        svc, _ = _make_service()
        case = _make_case(topic=TopicKey.VKYC_SESSION_FAILURE.value)
        result = svc.receive_message(case, "")
        assert isinstance(result, ReceiveMessageResult)
        assert result.case_id == case.case_id
        assert isinstance(result.slot_values, dict)
        assert isinstance(result.all_slots_filled, bool)
        assert isinstance(result.escalated, bool)
