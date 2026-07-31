"""
case_engine/nlp_router.py

Sprint 2.5.6: LLM Semantic Router — Blueprint Layer 2.5 (NLP & Semantic Router).

Replaces the regex-based Tier 1 classifier. Uses OpenAI API with structured
JSON output to:
  1. Detect intent from natural language (handles negation, paraphrase, code-mixing)
  2. Extract investigation entities (URN, Session ID, Agent ID, Application ID)
  3. Determine whether required investigation slots are missing
  4. Return a strict NLPSignal per Blueprint Layer 2.5 contract

Security rules (from CLAUDE.md):
  - NEVER log raw_text — it may contain PII (phone numbers, names, document IDs)
  - Log only intent, confidence, negation flag, and missing slot names
  - Wrap all API calls in try/except — never raises to caller
  - Fail-safe: returns intent=UNKNOWN on any error
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_ONTOLOGY_PATH = Path(__file__).parent.parent / "ontology.json"
_ONTOLOGY_CACHE: dict[str, Any] | None = None

_INTENT_TO_TOPIC_KEY: dict[str, str] = {
    "OTP_DELIVERY_FAILURE":  "OTP_Delivery_Failure",
    "VKYC_SESSION_FAILURE":  "VKYC_Session_Failure",
    "DOCUMENT_OCR_FAILURE":  "Document_OCR_Failure",
    "AGENT_PORTAL_ISSUE":    "Agent_Portal_Issue",
    "API_CALLBACK_FAILURE":  "API_Callback_Failure",
}


def _load_ontology() -> dict[str, Any]:
    global _ONTOLOGY_CACHE
    if _ONTOLOGY_CACHE is None:
        try:
            with open(_ONTOLOGY_PATH, encoding="utf-8") as fh:
                _ONTOLOGY_CACHE = json.load(fh)
            LOGGER.info("nlp_router: ontology loaded intents=%d", len(_ONTOLOGY_CACHE.get("intents", [])))
        except Exception as exc:
            LOGGER.error("nlp_router: ontology load failed path=%s error=%s", _ONTOLOGY_PATH, exc)
            _ONTOLOGY_CACHE = {"intents": [], "version": "error"}
    return _ONTOLOGY_CACHE


# ── NLPSignal — Blueprint Layer 2.5 canonical output ──────────────────────────

@dataclass
class NLPSignal:
    """
    Canonical output of the LLM Semantic Router.

    Consumed by:
      - Layer 3 (TopicClassifier) → mapped to TopicKey
      - Layer 5 (Slot Extractor) → entities pre-fill slots
      - Layer 6 (ClarificationEngine) → needs_clarification triggers question
    """
    intent:                 str                     # canonical intent name or UNKNOWN
    nested_case:            str | None              # sub-case within the intent
    entities:               dict[str, str | None]   # extracted domain entities
    negation_detected:      bool                    # "not receiving", "didn't get", etc.
    confidence:             float                   # 0.0–1.0
    needs_clarification:    bool                    # required investigation slots missing
    clarification_question: str | None             # question to ask if needs_clarification
    raw_text:               str                    # original input (NEVER logged)

    # Derived convenience
    @property
    def topic_key(self) -> str:
        return _INTENT_TO_TOPIC_KEY.get(self.intent, "UNKNOWN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent":                 self.intent,
            "nested_case":            self.nested_case,
            "entities":               self.entities,
            "negation_detected":      self.negation_detected,
            "confidence":             self.confidence,
            "needs_clarification":    self.needs_clarification,
            "clarification_question": self.clarification_question,
            "raw_text":               self.raw_text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], raw_text: str = "") -> "NLPSignal":
        return cls(
            intent=str(d.get("intent") or "UNKNOWN"),
            nested_case=d.get("nested_case"),
            entities=dict(d.get("entities") or {}),
            negation_detected=bool(d.get("negation_detected", False)),
            confidence=float(d.get("confidence") or 0.0),
            needs_clarification=bool(d.get("needs_clarification", True)),
            clarification_question=d.get("clarification_question"),
            raw_text=raw_text,
        )

    @classmethod
    def unknown(cls, raw_text: str = "") -> "NLPSignal":
        return cls(
            intent="UNKNOWN",
            nested_case=None,
            entities={},
            negation_detected=False,
            confidence=0.0,
            needs_clarification=True,
            clarification_question=(
                "Could you please describe your issue in more detail? "
                "For example: are you experiencing a Video KYC failure, OTP not received, "
                "document scan error, agent portal issue, or something else?"
            ),
            raw_text=raw_text,
        )


# ── NLPRouter ─────────────────────────────────────────────────────────────────

class NLPRouter:
    """
    LLM Semantic Router for Blueprint Layer 2.5.

    Uses OpenAI API (structured JSON mode) to parse raw ticket text into
    an NLPSignal. Handles:
      - Natural-language negation ("not receiving", "didn't get", "unable to")
      - Code-mixing ("OTP nahi aa raha")
      - Entity extraction (URN, Session ID, Agent ID, Application ID)
      - Missing-slot detection (sets needs_clarification=True when required
        investigation slots are absent from the text)

    Never raises — all errors return NLPSignal.unknown().
    PII discipline: only intent/confidence/negation/slot-names are logged.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._model   = model or os.environ.get("NLP_ROUTER_MODEL", "gpt-4o-mini")
        self._api_key = (
            api_key
            or os.environ.get("INTELLIGENCE_LLM_API_KEY")
            or os.environ.get("OPENAI_CHAT_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        self._timeout = timeout

    # ── Public API ────────────────────────────────────────────────────────────

    def route(
        self,
        raw_ticket_text: str,
        tenant_context: dict[str, Any] | None = None,
    ) -> NLPSignal:
        """
        Route raw ticket text through the LLM to produce a structured NLPSignal.

        Args:
            raw_ticket_text: Full text of the support ticket (may contain PII).
            tenant_context:  Optional tenant metadata (client name, bank name).

        Returns:
            NLPSignal with intent, entities, negation flag, and clarification info.
            Returns NLPSignal.unknown() on any error (never raises).

        Security: raw_ticket_text is NEVER logged.
        """
        try:
            return self._do_route(raw_ticket_text, tenant_context)
        except Exception as exc:
            LOGGER.error(
                "nlp_router.route failed text_len=%d error_type=%s error=%s",
                len(raw_ticket_text or ""), type(exc).__name__, str(exc)[:200],
            )
            return NLPSignal.unknown(raw_ticket_text)

    # ── Private ───────────────────────────────────────────────────────────────

    def _do_route(
        self,
        raw_text: str,
        tenant_context: dict[str, Any] | None,
    ) -> NLPSignal:
        if not self._api_key:
            LOGGER.warning("nlp_router: no API key configured — returning UNKNOWN")
            return NLPSignal.unknown(raw_text)

        if not raw_text or not raw_text.strip():
            return NLPSignal.unknown(raw_text)

        ontology       = _load_ontology()
        system_prompt  = self._build_system_prompt(ontology)
        user_prompt    = self._build_user_prompt(raw_text, tenant_context)

        from openai import OpenAI  # noqa: PLC0415
        client = OpenAI(api_key=self._api_key, timeout=self._timeout)

        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=600,
            )
        except Exception as exc:
            LOGGER.error("nlp_router: OpenAI call failed model=%s error=%s", self._model, exc)
            return NLPSignal.unknown(raw_text)

        raw_json = (response.choices[0].message.content or "{}").strip()
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            LOGGER.error("nlp_router: JSON decode failed error=%s", exc)
            return NLPSignal.unknown(raw_text)

        signal = self._build_signal(parsed, raw_text, ontology)

        LOGGER.warning(
            "NLP_ROUTER_SIGNAL intent=%s confidence=%s negation=%s needs_clarification=%s missing_slots=%s",
            signal.intent,
            f"{signal.confidence:.2f}",
            signal.negation_detected,
            signal.needs_clarification,
            [k for k, v in signal.entities.items() if not v],
        )
        return signal

    def _build_system_prompt(self, ontology: dict[str, Any]) -> str:
        intents_block = "\n".join(
            f"  - {i['intent_name']}: {i['description']}\n"
            f"    investigation_requires: {', '.join(i['required_slots_for_investigation'])}"
            for i in ontology.get("intents", [])
        )
        return f"""You are a VKYC (Video KYC) support ticket semantic router for KwikID, a fintech platform used by Indian banks.

Your job: analyze a bank agent's support message and classify it into one of the domain intents below.

DOMAIN INTENTS:
{intents_block}

ENTITY TYPES TO EXTRACT:
- urn: Customer's Unique Reference Number (any reference code like URN12345678, or just digits used to identify the customer in the admin portal)
- session_id: VKYC session identifier (format: KID-XXXXXXXX, or similar alphanumeric session code)
- phone_number: Customer's registered phone number or last 4 digits
- agent_id: Bank agent's ID (AGT-XXXX format, or a login username like "AGT001" or numeric ID)
- application_id: Customer's application or onboarding reference ID (e.g., APP-00129871)
- callback_type: Type of API callback (CBS, DMS, SFDC, or WEBHOOK)

KEYWORD → INTENT DISAMBIGUATION (use these as ground truth when in doubt):
- audio / sound / voice / mic / microphone / "can't hear" / "no sound" / "voice not coming" → VKYC_SESSION_FAILURE (nested_case: CAMERA_MIC_ISSUE)
- video / camera / freeze / lag / "dropped call" / disconnect / "screen share" / network / connection / bandwidth / "poor quality" → VKYC_SESSION_FAILURE
- OTP / "one time password" / SMS / "verification code" / "password not received" / "nahi aa raha" → OTP_DELIVERY_FAILURE
- scan / OCR / PAN / Aadhaar / "face match" / document / "blurry" / "mismatch" → DOCUMENT_OCR_FAILURE
- login / portal / "agent console" / "locked out" / "access denied" / queue / dashboard / "not loading" → AGENT_PORTAL_ISSUE
- CBS / DMS / SFDC / webhook / "API callback" / integration / timeout / "not triggered" → API_CALLBACK_FAILURE

CRITICAL RULES (must follow exactly):
1. NEGATION: "not receiving", "didn't get", "haven't gotten", "unable to get", "not coming", "nahi aa raha" ALL indicate a delivery FAILURE, not a success. Set negation_detected=true and classify normally.
2. INVESTIGATION SLOTS: If the text does NOT contain the required investigation identifiers (urn, session_id, etc.), set needs_clarification=true. NEVER ask for phone_number as the primary investigation identifier.
3. UNKNOWN: Only use UNKNOWN if the text genuinely does not match ANY domain intent (e.g., billing questions, branch hours, general inquiries). When in doubt, pick the closest intent with lower confidence rather than UNKNOWN. A technical complaint during VKYC is NEVER UNKNOWN.
4. CONFIDENCE: Set high confidence (>=0.85) when the intent is unambiguous. Use 0.60-0.84 when the text is vague but still classifiable. NEVER set UNKNOWN when keywords from the disambiguation table above appear.
5. ENTITIES: Extract ALL entities present. If not found, set value to null.

RESPONSE FORMAT (strict JSON, no extra text):
{{
  "intent": "<intent_name or UNKNOWN>",
  "nested_case": "<nested case name or null>",
  "entities": {{
    "urn": "<value or null>",
    "session_id": "<value or null>",
    "phone_number": "<value or null>",
    "agent_id": "<value or null>",
    "application_id": "<value or null>",
    "callback_type": "<value or null>"
  }},
  "negation_detected": true/false,
  "confidence": 0.0-1.0,
  "needs_clarification": true/false,
  "clarification_question": "<question to ask the bank agent, or null if not needed>"
}}"""

    def _build_user_prompt(self, raw_text: str, tenant_context: dict | None) -> str:
        tenant_hint = ""
        if isinstance(tenant_context, dict):
            name = tenant_context.get("tenant_name") or tenant_context.get("client_name") or ""
            if name:
                tenant_hint = f"\nBank: {name}"
        return f"Analyze this bank agent support ticket:{tenant_hint}\n\n---\n{raw_text}\n---\n\nReturn valid JSON only."

    def _build_signal(
        self,
        parsed: dict[str, Any],
        raw_text: str,
        ontology: dict[str, Any],
    ) -> NLPSignal:
        intent = str(parsed.get("intent") or "UNKNOWN").strip()

        # Validate against known intents
        known = {i["intent_name"] for i in ontology.get("intents", [])}
        if intent not in known:
            intent = "UNKNOWN"

        entities: dict[str, str | None] = {}
        raw_entities = parsed.get("entities") or {}
        if isinstance(raw_entities, dict):
            for key, val in raw_entities.items():
                entities[key] = str(val).strip() if isinstance(val, str) and val.strip() else None

        # Cross-check: enforce clarification if required investigation slots are absent
        needs_clarification = bool(parsed.get("needs_clarification", True))
        clarification_question = parsed.get("clarification_question")

        if intent != "UNKNOWN":
            intent_def = next(
                (i for i in ontology.get("intents", []) if i["intent_name"] == intent),
                None,
            )
            if intent_def:
                required = intent_def.get("required_slots_for_investigation", [])
                missing  = [s for s in required if not (entities.get(s) or "")]
                if missing:
                    needs_clarification = True
                    if not clarification_question:
                        clarification_question = intent_def.get("clarification_question")
                        if not clarification_question:
                            clarification_question = (
                                f"Please provide the following to investigate: "
                                f"{', '.join(missing)}."
                            )

        return NLPSignal(
            intent=intent,
            nested_case=parsed.get("nested_case"),
            entities=entities,
            negation_detected=bool(parsed.get("negation_detected", False)),
            confidence=min(1.0, max(0.0, float(parsed.get("confidence") or 0.0))),
            needs_clarification=needs_clarification,
            clarification_question=clarification_question if isinstance(clarification_question, str) else None,
            raw_text=raw_text,
        )


# ── Factory ───────────────────────────────────────────────────────────────────

def build_nlp_router(
    model: str | None = None,
    api_key: str | None = None,
) -> NLPRouter | None:
    """
    Factory: return a configured NLPRouter, or None when disabled.

    Set NLP_ROUTER_ENABLED=false in .env to disable (falls back to UNKNOWN classification).
    """
    enabled = os.environ.get("NLP_ROUTER_ENABLED", "true").lower() not in ("false", "0", "no")
    if not enabled:
        LOGGER.info("nlp_router: disabled via NLP_ROUTER_ENABLED=false")
        return None
    return NLPRouter(model=model, api_key=api_key)
