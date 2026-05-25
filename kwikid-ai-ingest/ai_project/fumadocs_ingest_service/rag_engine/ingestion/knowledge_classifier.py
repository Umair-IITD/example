"""
rag_engine/ingestion/knowledge_classifier.py

Classifies KnowledgeArticle objects and computes quality scores.

Classification hierarchy:
  VERIFIED_REPLY  — accepted answer OR answer_score >= 3
  TROUBLESHOOTING — technical fix content (error/issue/config patterns in title/tags)
  FAQ             — general Q&A
  POLICY          — process/procedure/compliance content
  RCA             — root-cause analysis documented
  ESCALATION      — must NEVER enter retrieval (safety critical)

Quality score formula (deterministic, [0.0, 1.0]):
  0.35 * accepted_bonus     (1.0 if accepted answer, else 0.0)
  0.25 * answer_score_norm  (min(1.0, answer_score / 10.0))
  0.20 * answer_len_norm    (min(1.0, len(answer_body) / 500.0))
  0.10 * question_score_norm(min(1.0, max(0.0, question_score / 5.0)))
  0.10 * view_norm          (min(1.0, view_count / 50.0))

Escalation detection:
  Posts matching ESCALATION_SIGNALS in title or answer body are classified as
  ESCALATION and their is_embeddable flag is set to False. These NEVER reach
  rag_knowledge_chunks. This is a safety-critical invariant.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag_engine.ingestion.parsers.stackoverflow_parser import KnowledgeArticle

LOGGER = logging.getLogger(__name__)


class KnowledgeClass(str, Enum):
    VERIFIED_REPLY  = "VERIFIED_REPLY"
    TROUBLESHOOTING = "TROUBLESHOOTING"
    FAQ             = "FAQ"
    POLICY          = "POLICY"
    RCA             = "RCA"
    ESCALATION      = "ESCALATION"  # NEVER embedded


@dataclass
class ClassificationResult:
    knowledge_class: KnowledgeClass
    quality_score:   float
    is_embeddable:   bool          # False for ESCALATION and very-low-quality posts
    reject_reason:   str           # Human-readable explanation when is_embeddable=False


# ── Keyword signal lists ──────────────────────────────────────────────────────

# Posts matching ANY of these signals are classified as ESCALATION and blocked.
# Evaluated against lower-cased title + answer body.
_ESCALATION_SIGNALS: frozenset[str] = frozenset({
    "escalate",
    "escalation",
    "critical issue",
    "production down",
    "prod down",
    "data breach",
    "compliance violation",
    "legal",
    "fraud",
    "regulator",
    "regulatory",
    "rbi inspection",
    "security incident",
    "pen test",
    "penetration test",
})

# Title / tag keyword patterns for classification decision tree
_TROUBLESHOOTING_PATTERNS = re.compile(
    r"\b(error|issue|fail|failed|failure|crash|timeout|exception|bug|"
    r"broken|fix|resolve|debug|troubleshoot|problem|not working|"
    r"config|configure|setup|install|deploy|deployment|integration)\b",
    re.IGNORECASE,
)

_POLICY_PATTERNS = re.compile(
    r"\b(policy|process|procedure|sla|sop|compliance|guideline|"
    r"workflow|approval|mandate|protocol)\b",
    re.IGNORECASE,
)

_RCA_PATTERNS = re.compile(
    r"\b(root cause|rca|post mortem|why did|investigation|"
    r"analysis|diagnosis|investigated)\b",
    re.IGNORECASE,
)

_FAQ_TRIGGER_WORDS = re.compile(
    r"\b(how|what|when|why|where|which|who|can i|should i|is it)\b",
    re.IGNORECASE,
)

# Minimum content thresholds
_MIN_QUESTION_CHARS  = 50
_MIN_ANSWER_CHARS    = 80
_MIN_QUALITY_TO_EMBED = 0.15   # absolute floor — below this the content is too thin


class KnowledgeClassifier:
    """
    Classifies a KnowledgeArticle and computes its quality score.

    Stateless and thread-safe.

    Usage:
        classifier = KnowledgeClassifier()
        result = classifier.classify(article)
        if result.is_embeddable:
            print(result.knowledge_class, result.quality_score)
    """

    def classify(self, article: "KnowledgeArticle") -> ClassificationResult:
        """
        Classify a KnowledgeArticle and compute its quality score.

        Returns ClassificationResult. is_embeddable=False means the article
        must NOT be chunked or embedded.
        """
        # ── 1. Minimum content validation ────────────────────────────────────
        if not article.question_body or len(article.question_body.strip()) < _MIN_QUESTION_CHARS:
            return ClassificationResult(
                knowledge_class=KnowledgeClass.FAQ,
                quality_score=0.0,
                is_embeddable=False,
                reject_reason="question_body too short",
            )

        if not article.title or not article.title.strip():
            return ClassificationResult(
                knowledge_class=KnowledgeClass.FAQ,
                quality_score=0.0,
                is_embeddable=False,
                reject_reason="missing title",
            )

        # ── 2. Escalation detection (safety-critical, checked before anything) ─
        combined_text = (
            (article.title or "") + " " +
            (article.question_body or "") + " " +
            (article.answer_body or "")
        ).lower()

        for signal in _ESCALATION_SIGNALS:
            if signal in combined_text:
                LOGGER.debug(
                    "ESCALATION detected in post %s (signal=%r)",
                    article.article_id, signal,
                )
                return ClassificationResult(
                    knowledge_class=KnowledgeClass.ESCALATION,
                    quality_score=0.0,
                    is_embeddable=False,
                    reject_reason=f"escalation_signal={signal!r}",
                )

        # ── 3. Deleted post guard ─────────────────────────────────────────────
        if article.post_state.lower() in {"deleted", "closed", "locked"}:
            return ClassificationResult(
                knowledge_class=KnowledgeClass.FAQ,
                quality_score=0.0,
                is_embeddable=False,
                reject_reason=f"post_state={article.post_state!r}",
            )

        # ── 4. Compute quality score ──────────────────────────────────────────
        quality_score = self.compute_quality_score(article)

        # ── 5. Very-low-quality floor ─────────────────────────────────────────
        if quality_score < _MIN_QUALITY_TO_EMBED:
            return ClassificationResult(
                knowledge_class=KnowledgeClass.FAQ,
                quality_score=quality_score,
                is_embeddable=False,
                reject_reason=f"quality_score={quality_score:.3f} < {_MIN_QUALITY_TO_EMBED}",
            )

        # ── 6. Classify ───────────────────────────────────────────────────────
        knowledge_class = self._classify_content(article)

        return ClassificationResult(
            knowledge_class=knowledge_class,
            quality_score=quality_score,
            is_embeddable=True,
            reject_reason="",
        )

    def should_embed(self, article: "KnowledgeArticle") -> bool:
        """Convenience wrapper — True if the article is safe to embed."""
        return self.classify(article).is_embeddable

    def compute_quality_score(self, article: "KnowledgeArticle") -> float:
        """
        Deterministic quality score [0.0, 1.0].

        Formula:
            0.35 * accepted_bonus
          + 0.25 * answer_score_norm (capped at 1.0 at score=10)
          + 0.20 * answer_len_norm   (capped at 1.0 at 500 chars)
          + 0.10 * q_score_norm      (capped at 1.0 at score=5)
          + 0.10 * view_norm         (capped at 1.0 at 50 views)
        """
        accepted_bonus    = 1.0 if article.accepted_answer_id is not None else 0.0
        answer_score_norm = min(1.0, max(0.0, article.answer_score  / 10.0))
        q_score_norm      = min(1.0, max(0.0, article.question_score / 5.0))
        view_norm         = min(1.0, article.view_count / 50.0)

        answer_len = len(article.answer_body or "")
        len_score  = min(1.0, answer_len / 500.0)

        score = (
            0.35 * accepted_bonus
            + 0.25 * answer_score_norm
            + 0.20 * len_score
            + 0.10 * q_score_norm
            + 0.10 * view_norm
        )
        return round(min(1.0, max(0.0, score)), 3)

    def _classify_content(self, article: "KnowledgeArticle") -> KnowledgeClass:
        """Apply decision tree to assign the most appropriate KnowledgeClass."""
        # Rule 1: High-quality confirmed answers → VERIFIED_REPLY
        if article.accepted_answer_id is not None or article.answer_score >= 3:
            return KnowledgeClass.VERIFIED_REPLY

        combined = f"{article.title} {article.question_body}"

        # Rule 2: RCA patterns in title
        if _RCA_PATTERNS.search(article.title):
            return KnowledgeClass.RCA

        # Rule 3: Policy / compliance patterns
        if _POLICY_PATTERNS.search(combined):
            return KnowledgeClass.POLICY

        # Rule 4: Technical/operational troubleshooting
        if _TROUBLESHOOTING_PATTERNS.search(combined):
            return KnowledgeClass.TROUBLESHOOTING

        # Rule 5: Question-style title → FAQ (catch-all)
        return KnowledgeClass.FAQ
