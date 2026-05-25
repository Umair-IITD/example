"""
query_router/classifier.py

Deterministic keyword-pattern query classifier for KwikID support queries.

Architecture:
  1. Spam detection (no network call, immediate reject)
  2. Length check (very short → conversational, no retrieval needed)
  3. Deterministic pattern matching (always runs, O(n*m) where n=patterns, m=query_len)
     - Per-category weighted keyword patterns (KwikID domain vocabulary)
     - Returns category + confidence score
  4. LLM enrichment (optional, async, opt-in via QUERY_ROUTER_LLM_ENABLED=true)
     - Only fires when deterministic confidence < QUERY_ROUTER_LLM_THRESHOLD
     - Hard 5s timeout; falls back to deterministic result on failure

The deterministic path makes NO network calls — safe for high-concurrency sync contexts.
The classifier.classify() method is synchronous and always safe to call.

Routing influence (via RoutingDecision.from_classification):
  - ESCALATION:     force_escalation=True, human review mandatory
  - SPAM/CONVERSATIONAL: skip_retrieval=True, no DB call
  - KYC/ONBOARDING/SOP: prioritize_sop=True, SOP chunks ranked higher
  - All:            per-category top_k and threshold overrides
"""
from __future__ import annotations

import logging
import re
from typing import Any

from query_router.taxonomy import IssueCategory, CATEGORY_DESCRIPTIONS
from query_router.models import ClassificationResult, RoutingDecision

LOGGER = logging.getLogger(__name__)


# ── KwikID-domain keyword patterns ────────────────────────────────────────────
# Each entry: (compiled regex, weight)
# Weight is additive — a query can score in multiple categories simultaneously.
# Weights reflect diagnostic specificity: higher = more distinctive term.

_PATTERNS: dict[IssueCategory, list[tuple[re.Pattern, float]]] = {
    IssueCategory.KYC: [
        (re.compile(r"\b(kyc|vkyc|video[\s\-]*kyc|e[\s\-]*kyc|ekyc|ckyc|central[\s\-]*kyc)\b", re.I), 2.0),
        (re.compile(r"\b(pan|aadhaar|aadhar|passport|voter[\s\-]*id|driving[\s\-]*licen[sc]e|dl\b)\b", re.I), 1.8),
        (re.compile(r"\b(liveness|face[\s\-]*match|selfie|biometric|fingerprint)\b", re.I), 1.8),
        (re.compile(r"\b(ocr|optical[\s\-]*character|document[\s\-]*scan|image[\s\-]*quality)\b", re.I), 1.5),
        (re.compile(r"\b(cvl|nsdl|digilocker|digi[\s\-]*locker)\b", re.I), 1.8),
        (re.compile(r"\b(verif(?:y|ied|ication)|id[\s\-]*proof|document[\s\-]*upload)\b", re.I), 0.8),
        (re.compile(r"\b(mismatch|rejected|failed).*\b(pan|aadhaar|document|kyc)\b", re.I), 1.5),
    ],
    IssueCategory.TRANSACTION: [
        (re.compile(r"\b(upi|neft|rtgs|imps|nach|ach|ecs|mandate|enach)\b", re.I), 2.0),
        (re.compile(r"\b(otp|tpin|mpin|ipin|t[\s\-]*otp)\b", re.I), 1.5),
        (re.compile(r"\b(debit[\s\-]*card|credit[\s\-]*card|virtual[\s\-]*card|rupay|visa|mastercard)\b", re.I), 1.5),
        (re.compile(r"\b(transaction|transfer|payment|remittance|fund)\b", re.I), 1.0),
        (re.compile(r"\b(pending|stuck|reversed|declined|bounced|failed).*\b(payment|transaction|transfer)\b", re.I), 1.5),
        (re.compile(r"\b(balance|wallet|account[\s\-]*statement|mini[\s\-]*statement)\b", re.I), 0.8),
        (re.compile(r"\b(chargeback|dispute|unauthorized[\s\-]*transaction)\b", re.I), 1.8),
    ],
    IssueCategory.TECHNICAL: [
        (re.compile(r"\b(ERR[\s\-]?\d{3,}|PGRST\d+|error[\s\-]*code)\b", re.I), 2.0),
        (re.compile(r"\b(4\d\d|5\d\d)[\s\-]*(error|status|response)\b", re.I), 1.5),
        (re.compile(r"\b(api|sdk|webhook|endpoint|http|https|rest|graphql)\b", re.I), 1.0),
        (re.compile(r"\b(timeout|connection[\s\-]*refused|503|502|504|gateway)\b", re.I), 1.5),
        (re.compile(r"\b(authenticat(?:e|ion)|authoriz(?:e|ation)|token[\s\-]*expir|jwt|bearer)\b", re.I), 1.2),
        (re.compile(r"\b(bug|crash|exception|traceback|stack[\s\-]*trace|not[\s\-]*work(?:ing)?)\b", re.I), 1.0),
        (re.compile(r"\b(integrat(?:e|ion)|connect(?:ion)?|import[\s\-]*error|module)\b", re.I), 0.8),
        (re.compile(r"\b(ssl|tls|certificate|handshake)\b", re.I), 1.5),
    ],
    IssueCategory.BILLING: [
        (re.compile(r"\b(invoice|bill(?:ing)?|receipt|statement)\b", re.I), 1.5),
        (re.compile(r"\b(subscription|plan|tier|pricing|upgrade|downgrade)\b", re.I), 1.2),
        (re.compile(r"\b(refund|charge(?:back)?|overpaid|credit[\s\-]*note)\b", re.I), 1.5),
        (re.compile(r"\b(gst|gstin|igst|cgst|sgst|tds|tax[\s\-]*deduct)\b", re.I), 1.8),
        (re.compile(r"\b(quota|limit[\s\-]*exceed|api[\s\-]*call[\s\-]*limit|overage)\b", re.I), 1.2),
    ],
    IssueCategory.ONBOARDING: [
        (re.compile(r"\b(onboard(?:ing)?|sign[\s\-]*up|register(?:ation)?)\b", re.I), 2.0),
        (re.compile(r"\b(new[\s\-]*user|new[\s\-]*client|new[\s\-]*account|first[\s\-]*time)\b", re.I), 1.5),
        (re.compile(r"\b(activat(?:e|ion)|get[\s\-]*started|setup|configure)\b", re.I), 1.0),
        (re.compile(r"\b(credential|api[\s\-]*key[\s\-]*generation|sandbox)\b", re.I), 1.0),
    ],
    IssueCategory.SOP_LOOKUP: [
        (re.compile(r"\b(sop|standard[\s\-]*operating[\s\-]*procedure)\b", re.I), 2.5),
        (re.compile(r"\b(procedure|process|workflow|checklist|policy|guideline)\b", re.I), 1.5),
        (re.compile(r"\b(tat|turnaround[\s\-]*time|sla|service[\s\-]*level)\b", re.I), 1.8),
        (re.compile(r"\b(how[\s\-]*should|what[\s\-]*is[\s\-]*the[\s\-]*process|what[\s\-]*are[\s\-]*the[\s\-]*steps)\b", re.I), 1.2),
        (re.compile(r"\b(escalat(?:e|ion)[\s\-]*procedure|rca[\s\-]*template)\b", re.I), 1.5),
        (re.compile(r"\b(audit|compli(?:ance|ant)|regulator(?:y)?)\b", re.I), 1.0),
    ],
    IssueCategory.ESCALATION: [
        (re.compile(r"\b(fraud|scam|phish(?:ing)?|suspicious[\s\-]*activity|unauthorized)\b", re.I), 2.5),
        (re.compile(r"\b(rbi|sebi|npci|irdai|regulator)\b", re.I), 2.0),
        (re.compile(r"\b(legal|court|notice|lawsuit|arbitrat(?:e|ion)|litigation)\b", re.I), 2.0),
        (re.compile(r"\b(urgent|critical|emergency|immediately|asap)\b", re.I), 1.5),
        (re.compile(r"\b(grievan(?:ce|t)|ombudsman|consumer[\s\-]*forum|complaint[\s\-]*authority)\b", re.I), 2.0),
        (re.compile(r"\b(data[\s\-]*breach|security[\s\-]*incident|compromise[d]?)\b", re.I), 2.5),
        (re.compile(r"\b(supervisor|manager|escalat(?:e|ion))\b", re.I), 1.2),
    ],
    IssueCategory.FAQ: [
        (re.compile(r"\b(what[\s\-]*is|what[\s\-]*are|define|explain|meaning|overview)\b", re.I), 0.8),
        (re.compile(r"\b(how[\s\-]*does|how[\s\-]*do(?:es)?|can[\s\-]*I)\b", re.I), 0.6),
        (re.compile(r"\b(difference[\s\-]*between|compare|vs\.?\s|versus)\b", re.I), 0.9),
        (re.compile(r"\b(feature|capability|support(?:s|ed)?|compatible)\b", re.I), 0.5),
    ],
    IssueCategory.CONVERSATIONAL: [
        (re.compile(r"^(hi|hello|hey|good[\s\-]*(morning|afternoon|evening)|howdy)[.!?]*$", re.I), 3.0),
        (re.compile(r"^(thanks?|thank[\s\-]*you|thx|ty)[.!?]*$", re.I), 3.0),
        (re.compile(r"^(ok(?:ay)?|yes|no|sure|got[\s\-]*it|noted|understood|agreed)[.!?]*$", re.I), 3.0),
        (re.compile(r"\b(thanks?|thank[\s\-]*you|appreciate|great|perfect|wonderful)\b", re.I), 0.8),
    ],
}

# ── Spam detection patterns ───────────────────────────────────────────────────

_SPAM_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:https?://|www\.)\S{5,}", re.I),       # External URLs
    re.compile(r"<\s*script|<\s*iframe|javascript\s*:", re.I),  # XSS/injection
    re.compile(r"(.)\1{15,}"),                             # Character flood (aaaaaa...)
    re.compile(r"^\W+$"),                                  # Symbol-only input
    re.compile(r"\b(buy|sell|earn|money|invest|crypto|bitcoin|forex)\b.{0,20}\b(now|today|fast|quick)\b", re.I),
    re.compile(r"(?:SELECT|INSERT|UPDATE|DELETE|DROP|UNION|--|;--)\s", re.I),  # SQL injection
]

_MAX_QUERY_LEN = 2000
_MIN_QUERY_LEN_FOR_CLASSIFICATION = 8


def _detect_spam(query: str) -> bool:
    if len(query) > _MAX_QUERY_LEN:
        return True
    for pat in _SPAM_PATTERNS:
        if pat.search(query):
            return True
    return False


def _score_categories(query: str) -> dict[IssueCategory, float]:
    scores: dict[IssueCategory, float] = {cat: 0.0 for cat in IssueCategory}
    for category, patterns in _PATTERNS.items():
        for pattern, weight in patterns:
            matches = pattern.findall(query)
            if matches:
                # Cap at 3× to prevent a single pattern from dominating
                scores[category] += weight * min(len(matches), 3)
    return scores


def _normalize_confidence(score: float, total_score: float, n_categories_with_score: int) -> float:
    """Map raw score to [0.0, 1.0]. Higher when one category dominates clearly."""
    if total_score == 0 or score == 0:
        return 0.0
    fraction = score / total_score
    # Apply mild compression: perfect match = 0.92, not 1.0 (avoid overconfidence)
    # Penalize when many categories score (ambiguous query)
    ambiguity_penalty = max(0.0, (n_categories_with_score - 1) * 0.04)
    return round(min(0.92, max(0.05, fraction * 1.3 - ambiguity_penalty)), 3)


class QueryClassifier:
    """
    Production-grade deterministic query classifier for KwikID support queries.

    Synchronous path (classify) is always safe for high-concurrency use.
    Async path (classify_query) adds optional LLM enrichment for low-confidence results.

    Configuration env vars:
      QUERY_ROUTER_LLM_ENABLED    — enable LLM fallback (default: false)
      QUERY_ROUTER_LLM_THRESHOLD  — confidence below which LLM is tried (default: 0.35)
      QUERY_ROUTER_LLM_MODEL      — model for LLM classification (default: gpt-4o-mini)
    """

    def __init__(self) -> None:
        import os
        self._llm_enabled = os.getenv("QUERY_ROUTER_LLM_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self._llm_threshold = float(os.getenv("QUERY_ROUTER_LLM_THRESHOLD", "0.35"))
        self._llm_model = os.getenv("QUERY_ROUTER_LLM_MODEL", "gpt-4o-mini")
        LOGGER.info(
            "QueryClassifier initialized llm_enabled=%s llm_threshold=%.2f model=%s",
            self._llm_enabled,
            self._llm_threshold,
            self._llm_model,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify(self, query: str) -> ClassificationResult:
        """
        Synchronous deterministic classification. No network calls.
        Safe to call from synchronous and async contexts.
        """
        query_stripped = query.strip()

        # 1. Spam check
        if _detect_spam(query_stripped):
            return ClassificationResult(
                category=IssueCategory.SPAM,
                confidence=0.95,
                reasoning="Query matched spam/injection detection patterns.",
                classifier_used="deterministic_spam",
            )

        # 2. Very short queries → conversational (not enough signal for classification)
        if len(query_stripped) < _MIN_QUERY_LEN_FOR_CLASSIFICATION:
            return ClassificationResult(
                category=IssueCategory.CONVERSATIONAL,
                confidence=0.85,
                reasoning=f"Query too short ({len(query_stripped)} chars) for classification.",
                classifier_used="deterministic_length",
            )

        # 3. Pattern scoring across all categories
        scores = _score_categories(query_stripped)
        total_score = sum(scores.values())
        categories_with_score = sum(1 for v in scores.values() if v > 0)

        if total_score == 0:
            return ClassificationResult(
                category=IssueCategory.FAQ,
                confidence=0.25,
                reasoning="No domain-specific patterns matched. Treating as general FAQ.",
                classifier_used="deterministic_fallback",
            )

        # 4. Rank categories by score
        ranked = sorted(
            [(cat, score) for cat, score in scores.items() if score > 0],
            key=lambda x: x[1],
            reverse=True,
        )
        best_category, best_score = ranked[0]
        confidence = _normalize_confidence(best_score, total_score, categories_with_score)

        secondary = {
            cat.value: round(score / total_score, 3)
            for cat, score in ranked[1:4]  # top-3 secondary
            if score > 0
        }

        return ClassificationResult(
            category=best_category,
            confidence=confidence,
            reasoning=(
                f"Deterministic: {best_category.value} "
                f"score={best_score:.2f}/{total_score:.2f} "
                f"confidence={confidence:.2f} "
                f"competing_categories={categories_with_score}"
            ),
            secondary_categories=secondary,
            classifier_used="deterministic",
        )

    def route(self, query: str) -> RoutingDecision:
        """Classify and immediately produce a RoutingDecision. Synchronous."""
        result = self.classify(query)
        return RoutingDecision.from_classification(result)

    async def classify_query(self, query: str) -> ClassificationResult:
        """
        Async classification with optional LLM enrichment for low-confidence results.
        Always falls back to deterministic result if LLM fails.
        """
        result = self.classify(query)

        if (
            self._llm_enabled
            and result.confidence < self._llm_threshold
            and result.category not in {IssueCategory.SPAM, IssueCategory.CONVERSATIONAL}
        ):
            try:
                result = await self._llm_classify(query, result)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "LLM classification failed (%s) — using deterministic result",
                    type(exc).__name__,
                )

        return result

    async def route_query(self, query: str) -> RoutingDecision:
        """Async classification + routing decision."""
        result = await self.classify_query(query)
        return RoutingDecision.from_classification(result)

    # ── LLM enrichment (optional) ─────────────────────────────────────────────

    async def _llm_classify(
        self,
        query: str,
        fallback: ClassificationResult,
    ) -> ClassificationResult:
        """
        Call gpt-4o-mini to classify ambiguous queries.
        Timeout: 5 seconds. Falls back to deterministic result on any failure.
        """
        import os
        import json
        import httpx

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return fallback

        categories_desc = "\n".join(
            f"- {cat.value}: {desc}"
            for cat, desc in CATEGORY_DESCRIPTIONS.items()
        )
        system = (
            "You are a KwikID support query classifier. "
            "Classify the query into exactly one of these categories. "
            'Respond with valid JSON ONLY: {"category": "<value>", "confidence": 0.0-1.0, "reasoning": "<brief>"}'
        )
        user_msg = f"Categories:\n{categories_desc}\n\nQuery to classify:\n{query}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": self._llm_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": 80,
                        "temperature": 0.0,
                    },
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                parsed: dict[str, Any] = json.loads(raw)

                cat_str = str(parsed.get("category", "")).lower().strip()
                llm_confidence = float(parsed.get("confidence", 0.5))
                llm_reasoning = str(parsed.get("reasoning", ""))

                llm_category = IssueCategory(cat_str)  # raises ValueError if invalid

                LOGGER.debug(
                    "LLM classifier: %s → %s (conf=%.2f)",
                    fallback.category.value,
                    llm_category.value,
                    llm_confidence,
                )
                return ClassificationResult(
                    category=llm_category,
                    confidence=min(0.95, llm_confidence),
                    reasoning=f"LLM({self._llm_model}): {llm_reasoning}",
                    classifier_used="llm",
                )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            LOGGER.debug("LLM classification parse error: %s — using deterministic", exc)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("LLM classification network error: %s — using deterministic", exc)

        return fallback


# Module-level singleton — use this in all application code
classifier = QueryClassifier()
