"""
case_engine/classifier.py

Topic classifier — Layer 2.5 / Layer 3 boundary.

Sprint 2.5.6: All regex / pattern-matching logic removed.
Classification is now performed by the LLM Semantic Router (NLPRouter).

If the NLPRouter is not configured (no API key, NLP_ROUTER_ENABLED=false),
or if the LLM call fails, the classifier returns TopicKey.UNKNOWN so the
Clarification Engine can ask the bank agent for more context.

Interface is unchanged: TopicClassifier.classify(text) → ClassificationResult.
The NLPSignal produced by the router is stored on ClassificationResult.nlp_signal
so that CaseService.classify_case() can propagate it to the Case object.
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.models import ClassificationResult, TopicKey
from case_engine.nlp_router import NLPRouter, NLPSignal, build_nlp_router

LOGGER = logging.getLogger(__name__)

# Maps LLM router intent names → TopicKey enum values
_INTENT_TO_TOPIC: dict[str, TopicKey] = {
    "OTP_DELIVERY_FAILURE":  TopicKey.OTP_DELIVERY_FAILURE,
    "VKYC_SESSION_FAILURE":  TopicKey.VKYC_SESSION_FAILURE,
    "DOCUMENT_OCR_FAILURE":  TopicKey.DOCUMENT_OCR_FAILURE,
    "AGENT_PORTAL_ISSUE":    TopicKey.AGENT_PORTAL_ISSUE,
    "API_CALLBACK_FAILURE":  TopicKey.API_CALLBACK_FAILURE,
}


class TopicClassifier:
    """
    Topic classifier backed by the LLM Semantic Router.

    Interface:
        classify(text: str) -> ClassificationResult

    Tier 2 only (regex Tier 1 removed per Sprint 2.5.6 blueprint update).
    Returns UNKNOWN when the router is unconfigured or the LLM call fails.
    """

    def __init__(self, nlp_router: NLPRouter | None = None) -> None:
        self._router = nlp_router

    def classify(self, text: str) -> ClassificationResult:
        """
        Classify ticket text into a topic using the LLM Semantic Router.

        Returns ClassificationResult with:
          - topic: matched TopicKey or UNKNOWN
          - confidence: LLM confidence (0.85+ meets threshold)
          - tier_used: 2 (LLM) or 0 (no router / fallback)
          - nlp_signal: full NLPSignal dict for downstream slot extraction
        """
        LOGGER.warning(
            "ENTER_CLASSIFIER text_len=%d router_wired=%s",
            len(text or ""), self._router is not None,
        )

        if not text or not text.strip():
            LOGGER.warning("RETURN_CLASSIFIER_NONE reason=empty_text")
            return ClassificationResult(
                topic=TopicKey.UNKNOWN,
                confidence=0.0,
                tier_used=0,
                raw_text_excerpt="",
                nlp_signal=None,
            )

        if self._router is None:
            LOGGER.warning("RETURN_CLASSIFIER_NONE reason=no_router_configured")
            return ClassificationResult(
                topic=TopicKey.UNKNOWN,
                confidence=0.0,
                tier_used=0,
                raw_text_excerpt=text[:500],
                nlp_signal=None,
            )

        signal: NLPSignal = self._router.route(text)
        topic  = _INTENT_TO_TOPIC.get(signal.intent, TopicKey.UNKNOWN)

        LOGGER.warning(
            "RETURN_CLASSIFIER_TOPIC intent=%s topic=%s confidence=%s tier=2 negation=%s",
            signal.intent, topic.value, signal.confidence, signal.negation_detected,
        )

        return ClassificationResult(
            topic=topic,
            confidence=signal.confidence,
            tier_used=2,
            raw_text_excerpt=text[:500],
            nlp_signal=signal.to_dict(),
        )


def build_topic_classifier(
    nlp_router: NLPRouter | None = None,
) -> TopicClassifier:
    """
    Factory: build a TopicClassifier wired to the LLM Semantic Router.

    If nlp_router is not provided, attempts to build one from environment.
    """
    router = nlp_router or build_nlp_router()
    return TopicClassifier(nlp_router=router)
