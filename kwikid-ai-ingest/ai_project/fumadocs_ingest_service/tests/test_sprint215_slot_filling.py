"""
tests/test_sprint215_slot_filling.py

Sprint 2.15: Tests for slot filling data models.
Covers SlotStatus, SlotDefinition, SlotValue, ClarificationQuestion.
"""
import re
import pytest

from case_engine.slot_filling.models import (
    ClarificationQuestion,
    SlotDefinition,
    SlotStatus,
    SlotValue,
)


# ── SlotStatus ────────────────────────────────────────────────────────────────

class TestSlotStatus:
    def test_values(self):
        assert SlotStatus.EMPTY   == "EMPTY"
        assert SlotStatus.PENDING == "PENDING"
        assert SlotStatus.FILLED  == "FILLED"
        assert SlotStatus.INVALID == "INVALID"

    def test_is_str_enum(self):
        assert isinstance(SlotStatus.FILLED, str)

    def test_from_string(self):
        assert SlotStatus("FILLED") == SlotStatus.FILLED
        assert SlotStatus("INVALID") == SlotStatus.INVALID

    def test_four_values(self):
        assert len(SlotStatus) == 4


# ── SlotDefinition ────────────────────────────────────────────────────────────

class TestSlotDefinition:
    def _make(self, **kwargs):
        defaults = dict(
            name="session_id",
            description="A session ID",
            clarification_prompt="Please provide your session ID.",
        )
        defaults.update(kwargs)
        return SlotDefinition(**defaults)

    def test_is_frozen(self):
        sd = self._make()
        with pytest.raises((AttributeError, TypeError)):
            sd.name = "changed"  # type: ignore[misc]

    def test_required_default_true(self):
        sd = self._make()
        assert sd.required is True

    def test_max_attempts_default(self):
        sd = self._make()
        assert sd.max_attempts == 2

    def test_valid_value_free_form(self):
        sd = self._make()
        assert sd.is_valid_value("KID-AB12CD34") is True
        assert sd.is_valid_value("") is False
        assert sd.is_valid_value("   ") is False

    def test_valid_value_enum(self):
        sd = self._make(valid_values=frozenset({"SMS", "EMAIL", "VOICE"}))
        assert sd.is_valid_value("SMS") is True
        assert sd.is_valid_value("sms") is True      # case-insensitive
        assert sd.is_valid_value("email") is True
        assert sd.is_valid_value("FAX") is False
        assert sd.is_valid_value("") is False

    def test_valid_value_pattern(self):
        sd = self._make(validation_pattern=re.compile(r"\d{4,10}"))
        assert sd.is_valid_value("1234") is True
        assert sd.is_valid_value("12345678") is True
        assert sd.is_valid_value("123") is False    # too short
        assert sd.is_valid_value("abc") is False
        assert sd.is_valid_value("") is False

    def test_valid_value_whitespace_stripped(self):
        sd = self._make(valid_values=frozenset({"SMS"}))
        assert sd.is_valid_value("  SMS  ") is True

    def test_valid_values_none_allows_anything_non_empty(self):
        sd = self._make(valid_values=None, validation_pattern=None)
        assert sd.is_valid_value("anything") is True
        assert sd.is_valid_value("") is False


# ── SlotValue ─────────────────────────────────────────────────────────────────

class TestSlotValue:
    def test_default_empty(self):
        sv = SlotValue(slot_name="session_id")
        assert sv.status == SlotStatus.EMPTY
        assert sv.value is None
        assert sv.attempt_count == 0

    def test_to_dict(self):
        sv = SlotValue(slot_name="session_id", status=SlotStatus.FILLED, value="KID-X", attempt_count=0)
        d = sv.to_dict()
        assert d["status"] == "FILLED"
        assert d["value"] == "KID-X"
        assert d["attempt_count"] == 0

    def test_from_dict_round_trip(self):
        original = SlotValue(slot_name="channel", status=SlotStatus.INVALID, value=None, attempt_count=2)
        d = original.to_dict()
        restored = SlotValue.from_dict("channel", d)
        assert restored.slot_name == "channel"
        assert restored.status == SlotStatus.INVALID
        assert restored.value is None
        assert restored.attempt_count == 2

    def test_from_dict_defaults_empty(self):
        sv = SlotValue.from_dict("x", {})
        assert sv.status == SlotStatus.EMPTY
        assert sv.attempt_count == 0

    def test_to_dict_missing_value_is_none(self):
        sv = SlotValue(slot_name="x")
        assert sv.to_dict()["value"] is None


# ── ClarificationQuestion ─────────────────────────────────────────────────────

class TestClarificationQuestion:
    def test_is_frozen(self):
        cq = ClarificationQuestion(slot_name="x", prompt_text="p", is_required=True)
        with pytest.raises((AttributeError, TypeError)):
            cq.slot_name = "y"  # type: ignore[misc]

    def test_to_dict_no_valid_values(self):
        cq = ClarificationQuestion(slot_name="session_id", prompt_text="Prompt.", is_required=True)
        d = cq.to_dict()
        assert d["slot_name"] == "session_id"
        assert d["prompt_text"] == "Prompt."
        assert d["is_required"] is True
        assert "valid_values" not in d

    def test_to_dict_with_valid_values(self):
        cq = ClarificationQuestion(
            slot_name="channel",
            prompt_text="Which channel?",
            is_required=True,
            valid_values=("EMAIL", "SMS", "VOICE"),
        )
        d = cq.to_dict()
        assert d["valid_values"] == ["EMAIL", "SMS", "VOICE"]

    def test_optional_slot(self):
        cq = ClarificationQuestion(slot_name="err", prompt_text="Error?", is_required=False)
        d = cq.to_dict()
        assert d["is_required"] is False
