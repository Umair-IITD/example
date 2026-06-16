"""
tests/test_sprint215_clarification_engine.py

Sprint 2.15: Tests for ClarificationEngine.
Covers: next_question, accept_slot_value, all_required_filled,
        any_max_attempts_exceeded, extract_from_text, serialization helpers.
"""
import pytest

from case_engine.clarification_engine import ClarificationEngine
from case_engine.models import TopicKey
from case_engine.slot_filling.models import (
    ClarificationQuestion,
    SlotStatus,
    SlotValue,
)


@pytest.fixture
def engine():
    return ClarificationEngine()


def _filled(slot_name: str, value: str = "test") -> SlotValue:
    return SlotValue(slot_name=slot_name, status=SlotStatus.FILLED, value=value)


def _empty(slot_name: str) -> SlotValue:
    return SlotValue(slot_name=slot_name, status=SlotStatus.EMPTY)


def _invalid(slot_name: str, attempts: int = 1) -> SlotValue:
    return SlotValue(slot_name=slot_name, status=SlotStatus.INVALID, attempt_count=attempts)


# ── next_question ─────────────────────────────────────────────────────────────

class TestNextQuestion:
    def test_returns_first_required_slot_when_all_empty(self, engine):
        q = engine.next_question(TopicKey.VKYC_SESSION_FAILURE, {})
        assert isinstance(q, ClarificationQuestion)
        assert q.slot_name == "session_id"
        assert q.is_required is True

    def test_skips_filled_slots(self, engine):
        slots = {"session_id": _filled("session_id", "KID-AB12")}
        q = engine.next_question(TopicKey.VKYC_SESSION_FAILURE, slots)
        assert q is not None
        assert q.slot_name == "phone_number"

    def test_returns_none_when_all_required_filled(self, engine):
        slots = {
            "session_id":   _filled("session_id", "KID-AB12"),
            "phone_number": _filled("phone_number", "1234"),
        }
        q = engine.next_question(TopicKey.VKYC_SESSION_FAILURE, slots)
        assert q is None

    def test_returns_none_for_unknown_topic(self, engine):
        q = engine.next_question(TopicKey.UNKNOWN, {})
        assert q is None

    def test_includes_valid_values_for_enum_slot(self, engine):
        q = engine.next_question(TopicKey.OTP_DELIVERY_FAILURE, {
            "phone_number": _filled("phone_number", "1234"),
        })
        assert q is not None
        assert q.slot_name == "channel"
        assert q.valid_values is not None
        assert "SMS" in q.valid_values

    def test_invalid_slot_still_asked(self, engine):
        slots = {"session_id": _invalid("session_id")}
        q = engine.next_question(TopicKey.VKYC_SESSION_FAILURE, slots)
        assert q is not None
        assert q.slot_name == "session_id"

    def test_all_five_topics_return_question_when_empty(self, engine):
        topics = [
            TopicKey.VKYC_SESSION_FAILURE,
            TopicKey.OTP_DELIVERY_FAILURE,
            TopicKey.DOCUMENT_OCR_FAILURE,
            TopicKey.AGENT_PORTAL_ISSUE,
            TopicKey.API_CALLBACK_FAILURE,
        ]
        for topic in topics:
            q = engine.next_question(topic, {})
            assert q is not None, f"No question for {topic}"


# ── accept_slot_value ─────────────────────────────────────────────────────────

class TestAcceptSlotValue:
    def test_valid_free_form_fills_slot(self, engine):
        sv = engine.accept_slot_value(
            TopicKey.VKYC_SESSION_FAILURE, "session_id", "KID-AB12", {}
        )
        assert sv.status == SlotStatus.FILLED
        assert sv.value == "KID-AB12"

    def test_valid_enum_fills_slot(self, engine):
        sv = engine.accept_slot_value(
            TopicKey.OTP_DELIVERY_FAILURE, "channel", "SMS", {}
        )
        assert sv.status == SlotStatus.FILLED
        assert sv.value == "SMS"

    def test_invalid_enum_marks_invalid(self, engine):
        sv = engine.accept_slot_value(
            TopicKey.OTP_DELIVERY_FAILURE, "channel", "FAX", {}
        )
        assert sv.status == SlotStatus.INVALID
        assert sv.value is None
        assert sv.attempt_count == 1

    def test_attempt_count_preserved_on_valid(self, engine):
        existing = {
            "channel": SlotValue(slot_name="channel", status=SlotStatus.INVALID, attempt_count=1)
        }
        sv = engine.accept_slot_value(
            TopicKey.OTP_DELIVERY_FAILURE, "channel", "EMAIL", existing
        )
        assert sv.status == SlotStatus.FILLED
        assert sv.attempt_count == 1  # preserved from existing

    def test_attempt_count_incremented_on_invalid(self, engine):
        existing = {
            "channel": SlotValue(slot_name="channel", status=SlotStatus.INVALID, attempt_count=1)
        }
        sv = engine.accept_slot_value(
            TopicKey.OTP_DELIVERY_FAILURE, "channel", "FAX", existing
        )
        assert sv.attempt_count == 2

    def test_unknown_slot_name_fills_anyway(self, engine):
        sv = engine.accept_slot_value(
            TopicKey.VKYC_SESSION_FAILURE, "unknown_slot", "any_value", {}
        )
        assert sv.status == SlotStatus.FILLED

    def test_whitespace_stripped_from_value(self, engine):
        sv = engine.accept_slot_value(
            TopicKey.VKYC_SESSION_FAILURE, "session_id", "  KID-AB12  ", {}
        )
        assert sv.value == "KID-AB12"

    def test_does_not_mutate_input_dict(self, engine):
        original = {}
        engine.accept_slot_value(
            TopicKey.VKYC_SESSION_FAILURE, "session_id", "KID-X", original
        )
        assert "session_id" not in original


# ── all_required_filled ───────────────────────────────────────────────────────

class TestAllRequiredFilled:
    def test_false_when_empty(self, engine):
        assert engine.all_required_filled(TopicKey.VKYC_SESSION_FAILURE, {}) is False

    def test_true_when_all_filled(self, engine):
        slots = {
            "session_id":   _filled("session_id", "KID-X"),
            "phone_number": _filled("phone_number", "1234"),
        }
        assert engine.all_required_filled(TopicKey.VKYC_SESSION_FAILURE, slots) is True

    def test_false_when_one_missing(self, engine):
        slots = {"session_id": _filled("session_id", "KID-X")}
        assert engine.all_required_filled(TopicKey.VKYC_SESSION_FAILURE, slots) is False

    def test_false_when_invalid_present(self, engine):
        slots = {
            "session_id":   _filled("session_id", "KID-X"),
            "phone_number": _invalid("phone_number"),
        }
        assert engine.all_required_filled(TopicKey.VKYC_SESSION_FAILURE, slots) is False

    def test_true_when_optional_missing(self, engine):
        slots = {
            "session_id":   _filled("session_id", "KID-X"),
            "phone_number": _filled("phone_number", "1234"),
            # failure_code (optional) not present — should not block
        }
        assert engine.all_required_filled(TopicKey.VKYC_SESSION_FAILURE, slots) is True

    def test_false_for_unknown_topic(self, engine):
        assert engine.all_required_filled(TopicKey.UNKNOWN, {}) is False


# ── any_max_attempts_exceeded ─────────────────────────────────────────────────

class TestAnyMaxAttemptsExceeded:
    def test_false_when_below_limit(self, engine):
        slots = {"session_id": _invalid("session_id", attempts=1)}
        assert engine.any_max_attempts_exceeded(TopicKey.VKYC_SESSION_FAILURE, slots) is False

    def test_true_when_at_limit(self, engine):
        slots = {"session_id": _invalid("session_id", attempts=2)}
        assert engine.any_max_attempts_exceeded(TopicKey.VKYC_SESSION_FAILURE, slots) is True

    def test_true_when_over_limit(self, engine):
        slots = {"session_id": _invalid("session_id", attempts=5)}
        assert engine.any_max_attempts_exceeded(TopicKey.VKYC_SESSION_FAILURE, slots) is True

    def test_false_for_unknown_topic(self, engine):
        slots = {"session_id": _invalid("session_id", attempts=99)}
        assert engine.any_max_attempts_exceeded(TopicKey.UNKNOWN, slots) is False

    def test_false_when_no_slots(self, engine):
        assert engine.any_max_attempts_exceeded(TopicKey.VKYC_SESSION_FAILURE, {}) is False


# ── extract_from_text ─────────────────────────────────────────────────────────

class TestExtractFromText:
    def test_extracts_enum_slot_from_text(self, engine):
        updated = engine.extract_from_text(
            TopicKey.OTP_DELIVERY_FAILURE,
            "The customer is not receiving SMS OTP",
            {},
        )
        assert "channel" in updated
        assert updated["channel"].status == SlotStatus.FILLED
        assert updated["channel"].value == "SMS"

    def test_case_insensitive_extraction(self, engine):
        updated = engine.extract_from_text(
            TopicKey.OTP_DELIVERY_FAILURE,
            "Please retry via email channel",
            {},
        )
        assert updated.get("channel") is not None
        assert updated["channel"].status == SlotStatus.FILLED

    def test_does_not_overwrite_filled_slots(self, engine):
        existing = {"channel": _filled("channel", "VOICE")}
        updated = engine.extract_from_text(
            TopicKey.OTP_DELIVERY_FAILURE,
            "SMS is failing",
            existing,
        )
        assert updated["channel"].value == "VOICE"  # not overwritten

    def test_does_not_extract_free_form_slots(self, engine):
        updated = engine.extract_from_text(
            TopicKey.VKYC_SESSION_FAILURE,
            "KID-AB12CD34 is the session",
            {},
        )
        # session_id has a pattern, not valid_values — not extracted by text scan
        assert "session_id" not in updated or updated.get("session_id", SlotValue("session_id")).status != SlotStatus.FILLED

    def test_no_match_returns_unchanged(self, engine):
        updated = engine.extract_from_text(
            TopicKey.OTP_DELIVERY_FAILURE,
            "unrelated text with no channel mentions",
            {},
        )
        assert "channel" not in updated

    def test_returns_copy_not_original(self, engine):
        original = {}
        updated = engine.extract_from_text(
            TopicKey.OTP_DELIVERY_FAILURE, "SMS", original
        )
        assert original is not updated


# ── Serialization helpers ─────────────────────────────────────────────────────

class TestSerializationHelpers:
    def test_round_trip(self, engine):
        slots = {
            "session_id":   _filled("session_id", "KID-X"),
            "phone_number": _invalid("phone_number", 1),
        }
        raw = ClarificationEngine.slot_values_to_dict(slots)
        restored = ClarificationEngine.slot_values_from_dict(raw)

        assert restored["session_id"].status == SlotStatus.FILLED
        assert restored["session_id"].value == "KID-X"
        assert restored["phone_number"].status == SlotStatus.INVALID
        assert restored["phone_number"].attempt_count == 1

    def test_to_dict_empty(self, engine):
        assert ClarificationEngine.slot_values_to_dict({}) == {}

    def test_from_dict_empty(self, engine):
        assert ClarificationEngine.slot_values_from_dict({}) == {}
