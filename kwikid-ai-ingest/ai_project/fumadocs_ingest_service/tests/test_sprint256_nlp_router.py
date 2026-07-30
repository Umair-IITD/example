"""
tests/test_sprint256_nlp_router.py

Sprint 2.5.6: LLM Semantic Router + Ontology + Clarification Flow tests.

Blueprint §6 (Clarification Engine) and §2 (L1 Responsibilities) verify:
  - System asks for URN / Session ID — NEVER phone number — when classifying OTP issues.
  - NLP router handles negation ("not receiving", "didn't get") correctly.
  - Required investigation slots are urn + session_id, not resolution slots.

Coverage:
  A — NLPSignal and NLPRouter unit tests (dataclass, mocked OpenAI, fail-safe)
  B — Ontology correctness (5 intents, investigation slots, not resolution slots)
  C — Clarification flow (asks for URN/session_id, NOT phone_number)
  D — Pipeline integration (TopicClassifier, slot pre-fill from NLPSignal)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ONTOLOGY_PATH = Path(__file__).parent.parent / "ontology.json"


# ── helpers ────────────────────────────────────────────────────────────────────

def _openai_response(content: str) -> MagicMock:
    """Simulate an OpenAI chat.completions response."""
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


def _router_with_key():
    from case_engine.nlp_router import NLPRouter
    return NLPRouter(api_key="test-key-for-mocking")


def _mock_router(signal_dict: dict):
    from case_engine.nlp_router import NLPRouter, NLPSignal
    router = MagicMock(spec=NLPRouter)
    router.route.return_value = NLPSignal.from_dict(signal_dict)
    return router


# ── Section A: NLPSignal and NLPRouter unit tests ──────────────────────────────

class TestA_NLPSignal:

    def test_A1_unknown_signal_defaults(self):
        from case_engine.nlp_router import NLPSignal
        sig = NLPSignal.unknown("raw text")
        assert sig.intent == "UNKNOWN"
        assert sig.confidence == 0.0
        assert sig.needs_clarification is True
        assert sig.negation_detected is False
        assert sig.entities == {}
        assert sig.clarification_question is not None
        assert sig.raw_text == "raw text"

    def test_A2_from_dict_parses_correctly(self):
        from case_engine.nlp_router import NLPSignal
        d = {
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": "OTP_NOT_RECEIVED",
            "entities": {"urn": "URN123", "session_id": "KID-12345678"},
            "negation_detected": True,
            "confidence": 0.92,
            "needs_clarification": False,
            "clarification_question": None,
        }
        sig = NLPSignal.from_dict(d, raw_text="Customer not receiving OTP")
        assert sig.intent == "OTP_DELIVERY_FAILURE"
        assert sig.nested_case == "OTP_NOT_RECEIVED"
        assert sig.entities["urn"] == "URN123"
        assert sig.entities["session_id"] == "KID-12345678"
        assert sig.negation_detected is True
        assert abs(sig.confidence - 0.92) < 0.001
        assert sig.needs_clarification is False
        assert sig.raw_text == "Customer not receiving OTP"

    def test_A3_to_dict_roundtrip(self):
        from case_engine.nlp_router import NLPSignal
        original = NLPSignal(
            intent="OTP_DELIVERY_FAILURE",
            nested_case="OTP_NOT_RECEIVED",
            entities={"urn": "URN123", "session_id": "KID-12345678"},
            negation_detected=True,
            confidence=0.92,
            needs_clarification=False,
            clarification_question=None,
            raw_text="some text",
        )
        d = original.to_dict()
        restored = NLPSignal.from_dict(d, raw_text=d["raw_text"])
        assert restored.intent == original.intent
        assert abs(restored.confidence - original.confidence) < 0.001
        assert restored.entities == original.entities
        assert restored.negation_detected == original.negation_detected

    def test_A4_topic_key_mapping(self):
        from case_engine.nlp_router import NLPSignal
        cases = [
            ("OTP_DELIVERY_FAILURE",  "OTP_Delivery_Failure"),
            ("VKYC_SESSION_FAILURE",  "VKYC_Session_Failure"),
            ("DOCUMENT_OCR_FAILURE",  "Document_OCR_Failure"),
            ("AGENT_PORTAL_ISSUE",    "Agent_Portal_Issue"),
            ("API_CALLBACK_FAILURE",  "API_Callback_Failure"),
            ("UNKNOWN",               "UNKNOWN"),
        ]
        for intent, expected_key in cases:
            sig = NLPSignal.from_dict({"intent": intent, "confidence": 0.9})
            assert sig.topic_key == expected_key, f"{intent} mapped to {sig.topic_key!r}"

    def test_A5_no_api_key_returns_unknown(self, monkeypatch):
        # Use monkeypatch so env vars are restored after this test (no global pollution)
        for key in ("INTELLIGENCE_LLM_API_KEY", "OPENAI_CHAT_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        from case_engine.nlp_router import NLPRouter
        router = NLPRouter(api_key=None)
        sig = router.route("Customer not receiving OTP")
        assert sig.intent == "UNKNOWN"
        assert sig.needs_clarification is True

    def test_A6_mocked_otp_intent_extraction(self):
        router = _router_with_key()
        llm_reply = json.dumps({
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": "OTP_NOT_RECEIVED",
            "entities": {
                "urn": "URN12345678",
                "session_id": "KID-ABCD1234",
                "phone_number": None,
                "agent_id": None,
                "application_id": None,
                "callback_type": None,
            },
            "negation_detected": True,
            "confidence": 0.95,
            "needs_clarification": False,
            "clarification_question": None,
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("Customer URN12345678 KID-ABCD1234 is not receiving OTP")
        assert sig.intent == "OTP_DELIVERY_FAILURE"
        assert sig.negation_detected is True
        assert sig.entities.get("urn") == "URN12345678"
        assert sig.entities.get("session_id") == "KID-ABCD1234"
        assert sig.needs_clarification is False

    def test_A7_negation_not_receiving(self):
        router = _router_with_key()
        llm_reply = json.dumps({
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": None,
            "entities": {"urn": None, "session_id": None, "phone_number": None,
                         "agent_id": None, "application_id": None, "callback_type": None},
            "negation_detected": True,
            "confidence": 0.88,
            "needs_clarification": True,
            "clarification_question": "Please provide URN and Session ID",
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("Customer is not receiving the OTP")
        assert sig.intent == "OTP_DELIVERY_FAILURE"
        assert sig.negation_detected is True
        assert sig.needs_clarification is True

    def test_A8_negation_didnt_get(self):
        router = _router_with_key()
        llm_reply = json.dumps({
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": None,
            "entities": {"urn": None, "session_id": None, "phone_number": None,
                         "agent_id": None, "application_id": None, "callback_type": None},
            "negation_detected": True,
            "confidence": 0.87,
            "needs_clarification": True,
            "clarification_question": "Please provide URN and Session ID",
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("Customer didn't get the OTP on his mobile")
        assert sig.intent == "OTP_DELIVERY_FAILURE"
        assert sig.negation_detected is True

    def test_A9_entity_extraction_urn_and_session(self):
        router = _router_with_key()
        llm_reply = json.dumps({
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": None,
            "entities": {"urn": "URN99887766", "session_id": "KID-XYZ12345",
                         "phone_number": None, "agent_id": None,
                         "application_id": None, "callback_type": None},
            "negation_detected": True,
            "confidence": 0.93,
            "needs_clarification": False,
            "clarification_question": None,
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("URN99887766 KID-XYZ12345 OTP issue")
        assert sig.entities.get("urn") == "URN99887766"
        assert sig.entities.get("session_id") == "KID-XYZ12345"

    def test_A10_needs_clarification_when_urn_missing(self):
        router = _router_with_key()
        # LLM said needs_clarification=False but URN/session_id are None —
        # the router's cross-check must override this.
        llm_reply = json.dumps({
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": None,
            "entities": {"urn": None, "session_id": None, "phone_number": None,
                         "agent_id": None, "application_id": None, "callback_type": None},
            "negation_detected": True,
            "confidence": 0.85,
            "needs_clarification": False,
            "clarification_question": None,
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("OTP not coming")
        assert sig.needs_clarification is True
        assert sig.clarification_question is not None

    def test_A11_no_clarification_when_all_slots_present(self):
        router = _router_with_key()
        llm_reply = json.dumps({
            "intent": "OTP_DELIVERY_FAILURE",
            "nested_case": None,
            "entities": {"urn": "URN11223344", "session_id": "KID-SESSION9",
                         "phone_number": None, "agent_id": None,
                         "application_id": None, "callback_type": None},
            "negation_detected": True,
            "confidence": 0.95,
            "needs_clarification": False,
            "clarification_question": None,
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("URN11223344 KID-SESSION9 OTP not coming")
        assert sig.needs_clarification is False

    def test_A12_openai_api_failure_returns_unknown(self):
        router = _router_with_key()
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = RuntimeError("Connection error")
            sig = router.route("OTP not received")
        # Must never raise — fail-safe contract
        assert sig is not None
        assert sig.intent == "UNKNOWN"

    def test_A13_empty_text_returns_unknown(self):
        router = _router_with_key()
        sig = router.route("")
        assert sig.intent == "UNKNOWN"

    def test_A13b_whitespace_text_returns_unknown(self):
        router = _router_with_key()
        sig = router.route("   ")
        assert sig.intent == "UNKNOWN"

    def test_A14_invalid_json_response_returns_unknown(self):
        router = _router_with_key()
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response("not-json{")
            sig = router.route("OTP issue")
        assert sig.intent == "UNKNOWN"

    def test_A15_build_nlp_router_disabled(self, monkeypatch):
        monkeypatch.setenv("NLP_ROUTER_ENABLED", "false")
        from case_engine import nlp_router as mod
        result = mod.build_nlp_router()
        assert result is None

    def test_A16_build_nlp_router_enabled(self, monkeypatch):
        monkeypatch.setenv("NLP_ROUTER_ENABLED", "true")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        from case_engine import nlp_router as mod
        from case_engine.nlp_router import NLPRouter
        result = mod.build_nlp_router()
        assert isinstance(result, NLPRouter)

    def test_A17_unknown_intent_in_llm_response_normalised(self):
        router = _router_with_key()
        # LLM returns an invented intent name not in ontology
        llm_reply = json.dumps({
            "intent": "SOME_INVENTED_INTENT",
            "entities": {},
            "negation_detected": False,
            "confidence": 0.5,
            "needs_clarification": False,
        })
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _openai_response(llm_reply)
            sig = router.route("Some random message")
        # Router must normalize unknown intents to UNKNOWN
        assert sig.intent == "UNKNOWN"


# ── Section B: Ontology correctness ────────────────────────────────────────────

class TestB_Ontology:

    @pytest.fixture(scope="class")
    def ontology(self):
        with open(ONTOLOGY_PATH, encoding="utf-8") as fh:
            return json.load(fh)

    def _intent(self, ontology: dict, name: str) -> dict:
        return next((i for i in ontology["intents"] if i["intent_name"] == name), {})

    def test_B1_ontology_file_exists(self):
        assert ONTOLOGY_PATH.exists(), f"ontology.json not found at {ONTOLOGY_PATH}"

    def test_B2_all_five_intents_present(self, ontology):
        names = {i["intent_name"] for i in ontology["intents"]}
        expected = {
            "OTP_DELIVERY_FAILURE",
            "VKYC_SESSION_FAILURE",
            "DOCUMENT_OCR_FAILURE",
            "AGENT_PORTAL_ISSUE",
            "API_CALLBACK_FAILURE",
        }
        assert expected == names

    def test_B3_otp_requires_urn_and_session_id_not_phone(self, ontology):
        intent = self._intent(ontology, "OTP_DELIVERY_FAILURE")
        required = intent["required_slots_for_investigation"]
        assert "urn" in required
        assert "session_id" in required
        # Critical: phone_number is a RESOLUTION slot — must NOT be required for investigation
        assert "phone_number" not in required, (
            "phone_number must not be a required investigation slot for OTP_DELIVERY_FAILURE. "
            "It is a resolution parameter (used only when OTP resend is confirmed as the action)."
        )

    def test_B4_vkyc_requires_urn_and_session_id(self, ontology):
        intent = self._intent(ontology, "VKYC_SESSION_FAILURE")
        required = intent["required_slots_for_investigation"]
        assert "urn" in required
        assert "session_id" in required

    def test_B5_ocr_requires_urn_and_session_id(self, ontology):
        intent = self._intent(ontology, "DOCUMENT_OCR_FAILURE")
        required = intent["required_slots_for_investigation"]
        assert "urn" in required
        assert "session_id" in required

    def test_B6_portal_requires_agent_id_not_urn(self, ontology):
        intent = self._intent(ontology, "AGENT_PORTAL_ISSUE")
        required = intent["required_slots_for_investigation"]
        assert "agent_id" in required
        # Portal issues are about the AGENT account, not the customer's URN
        assert "urn" not in required

    def test_B7_api_callback_requires_application_and_callback_type(self, ontology):
        intent = self._intent(ontology, "API_CALLBACK_FAILURE")
        required = intent["required_slots_for_investigation"]
        assert "application_id" in required
        assert "callback_type" in required

    def test_B8_all_intents_have_clarification_question(self, ontology):
        for intent in ontology["intents"]:
            assert intent.get("clarification_question"), (
                f"{intent['intent_name']} is missing clarification_question"
            )

    def test_B9_ontology_has_version(self, ontology):
        assert "version" in ontology

    def test_B10_phone_number_is_optional_for_otp(self, ontology):
        intent = self._intent(ontology, "OTP_DELIVERY_FAILURE")
        optional = intent.get("optional_slots", [])
        assert "phone_number" in optional

    def test_B11_no_intent_requires_phone_number(self, ontology):
        for intent in ontology["intents"]:
            required = intent.get("required_slots_for_investigation", [])
            assert "phone_number" not in required, (
                f"{intent['intent_name']} must not require phone_number for investigation"
            )

    def test_B12_otp_has_nested_cases(self, ontology):
        intent = self._intent(ontology, "OTP_DELIVERY_FAILURE")
        nested = intent.get("nested_cases", [])
        assert len(nested) >= 2

    def test_B13_all_intents_have_example_phrases(self, ontology):
        for intent in ontology["intents"]:
            examples = intent.get("example_phrases", [])
            assert len(examples) >= 3, f"{intent['intent_name']} needs at least 3 example_phrases"


# ── Section C: Clarification flow tests ────────────────────────────────────────

class TestC_ClarificationFlow:

    def test_C1_asks_for_urn_when_missing(self):
        from case_engine.clarification.engine import WorkflowClarificationEngine
        engine = WorkflowClarificationEngine()
        result = engine.clarify(
            topic="OTP_Delivery_Failure",
            slot_context={},
            required_slots=["urn", "session_id"],
        )
        assert result.status == "NEEDS_CLARIFICATION"
        assert "urn" in result.missing_slots
        # Must mention URN / Session ID, must NOT mention phone number
        msg = result.clarification_message.lower()
        assert "urn" in msg or "session" in msg
        assert "phone" not in msg

    def test_C2_asks_for_session_id_when_urn_present(self):
        from case_engine.clarification.engine import WorkflowClarificationEngine
        engine = WorkflowClarificationEngine()
        result = engine.clarify(
            topic="OTP_Delivery_Failure",
            slot_context={"urn": "URN12345"},
            required_slots=["urn", "session_id"],
        )
        assert result.status == "NEEDS_CLARIFICATION"
        assert "session_id" in result.missing_slots
        msg = result.clarification_message.lower()
        assert "session" in msg or "kid" in msg

    def test_C3_ready_when_both_slots_present(self):
        from case_engine.clarification.engine import WorkflowClarificationEngine
        engine = WorkflowClarificationEngine()
        result = engine.clarify(
            topic="OTP_Delivery_Failure",
            slot_context={"urn": "URN12345", "session_id": "KID-ABCD1234"},
            required_slots=["urn", "session_id"],
        )
        assert result.status == "READY"
        assert result.ready_to_continue is True
        assert result.missing_slots == ()

    def test_C4_phone_number_prompt_is_marked_optional(self):
        from case_engine.clarification.engine import _SLOT_PROMPTS
        phone_prompt = _SLOT_PROMPTS.get("phone_number", "")
        assert phone_prompt, "phone_number must have a slot prompt"
        lowered = phone_prompt.lower()
        assert "optional" in lowered or "after" in lowered, (
            f"phone_number prompt must indicate it is optional/used after investigation. "
            f"Got: {phone_prompt!r}"
        )

    def test_C5_topic_registry_otp_required_slots_are_investigation_identifiers(self):
        from case_engine.topic_registry import get_registry
        from case_engine.models import TopicKey
        registry = get_registry(TopicKey.OTP_DELIVERY_FAILURE)
        assert registry is not None
        required_names = [s.name for s in registry.required]
        assert "urn" in required_names
        assert "session_id" in required_names
        assert "phone_number" not in required_names, (
            "phone_number must not be in required slots for OTP investigation"
        )
        assert "channel" not in required_names, (
            "channel must not be in required slots for OTP investigation"
        )

    def test_C6_topic_registry_otp_phone_number_is_optional(self):
        from case_engine.topic_registry import get_registry
        from case_engine.models import TopicKey
        registry = get_registry(TopicKey.OTP_DELIVERY_FAILURE)
        assert registry is not None
        optional_names = [s.name for s in registry.optional]
        assert "phone_number" in optional_names

    def test_C7_escalate_on_max_attempts_exceeded(self):
        from case_engine.clarification.engine import WorkflowClarificationEngine
        engine = WorkflowClarificationEngine()
        slot_state = {"urn": {"attempt_count": 2, "max_attempts": 2}}
        result = engine.clarify(
            topic="OTP_Delivery_Failure",
            slot_context={},
            required_slots=["urn", "session_id"],
            slot_state=slot_state,
        )
        assert result.status == "ESCALATE"
        assert result.ready_to_continue is False

    def test_C8_vkyc_registry_requires_urn_and_session_id(self):
        from case_engine.topic_registry import get_registry
        from case_engine.models import TopicKey
        registry = get_registry(TopicKey.VKYC_SESSION_FAILURE)
        assert registry is not None
        required_names = [s.name for s in registry.required]
        assert "urn" in required_names
        assert "session_id" in required_names

    def test_C9_agent_portal_registry_requires_agent_id_not_urn(self):
        from case_engine.topic_registry import get_registry
        from case_engine.models import TopicKey
        registry = get_registry(TopicKey.AGENT_PORTAL_ISSUE)
        assert registry is not None
        required_names = [s.name for s in registry.required]
        assert "agent_id" in required_names
        assert "urn" not in required_names

    def test_C10_api_callback_registry_required_slots(self):
        from case_engine.topic_registry import get_registry
        from case_engine.models import TopicKey
        registry = get_registry(TopicKey.API_CALLBACK_FAILURE)
        assert registry is not None
        required_names = [s.name for s in registry.required]
        assert "application_id" in required_names
        assert "callback_type" in required_names

    def test_C11_urn_slot_prompt_does_not_mention_phone(self):
        from case_engine.clarification.engine import _SLOT_PROMPTS
        urn_prompt = _SLOT_PROMPTS.get("urn", "")
        assert "phone" not in urn_prompt.lower(), (
            f"urn prompt must not mention phone number. Got: {urn_prompt!r}"
        )
        assert "URN" in urn_prompt or "Session" in urn_prompt


# ── Section D: Pipeline integration (TopicClassifier + slot pre-fill) ──────────

class TestD_PipelineIntegration:

    def test_D1_no_router_returns_unknown_tier_zero(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        clf = TopicClassifier(nlp_router=None)
        result = clf.classify("OTP not received")
        assert result.topic == TopicKey.UNKNOWN
        assert result.tier_used == 0
        assert result.nlp_signal is None

    def test_D2_otp_intent_maps_to_correct_topic_key(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({
            "intent": "OTP_DELIVERY_FAILURE",
            "entities": {"urn": "URN12345", "session_id": "KID-XY123"},
            "negation_detected": True,
            "confidence": 0.95,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("OTP not received")
        assert result.topic == TopicKey.OTP_DELIVERY_FAILURE
        assert result.tier_used == 2

    def test_D3_nlp_signal_propagated_on_result(self):
        from case_engine.classifier import TopicClassifier
        router = _mock_router({
            "intent": "OTP_DELIVERY_FAILURE",
            "entities": {"urn": "URN12345", "session_id": "KID-XY123"},
            "negation_detected": True,
            "confidence": 0.95,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("OTP not received")
        assert result.nlp_signal is not None
        assert result.nlp_signal["intent"] == "OTP_DELIVERY_FAILURE"
        assert result.nlp_signal["entities"]["urn"] == "URN12345"

    def test_D4_vkyc_intent_maps_to_correct_topic_key(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({
            "intent": "VKYC_SESSION_FAILURE",
            "entities": {"urn": "URN99887766", "session_id": "KID-SESSION1"},
            "negation_detected": False,
            "confidence": 0.91,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("VKYC session failed during liveness check")
        assert result.topic == TopicKey.VKYC_SESSION_FAILURE

    def test_D5_slot_prefill_from_nlp_signal_entities(self):
        from case_engine.runtime.support_agent_runtime import _extract_slots_from_nlp_signal
        from case_engine.models import TopicKey

        class _FakeCase:
            nlp_signal = {
                "intent": "OTP_DELIVERY_FAILURE",
                "entities": {
                    "urn": "URN12345678",
                    "session_id": "KID-ABCD1234",
                    "phone_number": None,
                    "agent_id": None,
                },
                "negation_detected": True,
                "confidence": 0.95,
            }

        slots = _extract_slots_from_nlp_signal(
            _FakeCase(), TopicKey.OTP_DELIVERY_FAILURE, "OTP not received"
        )
        assert slots.get("urn") == "URN12345678"
        assert slots.get("session_id") == "KID-ABCD1234"

    def test_D6_slot_prefill_ignores_none_entity_values(self):
        from case_engine.runtime.support_agent_runtime import _extract_slots_from_nlp_signal
        from case_engine.models import TopicKey

        class _FakeCase:
            nlp_signal = {
                "intent": "OTP_DELIVERY_FAILURE",
                "entities": {
                    "urn": None,
                    "session_id": None,
                    "phone_number": None,
                },
            }

        slots = _extract_slots_from_nlp_signal(
            _FakeCase(), TopicKey.OTP_DELIVERY_FAILURE, "OTP issue"
        )
        # Slots with None values must not be pre-filled
        for slot_name in ("urn", "session_id", "phone_number"):
            assert not slots.get(slot_name), f"{slot_name} should not be pre-filled with None"

    def test_D7_empty_text_returns_unknown_tier_zero(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({"intent": "OTP_DELIVERY_FAILURE", "confidence": 0.9})
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("")
        assert result.topic == TopicKey.UNKNOWN
        assert result.tier_used == 0
        router.route.assert_not_called()

    def test_D8_build_topic_classifier_returns_instance(self, monkeypatch):
        monkeypatch.setenv("NLP_ROUTER_ENABLED", "true")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-factory")
        from case_engine.classifier import build_topic_classifier, TopicClassifier
        clf = build_topic_classifier()
        assert isinstance(clf, TopicClassifier)

    def test_D9_api_callback_intent_maps_correctly(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({
            "intent": "API_CALLBACK_FAILURE",
            "entities": {"application_id": "APP-00129871", "callback_type": "CBS"},
            "negation_detected": False,
            "confidence": 0.90,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("CBS callback not triggered after VKYC")
        assert result.topic == TopicKey.API_CALLBACK_FAILURE

    def test_D10_unknown_intent_maps_to_unknown_topic(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({
            "intent": "UNKNOWN",
            "entities": {},
            "negation_detected": False,
            "confidence": 0.0,
            "needs_clarification": True,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("Something unrelated to VKYC")
        assert result.topic == TopicKey.UNKNOWN

    def test_D11_agent_portal_intent_maps_correctly(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({
            "intent": "AGENT_PORTAL_ISSUE",
            "entities": {"agent_id": "AGT-00123", "session_id": None},
            "negation_detected": False,
            "confidence": 0.88,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("Agent cannot login to portal")
        assert result.topic == TopicKey.AGENT_PORTAL_ISSUE

    def test_D12_document_ocr_intent_maps_correctly(self):
        from case_engine.classifier import TopicClassifier
        from case_engine.models import TopicKey
        router = _mock_router({
            "intent": "DOCUMENT_OCR_FAILURE",
            "entities": {"urn": "URN77665544", "session_id": "KID-DOC12345"},
            "negation_detected": False,
            "confidence": 0.89,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("PAN card OCR failed during document scan")
        assert result.topic == TopicKey.DOCUMENT_OCR_FAILURE

    def test_D13_confidence_propagated_from_nlp_signal(self):
        from case_engine.classifier import TopicClassifier
        router = _mock_router({
            "intent": "OTP_DELIVERY_FAILURE",
            "entities": {"urn": "U1", "session_id": "K1"},
            "negation_detected": False,
            "confidence": 0.93,
            "needs_clarification": False,
        })
        clf = TopicClassifier(nlp_router=router)
        result = clf.classify("OTP issue")
        assert abs(result.confidence - 0.93) < 0.001
