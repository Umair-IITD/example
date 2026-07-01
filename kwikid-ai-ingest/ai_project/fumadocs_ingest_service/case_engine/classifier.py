"""
case_engine/classifier.py

Topic classifier — deterministic intake gate.

Architecture (from 03_CASE_STATE_AND_DECISIONING.md):
- Tier 1: keyword/regex rules — < 5ms, ~70% coverage, no external calls
- Tier 2: semantic embedding (STUB for Sprint 1 — returns no-match)
           Full Tier 2 semantic classifier is Sprint 2 work.

If neither tier meets the 0.85 threshold, topic = UNKNOWN → case ESCALATED.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from case_engine.models import ClassificationResult, TopicKey

LOGGER = logging.getLogger(__name__)


# ── Tier 1 rules ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Tier1Rule:
    topic: TopicKey
    pattern: re.Pattern[str]
    confidence: float   # confidence assigned on match


_TIER1_RULES: list[_Tier1Rule] = [
    _Tier1Rule(
        topic=TopicKey.VKYC_SESSION_FAILURE,
        pattern=re.compile(
            r"(vkyc|video[\s\-]?kyc)"
            r".{0,60}"
            r"(link|session|expired|bandwidth|camera|mic|microphone|liveliness|liveness|quality|drop)",
            re.IGNORECASE,
        ),
        confidence=0.95,
    ),
    _Tier1Rule(
        topic=TopicKey.VKYC_SESSION_FAILURE,
        pattern=re.compile(
            r"\b(vkyc|video[\s\-]?kyc)\b",
            re.IGNORECASE,
        ),
        confidence=0.87,
    ),
    _Tier1Rule(
        topic=TopicKey.OTP_DELIVERY_FAILURE,
        pattern=re.compile(
            r"\botp\b"
            r".{0,60}"
            r"(not.{0,10}receiv|invalid|expired|limit|resend|sms|email|voice|channel|block|dnd|deliver)",
            re.IGNORECASE,
        ),
        confidence=0.95,
    ),
    _Tier1Rule(
        topic=TopicKey.OTP_DELIVERY_FAILURE,
        pattern=re.compile(
            r"\b(otp[\s\-]?not[\s\-]?coming|otp[\s\-]?issue|one[\s\-]?time[\s\-]?password)\b",
            re.IGNORECASE,
        ),
        confidence=0.90,
    ),
    _Tier1Rule(
        topic=TopicKey.DOCUMENT_OCR_FAILURE,
        pattern=re.compile(
            r"(pan|aadhaar|aadhar|uidai|xml|ocr|document)"
            r".{0,60}"
            r"(mismatch|fail|error|timeout|invalid|reject|face.?match|quality)",
            re.IGNORECASE,
        ),
        confidence=0.93,
    ),
    _Tier1Rule(
        topic=TopicKey.DOCUMENT_OCR_FAILURE,
        pattern=re.compile(
            r"\b(face[\s\-]?match|liveliness[\s\-]?fail|document[\s\-]?scan|ocr[\s\-]?error)\b",
            re.IGNORECASE,
        ),
        confidence=0.88,
    ),
    _Tier1Rule(
        topic=TopicKey.AGENT_PORTAL_ISSUE,
        pattern=re.compile(
            r"(agent|auditor|supervisor|portal)"
            r".{0,60}"
            r"(login|lock|unlock|access|queue|performance|slow|error|playback)",
            re.IGNORECASE,
        ),
        confidence=0.92,
    ),
    _Tier1Rule(
        topic=TopicKey.API_CALLBACK_FAILURE,
        pattern=re.compile(
            r"(cbs|dms|sfdc|salesforce|callback|webhook|api|integration)"
            r".{0,60}"
            r"(fail|error|timeout|not.{0,5}trigger|missing|drop|retry)",
            re.IGNORECASE,
        ),
        confidence=0.90,
    ),
    _Tier1Rule(
        topic=TopicKey.API_CALLBACK_FAILURE,
        pattern=re.compile(
            r"\b(callback[\s\-]?fail|integration[\s\-]?error|post[\s\-]?kyc[\s\-]?webhook)\b",
            re.IGNORECASE,
        ),
        confidence=0.88,
    ),
]


class TopicClassifier:
    """
    Two-tier topic classifier.

    Tier 1: Deterministic regex rules. < 5ms. No external calls.
    Tier 2: Semantic embedding fallback. (STUB in Sprint 1.)
    """

    def classify(self, text: str) -> ClassificationResult:
        """
        Classify ticket text into a topic.

        Returns ClassificationResult with topic=UNKNOWN if no tier matches.
        """
        LOGGER.warning(
            "ENTER_CLASSIFIER text_len=%d text_preview=%r",
            len(text or ""), (text or "")[:120],
        )

        if not text or not text.strip():
            LOGGER.warning("RETURN_CLASSIFIER_NONE reason=empty_text")
            return ClassificationResult(
                topic=TopicKey.UNKNOWN,
                confidence=0.0,
                tier_used=0,
                raw_text_excerpt="",
            )

        excerpt = text[:500]

        # Tier 1: run all rules, take highest-confidence match
        best_confidence = 0.0
        best_topic: TopicKey | None = None

        for rule in _TIER1_RULES:
            if rule.pattern.search(text):
                if rule.confidence > best_confidence:
                    best_confidence = rule.confidence
                    best_topic = rule.topic

        LOGGER.warning(
            "RETURN_CLASSIFIER_CONFIDENCE tier1_best_confidence=%s tier1_best_topic=%s threshold=0.85",
            best_confidence, best_topic.value if best_topic else None,
        )

        if best_topic is not None and best_confidence >= 0.85:
            LOGGER.warning(
                "RETURN_CLASSIFIER_TOPIC topic=%s confidence=%s tier=1",
                best_topic.value, best_confidence,
            )
            return ClassificationResult(
                topic=best_topic,
                confidence=best_confidence,
                tier_used=1,
                raw_text_excerpt=excerpt,
            )

        # Tier 2: semantic fallback (Sprint 2 implementation)
        tier2_result = self._tier2_classify(text, excerpt)
        if tier2_result is not None:
            LOGGER.warning(
                "RETURN_CLASSIFIER_TOPIC topic=%s confidence=%s tier=2",
                tier2_result.topic.value, tier2_result.confidence,
            )
            return tier2_result

        # Neither tier matched: UNKNOWN
        LOGGER.warning(
            "RETURN_CLASSIFIER_NONE reason=no_tier_matched tier1_best=%s tier1_confidence=%s",
            best_topic.value if best_topic else None, best_confidence,
        )
        return ClassificationResult(
            topic=TopicKey.UNKNOWN,
            confidence=0.0,
            tier_used=0,
            raw_text_excerpt=excerpt,
        )

    def _tier2_classify(self, text: str, excerpt: str) -> ClassificationResult | None:
        """
        Semantic embedding-based classification.

        Sprint 1 stub — always returns None (no match).
        Sprint 2 will implement cosine similarity against representative topic embeddings.
        """
        return None
