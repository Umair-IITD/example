"""
tests/test_sprint215_topic_registry.py

Sprint 2.15: Tests for topic registry and slot registry.
Covers get_registry, SlotRegistry, and all 5 topic definitions.
"""
import pytest

from case_engine.models import TopicKey
from case_engine.slot_filling.slot_registry import SlotRegistry
from case_engine.topic_registry import get_registry, list_topics


# ── Registry lookup ───────────────────────────────────────────────────────────

class TestGetRegistry:
    def test_all_known_topics_have_registry(self):
        known = [
            TopicKey.VKYC_SESSION_FAILURE,
            TopicKey.OTP_DELIVERY_FAILURE,
            TopicKey.DOCUMENT_OCR_FAILURE,
            TopicKey.AGENT_PORTAL_ISSUE,
            TopicKey.API_CALLBACK_FAILURE,
        ]
        for topic in known:
            assert get_registry(topic) is not None, f"Missing registry for {topic}"

    def test_unknown_returns_none(self):
        assert get_registry(TopicKey.UNKNOWN) is None

    def test_list_topics_has_five(self):
        topics = list_topics()
        assert len(topics) == 5
        assert TopicKey.UNKNOWN not in topics

    def test_returns_slot_registry_instance(self):
        reg = get_registry(TopicKey.VKYC_SESSION_FAILURE)
        assert isinstance(reg, SlotRegistry)


# ── SlotRegistry API ──────────────────────────────────────────────────────────

class TestSlotRegistry:
    @pytest.fixture
    def vkyc(self):
        return get_registry(TopicKey.VKYC_SESSION_FAILURE)

    def test_required_names(self, vkyc):
        names = vkyc.required_names()
        assert "session_id" in names
        assert "phone_number" in names

    def test_optional_names(self, vkyc):
        names = vkyc.optional_names()
        assert "failure_code" in names

    def test_all_definitions(self, vkyc):
        all_defs = vkyc.all_definitions()
        all_names = {d.name for d in all_defs}
        assert "session_id" in all_names
        assert "phone_number" in all_names
        assert "failure_code" in all_names

    def test_definition_for_returns_correct(self, vkyc):
        sd = vkyc.definition_for("session_id")
        assert sd is not None
        assert sd.name == "session_id"

    def test_definition_for_unknown_returns_none(self, vkyc):
        assert vkyc.definition_for("nonexistent_slot") is None

    def test_required_are_frozen(self, vkyc):
        with pytest.raises((AttributeError, TypeError)):
            vkyc.required = ()  # type: ignore[misc]


# ── VKYC_SESSION_FAILURE slots ────────────────────────────────────────────────

class TestVKYCSlots:
    @pytest.fixture
    def reg(self):
        return get_registry(TopicKey.VKYC_SESSION_FAILURE)

    def test_has_two_required(self, reg):
        assert len(reg.required) == 2

    def test_session_id_required(self, reg):
        sd = reg.definition_for("session_id")
        assert sd.required is True

    def test_phone_number_required(self, reg):
        sd = reg.definition_for("phone_number")
        assert sd.required is True

    def test_failure_code_optional(self, reg):
        sd = reg.definition_for("failure_code")
        assert sd.required is False

    def test_session_id_accepts_kid_format(self, reg):
        sd = reg.definition_for("session_id")
        assert sd.is_valid_value("KID-AB12CD34") is True

    def test_phone_number_accepts_digits(self, reg):
        sd = reg.definition_for("phone_number")
        assert sd.is_valid_value("1234") is True
        assert sd.is_valid_value("abc") is False


# ── OTP_DELIVERY_FAILURE slots ────────────────────────────────────────────────

class TestOTPSlots:
    @pytest.fixture
    def reg(self):
        return get_registry(TopicKey.OTP_DELIVERY_FAILURE)

    def test_has_two_required(self, reg):
        assert len(reg.required) == 2

    def test_channel_has_valid_values(self, reg):
        sd = reg.definition_for("channel")
        assert sd.valid_values == frozenset({"SMS", "EMAIL", "VOICE"})

    def test_channel_accepts_sms(self, reg):
        sd = reg.definition_for("channel")
        assert sd.is_valid_value("SMS") is True
        assert sd.is_valid_value("sms") is True

    def test_channel_rejects_fax(self, reg):
        sd = reg.definition_for("channel")
        assert sd.is_valid_value("FAX") is False

    def test_attempt_count_optional(self, reg):
        sd = reg.definition_for("attempt_count")
        assert sd.required is False


# ── DOCUMENT_OCR_FAILURE slots ────────────────────────────────────────────────

class TestDocumentOCRSlots:
    @pytest.fixture
    def reg(self):
        return get_registry(TopicKey.DOCUMENT_OCR_FAILURE)

    def test_document_type_required(self, reg):
        sd = reg.definition_for("document_type")
        assert sd.required is True
        assert sd.is_valid_value("AADHAAR") is True
        assert sd.is_valid_value("PAN") is True
        assert sd.is_valid_value("aadhaar") is True
        assert sd.is_valid_value("UNKNOWN_DOC") is False

    def test_application_id_required(self, reg):
        sd = reg.definition_for("application_id")
        assert sd.required is True
        assert sd.is_valid_value("APP-00129871") is True


# ── AGENT_PORTAL_ISSUE slots ──────────────────────────────────────────────────

class TestAgentPortalSlots:
    @pytest.fixture
    def reg(self):
        return get_registry(TopicKey.AGENT_PORTAL_ISSUE)

    def test_portal_type_required(self, reg):
        sd = reg.definition_for("portal_type")
        assert sd.required is True
        assert sd.is_valid_value("WEB") is True
        assert sd.is_valid_value("MOBILE") is True
        assert sd.is_valid_value("DESKTOP") is True
        assert sd.is_valid_value("API") is False

    def test_agent_id_required(self, reg):
        sd = reg.definition_for("agent_id")
        assert sd.required is True
        assert sd.is_valid_value("AGT-00123") is True


# ── API_CALLBACK_FAILURE slots ────────────────────────────────────────────────

class TestAPICallbackSlots:
    @pytest.fixture
    def reg(self):
        return get_registry(TopicKey.API_CALLBACK_FAILURE)

    def test_callback_type_required(self, reg):
        sd = reg.definition_for("callback_type")
        assert sd.required is True
        assert sd.is_valid_value("CBS") is True
        assert sd.is_valid_value("DMS") is True
        assert sd.is_valid_value("SFDC") is True
        assert sd.is_valid_value("WEBHOOK") is True
        assert sd.is_valid_value("UNKNOWN") is False

    def test_application_id_required(self, reg):
        sd = reg.definition_for("application_id")
        assert sd.required is True
