"""
rag_engine/ingestion/knowledge_quality_validator.py

WORK ITEM 4: Knowledge Quality Validator.

Pre-ingestion validation gate. Runs BEFORE the KnowledgePipeline processes
an article.  All checks are deterministic and do NOT call any external service.

Failure modes handled:
  - Missing answer
  - Empty SOP / near-empty content
  - Broken markdown (encoding corruption)
  - Duplicate content (SHA256-based, in-memory within a run)
  - Image reference mismatch (refs in text but no manifest entry)
  - Invalid completeness scores (outside [0, 1])
  - Broken canonical URLs

Each failed validation produces a ValidationIssue with a severity level:
  "error"   — article MUST be rejected; it cannot be ingested safely
  "warning" — article is ingested but flagged for manual review

ValidationResult.is_valid is True only when there are NO "error"-level issues.

Usage:
    validator = KnowledgeQualityValidator()
    result = validator.validate(article)
    if not result.is_valid:
        # reject / flag for manual review
        for issue in result.errors:
            logger.warning(issue.message)
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from rag_engine.ingestion.parsers.stackoverflow_parser import KnowledgeArticle

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_MIN_QUESTION_CHARS     = 50    # absolute floor; shorter = not embeddable
_MIN_ANSWER_CHARS       = 30    # if article claims qa_pair, answer must be this long
_MIN_TOTAL_CHARS        = 100   # combined q+a must reach this
_MAX_REPETITION_RATIO   = 0.70  # if >70% of tokens are duplicate → corrupt

# Broken URL: must start with http:// or https://
_VALID_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Markdown corruption indicators
_CORRUPTION_RE = re.compile(
    r"(?:â€|Ã©|\\u[0-9a-fA-F]{4}|&#[0-9]+;|&amp;amp;|<\?xml|<!DOCTYPE)",
)

# Broken markdown: unterminated or unpaired Markdown block markers
_BROKEN_MARKDOWN_RE = re.compile(
    r"(?:```[^`]*$|^\s*!\[.*?\]\(\s*\))",  # unclosed code fences or empty image links
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationIssue:
    """One validation problem on an article."""
    code:     str     # machine-readable code, e.g. "MISSING_ANSWER"
    severity: str     # "error" | "warning"
    message:  str     # human-readable description


@dataclass
class ValidationResult:
    """Aggregate result of validating one KnowledgeArticle."""
    article_id: str
    issues:     list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True only when there are no 'error'-level issues."""
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def manual_review_required(self) -> bool:
        """True if any warning-level issue is present (even when is_valid=True)."""
        return len(self.warnings) > 0


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class KnowledgeQualityValidator:
    """
    Pre-ingestion quality gate for KnowledgeArticle objects.

    Stateful only for cross-article duplicate detection within a single run.
    Safe to reuse across runs: call reset_run() between ingestion runs.

    Usage:
        validator = KnowledgeQualityValidator()
        result = validator.validate(article)
        if not result.is_valid:
            reject_article(article, result)
        elif result.manual_review_required:
            flag_for_review(article)
    """

    def __init__(self) -> None:
        # Track content hashes seen in this run for duplicate detection
        self._seen_hashes: set[str] = set()

    def reset_run(self) -> None:
        """Reset cross-run state. Call between ingestion runs."""
        self._seen_hashes.clear()

    def validate(self, article: "KnowledgeArticle") -> ValidationResult:
        """
        Run all quality checks on a KnowledgeArticle.

        Returns ValidationResult. Never raises.
        """
        result = ValidationResult(article_id=article.article_id)
        try:
            self._run_checks(article, result)
        except Exception as exc:  # noqa: BLE001
            result.issues.append(
                ValidationIssue(
                    code="VALIDATOR_INTERNAL_ERROR",
                    severity="error",
                    message=f"Validator crashed on article {article.article_id}: {exc}",
                )
            )
        return result

    # ── Internal checks ───────────────────────────────────────────────────────

    def _run_checks(
        self,
        article: "KnowledgeArticle",
        result: ValidationResult,
    ) -> None:
        q_text = (article.question_markdown or article.question_body or "").strip()
        a_text = (article.answer_markdown or article.answer_body or "").strip()

        # 1. Missing question body
        if not q_text or len(q_text) < _MIN_QUESTION_CHARS:
            result.issues.append(ValidationIssue(
                code="MISSING_QUESTION",
                severity="error",
                message=(
                    f"Article {article.article_id}: question body is empty or too short "
                    f"({len(q_text)} chars, min={_MIN_QUESTION_CHARS})."
                ),
            ))

        # 2. Missing answer on qa_pair articles
        if article.article_type == "qa_pair" and (not a_text or len(a_text) < _MIN_ANSWER_CHARS):
            result.issues.append(ValidationIssue(
                code="MISSING_ANSWER",
                severity="error",
                message=(
                    f"Article {article.article_id}: declared article_type=qa_pair but "
                    f"answer body is absent or too short ({len(a_text)} chars)."
                ),
            ))

        # 3. Empty SOP check (combined content too short)
        total_chars = len(q_text) + len(a_text)
        if total_chars < _MIN_TOTAL_CHARS:
            result.issues.append(ValidationIssue(
                code="EMPTY_SOP",
                severity="error",
                message=(
                    f"Article {article.article_id}: combined content is {total_chars} chars "
                    f"(min={_MIN_TOTAL_CHARS}). Article is too thin to embed usefully."
                ),
            ))

        # 4. Broken markdown / encoding corruption
        combined = q_text + " " + a_text
        if _CORRUPTION_RE.search(combined):
            result.issues.append(ValidationIssue(
                code="PARSER_CORRUPTION",
                severity="error",
                message=(
                    f"Article {article.article_id}: content contains encoding corruption "
                    f"(mojibake or HTML entity artifacts). Parser output is unreliable."
                ),
            ))

        # 5. Unterminated markdown structures
        if _BROKEN_MARKDOWN_RE.search(combined):
            result.issues.append(ValidationIssue(
                code="BROKEN_MARKDOWN",
                severity="warning",
                message=(
                    f"Article {article.article_id}: content contains broken markdown "
                    f"(unclosed code fences or empty image links)."
                ),
            ))

        # 6. Broken canonical URL
        canonical = getattr(article, "canonical_url", "")
        if not canonical or not _VALID_URL_RE.match(canonical):
            result.issues.append(ValidationIssue(
                code="BROKEN_CANONICAL_URL",
                severity="warning",
                message=(
                    f"Article {article.article_id}: canonical_url is missing or not "
                    f"an absolute HTTP URL: {canonical!r}."
                ),
            ))

        # 7. Duplicate content detection (within this run)
        content_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        if content_hash in self._seen_hashes:
            result.issues.append(ValidationIssue(
                code="DUPLICATE_CONTENT",
                severity="error",
                message=(
                    f"Article {article.article_id}: content is a duplicate of an "
                    f"already-seen article in this ingestion run (hash={content_hash[:16]})."
                ),
            ))
        else:
            self._seen_hashes.add(content_hash)

        # 8. Image reference mismatch
        image_refs = getattr(article, "image_references", [])
        unresolved_manifest = [r for r in image_refs if not r.manifest_found]
        unresolved_local    = [r for r in image_refs if r.manifest_found and r.local_path is None]

        if unresolved_manifest:
            result.issues.append(ValidationIssue(
                code="IMAGE_MANIFEST_MISSING",
                severity="warning",
                message=(
                    f"Article {article.article_id}: {len(unresolved_manifest)} image(s) "
                    f"referenced in content but not found in images.json manifest: "
                    f"{[r.guid[:8] for r in unresolved_manifest]}."
                ),
            ))
        if unresolved_local:
            result.issues.append(ValidationIssue(
                code="IMAGE_ASSET_MISSING",
                severity="warning",
                message=(
                    f"Article {article.article_id}: {len(unresolved_local)} image(s) have "
                    f"manifest entries but no local binary file on disk."
                ),
            ))

        # 9. Invalid completeness score
        completeness = getattr(article, "completeness_score", None)
        if completeness is not None and not (0.0 <= completeness <= 1.0):
            result.issues.append(ValidationIssue(
                code="INVALID_COMPLETENESS_SCORE",
                severity="error",
                message=(
                    f"Article {article.article_id}: completeness_score={completeness!r} "
                    f"is outside valid range [0.0, 1.0]."
                ),
            ))

        # 10. Repetition check — near-duplicate tokens indicate corrupt/boilerplate content
        tokens = combined.lower().split()
        if len(tokens) >= 20:
            unique_ratio = len(set(tokens)) / len(tokens)
            if unique_ratio < (1.0 - _MAX_REPETITION_RATIO):
                result.issues.append(ValidationIssue(
                    code="REPETITIVE_CONTENT",
                    severity="warning",
                    message=(
                        f"Article {article.article_id}: {(1-unique_ratio)*100:.0f}% of tokens "
                        f"are duplicates (unique_ratio={unique_ratio:.2f}). "
                        f"Content may be boilerplate or corrupt."
                    ),
                ))
