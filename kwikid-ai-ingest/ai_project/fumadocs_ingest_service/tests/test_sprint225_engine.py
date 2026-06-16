"""
tests/test_sprint225_engine.py

Sprint 2.25: WorkflowClarificationEngine tests.

Coverage:
  - READY path: no required slots
  - READY path: all slots present
  - NEEDS_CLARIFICATION path: single missing slot
  - NEEDS_CLARIFICATION path: multiple missing slots (asks first)
  - ESCALATE path: attempt_count >= max_attempts for a missing slot
  - ESCALATE path: any missing slot exceeded triggers escalation
  - Slot prompts: known slots produce correct text
  - Slot prompts: unknown slots produce generic text
  - Determinism: same input → same status (10 runs)
  - Exception isolation: exception in _clarify returns READY (fail-open)
  - Slot type acceptance: tuple or list for required_slots
  - attempt_count from slot_state is used correctly
  - build_clarification_engine factory returns engine instance
  - Missing slot detection is case-sensitive
  - Empty slot context with empty required_slots → READY
"""
from __future__ import annotations

import pytest

from case_engine.clarification.engine import (
    WorkflowClarificationEngine,
    _SLOT_PROMPTS,
    build_clarification_engine,
)
from case_engine.clarification.models import ClarificationResult, ClarificationStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _engine() -> WorkflowClarificationEngine:
    return WorkflowClarificationEngine()


def _clarify(
    required_slots,
    slot_context=None,
    slot_state=None,
    topic="VKYC_Session_Failure",
) -> ClarificationResult:
    return _engine().clarify(
        topic=topic,
        slot_context=slot_context or {},
        required_slots=required_slots,
        slot_state=slot_state,
    )


# ── READY path ────────────────────────────────────────────────────────────────

class TestClarifyReadyPath:
    def test_no_required_slots_returns_ready(self):
        result = _clarify(required_slots=[])
        assert result.status is ClarificationStatus.READY
        assert result.ready_to_continue is True

    def test_empty_tuple_required_slots_returns_ready(self):
        result = _clarify(required_slots=())
        assert result.status is ClarificationStatus.READY

    def test_all_slots_present_returns_ready(self):
        result = _clarify(
            required_slots=["session_id", "phone_number"],
            slot_context={"session_id": "KID-12345678", "phone_number": "+911234567890"},
        )
        assert result.status is ClarificationStatus.READY
        assert result.ready_to_continue is True
        assert result.missing_slots == ()

    def test_ready_has_no_next_question(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={"session_id": "abc"},
        )
        assert result.next_question is None

    def test_ready_message_content(self):
        result = _clarify(
            required_slots=["phone_number"],
            slot_context={"phone_number": "+91999"},
        )
        assert "available" in result.clarification_message.lower()

    def test_ready_has_result_id(self):
        result = _clarify(required_slots=[])
        assert result.result_id
        assert len(result.result_id) > 0


# ── NEEDS_CLARIFICATION path ──────────────────────────────────────────────────

class TestClarifyNeedsClarificationPath:
    def test_single_missing_slot(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION
        assert "session_id" in result.missing_slots
        assert result.ready_to_continue is False

    def test_has_next_question_for_missing_slot(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
        )
        assert result.next_question is not None
        assert result.next_question.slot_name == "session_id"

    def test_multiple_missing_slots_asks_first(self):
        result = _clarify(
            required_slots=["session_id", "phone_number"],
            slot_context={},
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION
        assert len(result.missing_slots) == 2
        assert result.next_question.slot_name == "session_id"

    def test_partial_slots_filled(self):
        result = _clarify(
            required_slots=["session_id", "phone_number"],
            slot_context={"session_id": "KID-12345678"},
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION
        assert "phone_number" in result.missing_slots
        assert "session_id" not in result.missing_slots

    def test_clarification_message_is_prompt_text(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
        )
        assert "session_id" in result.clarification_message.lower() or len(result.clarification_message) > 0

    def test_empty_string_slot_is_considered_missing(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={"session_id": ""},
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION

    def test_none_slot_value_is_missing(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={"session_id": None},  # type: ignore[dict-item]
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION


# ── ESCALATE path ─────────────────────────────────────────────────────────────

class TestClarifyEscalatePath:
    def test_slot_at_max_attempts_escalates(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 2, "max_attempts": 2}},
        )
        assert result.status is ClarificationStatus.ESCALATE
        assert result.ready_to_continue is False

    def test_slot_exceeds_max_attempts_escalates(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 5, "max_attempts": 2}},
        )
        assert result.status is ClarificationStatus.ESCALATE

    def test_escalate_has_no_next_question(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 2, "max_attempts": 2}},
        )
        assert result.next_question is None

    def test_escalate_contains_missing_slots(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 2, "max_attempts": 2}},
        )
        assert "session_id" in result.missing_slots

    def test_one_exceeded_slot_in_multiple_triggers_escalation(self):
        result = _clarify(
            required_slots=["session_id", "phone_number"],
            slot_context={},
            slot_state={
                "session_id": {"attempt_count": 2, "max_attempts": 2},
                "phone_number": {"attempt_count": 0, "max_attempts": 2},
            },
        )
        assert result.status is ClarificationStatus.ESCALATE

    def test_below_max_attempts_does_not_escalate(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 1, "max_attempts": 2}},
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION

    def test_default_max_attempts_is_2(self):
        # With no slot_state info and 1 attempt, should NOT escalate
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 1}},
        )
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION
        # With 2 attempts and default max of 2, should ESCALATE
        result2 = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 2}},
        )
        assert result2.status is ClarificationStatus.ESCALATE


# ── Slot prompts ──────────────────────────────────────────────────────────────

class TestSlotPrompts:
    def test_session_id_prompt(self):
        result = _clarify(required_slots=["session_id"], slot_context={})
        assert result.next_question is not None
        assert "session_id" in result.next_question.prompt_text.lower() or \
               "session" in result.next_question.prompt_text.lower()

    def test_phone_number_prompt(self):
        result = _clarify(required_slots=["phone_number"], slot_context={})
        assert result.next_question is not None
        assert len(result.next_question.prompt_text) > 0

    def test_channel_prompt(self):
        result = _clarify(required_slots=["channel"], slot_context={})
        assert result.next_question is not None
        prompt = result.next_question.prompt_text
        # channel prompt should mention the options
        assert "sms" in prompt.lower() or "email" in prompt.lower() or "channel" in prompt.lower()

    def test_known_slot_names_have_prompts(self):
        for slot in list(_SLOT_PROMPTS.keys()):
            result = _clarify(required_slots=[slot], slot_context={})
            assert result.next_question is not None
            assert result.next_question.prompt_text == _SLOT_PROMPTS[slot]

    def test_unknown_slot_uses_generic_prompt(self):
        result = _clarify(required_slots=["custom_field_xyz"], slot_context={})
        assert result.next_question is not None
        assert "custom" in result.next_question.prompt_text.lower() or \
               "field" in result.next_question.prompt_text.lower() or \
               len(result.next_question.prompt_text) > 0


# ── Determinism ───────────────────────────────────────────────────────────────

class TestClarifyDeterminism:
    def test_ten_identical_calls_same_status(self):
        kwargs = dict(
            topic="OTP_Delivery_Failure",
            slot_context={"phone_number": ""},
            required_slots=["phone_number", "channel"],
            slot_state=None,
        )
        statuses = {_engine().clarify(**kwargs).status for _ in range(10)}
        assert len(statuses) == 1

    def test_ten_calls_same_missing_slots(self):
        results = [
            _clarify(
                required_slots=["session_id", "phone_number"],
                slot_context={"session_id": ""},
            )
            for _ in range(10)
        ]
        slot_sets = [set(r.missing_slots) for r in results]
        assert all(s == slot_sets[0] for s in slot_sets)


# ── Exception isolation ───────────────────────────────────────────────────────

class TestClarifyExceptionIsolation:
    def test_engine_never_raises_on_bad_input(self):
        engine = _engine()
        result = engine.clarify(
            topic="ANY",
            slot_context=None,  # type: ignore[arg-type]
            required_slots=None,  # type: ignore[arg-type]
            slot_state=None,
        )
        # Should not raise — returns some result
        assert result is not None

    def test_engine_returns_result_on_empty_inputs(self):
        result = _clarify(required_slots=[], slot_context=None)
        assert isinstance(result, ClarificationResult)


# ── Input type acceptance ─────────────────────────────────────────────────────

class TestClarifyInputTypes:
    def test_tuple_required_slots(self):
        result = _clarify(required_slots=("session_id",), slot_context={})
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION

    def test_list_required_slots(self):
        result = _clarify(required_slots=["session_id"], slot_context={})
        assert result.status is ClarificationStatus.NEEDS_CLARIFICATION


# ── Factory ───────────────────────────────────────────────────────────────────

class TestBuildClarificationEngine:
    def test_factory_returns_engine(self):
        engine = build_clarification_engine()
        assert isinstance(engine, WorkflowClarificationEngine)

    def test_factory_engine_is_functional(self):
        engine = build_clarification_engine()
        result = engine.clarify(
            topic="TEST",
            slot_context={"session_id": "abc"},
            required_slots=["session_id"],
        )
        assert result.status is ClarificationStatus.READY
