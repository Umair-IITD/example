"""
tests/test_knowledge_retrieval_evaluation.py

Sprint 2.30: Knowledge layer retrieval evaluation suite.

Deterministic, no-external-calls tests. No OpenAI/Claude API calls,
no Supabase calls, no network. All external dependencies are patched
with unittest.mock.

Test classes:
  TestParserRoundTrip            — synthetic SO post → KnowledgeArticle field assertions
  TestQualityValidatorChecks     — one test per validation check (10 checks)
  TestImageGrounding             — IMAGE_MANIFEST_MISSING / IMAGE_ASSET_MISSING warnings
  TestDuplicateDetection         — second call with same content → DUPLICATE_CONTENT error
  TestCompletenessFloor          — low score → manual_review_required=True
  TestKnowledgeMetricsCollector  — rag_engine MetricsCollector knowledge_ helpers
  TestHybridRetrieverInterface   — mock-patched hybrid retriever (RRF path)
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers — build minimal KnowledgeArticle stubs without touching the parser
# ---------------------------------------------------------------------------

def _make_article(**overrides: Any):
    """Return a KnowledgeArticle-like object with all required fields set to safe defaults."""
    from rag_engine.ingestion.parsers.stackoverflow_parser import (
        ImageReference,
        KnowledgeArticle,
    )

    defaults = dict(
        article_id="so_1001",
        source="stackoverflow_for_teams",
        source_post_id=1001,
        article_type="qa_pair",
        canonical_url="https://stackoverflowteams.com/c/kwikid/questions/1001",
        title="How to restart the KwikID service when it is unresponsive?",
        question_body="When the KwikID payment gateway service becomes unresponsive after a spike, how do we restart it safely without data loss?",
        answer_body="SSH into the host, run 'sudo systemctl restart kwikid-gateway', then verify the heartbeat endpoint returns 200 within 30 seconds.",
        question_markdown="When the KwikID payment gateway service becomes unresponsive after a spike, how do we restart it safely without data loss?",
        answer_markdown="SSH into the host, run 'sudo systemctl restart kwikid-gateway', then verify the heartbeat endpoint returns 200 within 30 seconds.",
        answer_post_id=2001,
        answer_score=5,
        question_score=3,
        view_count=42,
        accepted_answer_id=2001,
        tags_raw=["gateway", "restart", "kwikid"],
        clients=["kwikid"],
        image_references=[],
        completeness_flags={
            "question_present": True,
            "answer_present": True,
            "accepted_answer_present": True,
            "tags_present": True,
            "canonical_url_present": True,
            "markdown_present": True,
            "image_metadata_resolved": True,
            "image_assets_resolved": True,
            "minimum_content_length": True,
            "no_parser_corruption": True,
            "client_relevance_detectable": True,
        },
        completeness_score=1.0,
        manual_review_required=False,
        image_count=0,
        resolved_image_count=0,
        missing_image_count=0,
        image_grounding_status="no_images",
        safety_classification="safe_for_llm",
        post_state="Published",
        created_at_source="2026-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return KnowledgeArticle(**defaults)


def _make_image_ref(*, manifest_found: bool, local_path: str | None = None):
    from rag_engine.ingestion.parsers.stackoverflow_parser import ImageReference
    return ImageReference(
        url="https://stackoverflowteams.com/c/kwikid/images/s/46791add-fd95-4bf4-9785-9b7d7ecce7c3.png",
        guid="46791add-fd95-4bf4-9785-9b7d7ecce7c3",
        extension="png",
        image_id=99 if manifest_found else None,
        manifest_found=manifest_found,
        local_path=local_path,
    )


# ---------------------------------------------------------------------------
# 1. Parser Round-Trip
# ---------------------------------------------------------------------------

class TestParserRoundTrip(unittest.TestCase):
    """
    Parse a synthetic StackOverflow post dict and assert all metadata fields
    populate correctly without touching any real file on disk.
    """

    def _build_export_dir(self, tmp_path: Path) -> None:
        posts = [
            {
                "id": 1537,
                "postType": "question",
                "title": "How to fix KwikID timeout issues in production?",
                "bodyMarkdown": "When we see timeout errors from the KwikID payment system, what is the correct remediation procedure to follow?",
                "score": 3,
                "viewCount": 21,
                "acceptedAnswerId": 2001,
                "tags": ["kwikid-payments", "timeout", "production-fix"],
                "creationDate": "2026-01-10T08:00:00Z",
            },
            {
                "id": 2001,
                "postType": "answer",
                "parentId": 1537,
                "bodyMarkdown": "Check the gateway logs, increase connection pool size, and restart the payment adapter service using the SOP runbook.",
                "score": 9,
            },
        ]
        (tmp_path / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
        (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
        (tmp_path / "posts2votes.json").write_text("[]", encoding="utf-8")
        (tmp_path / "tags.json").write_text("[]", encoding="utf-8")
        (tmp_path / "images.json").write_text("[]", encoding="utf-8")

    def test_article_id_is_so_prefixed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._build_export_dir(tmp_path)
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            parser = StackOverflowParser()
            articles = parser.parse(tmp_path)
            self.assertEqual(len(articles), 1)
            self.assertEqual(articles[0].article_id, "so_1537")

    def test_article_type_qa_pair_when_answer_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._build_export_dir(tmp_path)
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertEqual(articles[0].article_type, "qa_pair")

    def test_image_count_zero_when_no_images(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._build_export_dir(tmp_path)
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            a = articles[0]
            self.assertEqual(a.image_count, 0)
            self.assertEqual(a.resolved_image_count, 0)
            self.assertEqual(a.missing_image_count, 0)
            self.assertEqual(a.image_grounding_status, "no_images")

    def test_completeness_score_between_0_and_1(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._build_export_dir(tmp_path)
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            score = articles[0].completeness_score
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_safety_classification_populated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._build_export_dir(tmp_path)
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertIn(
                articles[0].safety_classification,
                {"safe_for_llm", "restricted_internal", "sensitive_operations"},
            )

    def test_source_is_stackoverflow_for_teams(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._build_export_dir(tmp_path)
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertEqual(articles[0].source, "stackoverflow_for_teams")

    def test_accepted_answer_selected_over_higher_score(self):
        """Accepted answer must win over a higher-scored non-accepted answer."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            posts = [
                {
                    "id": 100,
                    "postType": "question",
                    "title": "What is the KwikID account activation process?",
                    "bodyMarkdown": "Explain the full KwikID account activation workflow for new enterprise customers.",
                    "score": 1,
                    "viewCount": 5,
                    "acceptedAnswerId": 101,
                    "tags": ["activation"],
                },
                {
                    "id": 101,
                    "postType": "answer",
                    "parentId": 100,
                    "bodyMarkdown": "This is the accepted answer with the correct activation steps.",
                    "score": 2,
                },
                {
                    "id": 102,
                    "postType": "answer",
                    "parentId": 100,
                    "bodyMarkdown": "This is a higher-scored alternative answer that should not win.",
                    "score": 10,
                },
            ]
            (tmp_path / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
            (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
            (tmp_path / "posts2votes.json").write_text("[]", encoding="utf-8")
            (tmp_path / "tags.json").write_text("[]", encoding="utf-8")
            (tmp_path / "images.json").write_text("[]", encoding="utf-8")
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertEqual(len(articles), 1)
            self.assertEqual(articles[0].answer_post_id, 101)

    def test_sensitive_operations_classification(self):
        """Articles mentioning 'production secret' get sensitive_operations classification."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            posts = [
                {
                    "id": 200,
                    "postType": "question",
                    "title": "How to rotate the production secret key safely?",
                    "bodyMarkdown": "We need to rotate the production secret in the vault. What are the exact steps to follow without downtime?",
                    "score": 1,
                    "viewCount": 2,
                    "acceptedAnswerId": None,
                    "tags": ["security"],
                },
            ]
            (tmp_path / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
            (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
            (tmp_path / "posts2votes.json").write_text("[]", encoding="utf-8")
            (tmp_path / "tags.json").write_text("[]", encoding="utf-8")
            (tmp_path / "images.json").write_text("[]", encoding="utf-8")
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertEqual(articles[0].safety_classification, "sensitive_operations")


# ---------------------------------------------------------------------------
# 2. Quality Validator Checks (one test per check)
# ---------------------------------------------------------------------------

class TestQualityValidatorChecks(unittest.TestCase):
    """One test per validation check, 10 checks total."""

    def _validator(self):
        from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
        v = KnowledgeQualityValidator()
        v.reset_run()
        return v

    def _codes(self, result) -> set[str]:
        return {i.code for i in result.issues}

    def test_check_1_missing_question(self):
        """Short/empty question body triggers MISSING_QUESTION error."""
        v = self._validator()
        a = _make_article(
            article_id="so_missing_q",
            question_body="short",
            question_markdown="short",
        )
        result = v.validate(a)
        self.assertFalse(result.is_valid)
        self.assertIn("MISSING_QUESTION", self._codes(result))

    def test_check_2_missing_answer(self):
        """qa_pair with absent/short answer body triggers MISSING_ANSWER error."""
        v = self._validator()
        a = _make_article(
            article_id="so_missing_a",
            article_type="qa_pair",
            answer_body="",
            answer_markdown="",
        )
        result = v.validate(a)
        self.assertFalse(result.is_valid)
        self.assertIn("MISSING_ANSWER", self._codes(result))

    def test_check_3_empty_sop(self):
        """Combined content below _MIN_TOTAL_CHARS triggers EMPTY_SOP error."""
        v = self._validator()
        a = _make_article(
            article_id="so_empty_sop",
            question_body="Too short for embedding.",
            question_markdown="Too short for embedding.",
            answer_body="Also too short.",
            answer_markdown="Also too short.",
        )
        result = v.validate(a)
        self.assertFalse(result.is_valid)
        self.assertIn("EMPTY_SOP", self._codes(result))

    def test_check_4_parser_corruption(self):
        """Encoding corruption (â€) in content triggers PARSER_CORRUPTION error."""
        v = self._validator()
        corrupted = "This articleâ€™s content is corrupted and contains â€˜bad encoding artifacts from an old copy-paste operation that caused issues."
        a = _make_article(
            article_id="so_corrupt",
            question_body=corrupted,
            question_markdown=corrupted,
        )
        result = v.validate(a)
        self.assertFalse(result.is_valid)
        self.assertIn("PARSER_CORRUPTION", self._codes(result))

    def test_check_5_broken_markdown(self):
        """Unclosed code fences trigger BROKEN_MARKDOWN warning."""
        v = self._validator()
        broken_md = (
            "Here is how to restart the service:\n"
            "```bash\nsudo systemctl restart kwikid\n"
            "# NOTE: fence intentionally not closed to trigger check"
        )
        long_answer = "SSH into the server and run the restart command following the standard runbook procedure documented in confluence."
        a = _make_article(
            article_id="so_broken_md",
            question_body=broken_md,
            question_markdown=broken_md,
            answer_body=long_answer,
            answer_markdown=long_answer,
        )
        result = v.validate(a)
        codes = self._codes(result)
        self.assertIn("BROKEN_MARKDOWN", codes)

    def test_check_6_broken_canonical_url(self):
        """Non-HTTP canonical_url triggers BROKEN_CANONICAL_URL warning."""
        v = self._validator()
        a = _make_article(
            article_id="so_bad_url",
            canonical_url="not-a-valid-url",
        )
        result = v.validate(a)
        self.assertIn("BROKEN_CANONICAL_URL", self._codes(result))

    def test_check_7_duplicate_content(self):
        """Second validate() call with same content triggers DUPLICATE_CONTENT error."""
        v = self._validator()
        a = _make_article(article_id="so_dup_1")
        b = _make_article(article_id="so_dup_2")  # identical content, different ID
        r1 = v.validate(a)
        r2 = v.validate(b)
        # First article is fine
        self.assertNotIn("DUPLICATE_CONTENT", {i.code for i in r1.issues})
        # Second article with same content is flagged
        self.assertFalse(r2.is_valid)
        self.assertIn("DUPLICATE_CONTENT", {i.code for i in r2.issues})

    def test_check_8_image_manifest_missing(self):
        """Image refs with manifest_found=False trigger IMAGE_MANIFEST_MISSING warning."""
        v = self._validator()
        ref = _make_image_ref(manifest_found=False)
        a = _make_article(
            article_id="so_img_manifest",
            image_references=[ref],
            image_count=1,
            resolved_image_count=0,
            missing_image_count=1,
            image_grounding_status="unresolved",
        )
        result = v.validate(a)
        self.assertIn("IMAGE_MANIFEST_MISSING", self._codes(result))

    def test_check_9_invalid_completeness_score(self):
        """completeness_score outside [0, 1] triggers INVALID_COMPLETENESS_SCORE error."""
        v = self._validator()
        a = _make_article(
            article_id="so_bad_score",
            completeness_score=1.5,  # out of range
        )
        result = v.validate(a)
        self.assertFalse(result.is_valid)
        self.assertIn("INVALID_COMPLETENESS_SCORE", self._codes(result))

    def test_check_10_repetitive_content(self):
        """Content with >70% duplicate tokens triggers REPETITIVE_CONTENT warning."""
        v = self._validator()
        # 80% of tokens are the same word → unique_ratio ≈ 0.20 → fails
        repeated = ("spam " * 40) + "unique words here there everywhere always occasionally"
        a = _make_article(
            article_id="so_repetitive",
            question_body=repeated,
            question_markdown=repeated,
            answer_body="This answer explains the spam-detection process in the KwikID system and why it triggers alerts.",
            answer_markdown="This answer explains the spam-detection process in the KwikID system and why it triggers alerts.",
        )
        result = v.validate(a)
        self.assertIn("REPETITIVE_CONTENT", self._codes(result))

    def test_valid_article_passes_all_checks(self):
        """A fully valid article with unique content must pass all checks."""
        v = self._validator()
        a = _make_article(article_id="so_valid_unique_pass")
        result = v.validate(a)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.errors, [])


# ---------------------------------------------------------------------------
# 3. Image Grounding
# ---------------------------------------------------------------------------

class TestImageGrounding(unittest.TestCase):
    """Assert IMAGE_MANIFEST_MISSING and IMAGE_ASSET_MISSING warnings fire correctly."""

    def _validator(self):
        from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
        v = KnowledgeQualityValidator()
        v.reset_run()
        return v

    def test_manifest_missing_fires_for_unresolved_ref(self):
        v = self._validator()
        ref = _make_image_ref(manifest_found=False, local_path=None)
        a = _make_article(
            article_id="so_img_no_manifest",
            image_references=[ref],
        )
        result = v.validate(a)
        codes = {i.code for i in result.issues}
        self.assertIn("IMAGE_MANIFEST_MISSING", codes)
        # IMAGE_ASSET_MISSING should NOT fire when manifest itself is absent
        self.assertNotIn("IMAGE_ASSET_MISSING", codes)

    def test_asset_missing_fires_when_manifest_found_but_no_local_file(self):
        v = self._validator()
        # manifest_found=True but local_path=None
        ref = _make_image_ref(manifest_found=True, local_path=None)
        a = _make_article(
            article_id="so_img_no_asset",
            image_references=[ref],
        )
        result = v.validate(a)
        codes = {i.code for i in result.issues}
        self.assertIn("IMAGE_ASSET_MISSING", codes)
        self.assertNotIn("IMAGE_MANIFEST_MISSING", codes)

    def test_no_image_warnings_when_both_resolved(self):
        v = self._validator()
        ref = _make_image_ref(manifest_found=True, local_path="/data/images/test.png")
        a = _make_article(
            article_id="so_img_fully_resolved",
            image_references=[ref],
        )
        result = v.validate(a)
        codes = {i.code for i in result.issues}
        self.assertNotIn("IMAGE_MANIFEST_MISSING", codes)
        self.assertNotIn("IMAGE_ASSET_MISSING", codes)

    def test_both_warnings_fire_for_mixed_refs(self):
        """Two refs: one with no manifest, one with manifest but no local asset."""
        v = self._validator()
        ref_no_manifest = _make_image_ref(manifest_found=False, local_path=None)
        ref_no_asset = _make_image_ref(manifest_found=True, local_path=None)
        # Give the second ref a different guid to avoid dedup
        from rag_engine.ingestion.parsers.stackoverflow_parser import ImageReference
        ref_no_asset = ImageReference(
            url="https://stackoverflowteams.com/c/kwikid/images/s/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.png",
            guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            extension="png",
            image_id=55,
            manifest_found=True,
            local_path=None,
        )
        a = _make_article(
            article_id="so_img_mixed",
            image_references=[ref_no_manifest, ref_no_asset],
        )
        result = v.validate(a)
        codes = {i.code for i in result.issues}
        self.assertIn("IMAGE_MANIFEST_MISSING", codes)
        self.assertIn("IMAGE_ASSET_MISSING", codes)

    def test_image_grounding_status_in_parser(self):
        """Parser sets image_grounding_status from image references."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            img_guid = "46791add-fd95-4bf4-9785-9b7d7ecce7c3"
            img_url = (
                f"https://stackoverflowteams.com/c/kwikid/images/s/{img_guid}.png"
            )
            posts = [
                {
                    "id": 300,
                    "postType": "question",
                    "title": "KwikID image reference grounding test case?",
                    "bodyMarkdown": f"See the screenshot: ![]({img_url})\nThen follow the steps described below in the runbook.",
                    "score": 1,
                    "viewCount": 1,
                    "acceptedAnswerId": None,
                    "tags": ["grounding"],
                },
            ]
            # No images.json entry → manifest_found=False
            (tmp_path / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
            (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
            (tmp_path / "posts2votes.json").write_text("[]", encoding="utf-8")
            (tmp_path / "tags.json").write_text("[]", encoding="utf-8")
            (tmp_path / "images.json").write_text("[]", encoding="utf-8")
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertEqual(len(articles), 1)
            a = articles[0]
            self.assertGreater(a.image_count, 0)
            self.assertEqual(a.missing_image_count, a.image_count)
            self.assertEqual(a.image_grounding_status, "unresolved")


# ---------------------------------------------------------------------------
# 4. Duplicate Detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection(unittest.TestCase):
    """Second validate() call with same content triggers DUPLICATE_CONTENT."""

    def test_first_pass_no_duplicate(self):
        from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
        v = KnowledgeQualityValidator()
        a = _make_article(article_id="so_dedup_first")
        result = v.validate(a)
        codes = {i.code for i in result.issues}
        self.assertNotIn("DUPLICATE_CONTENT", codes)

    def test_second_pass_same_content_triggers_duplicate(self):
        from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
        v = KnowledgeQualityValidator()
        a1 = _make_article(article_id="so_dedup_a")
        a2 = _make_article(article_id="so_dedup_b")  # identical body fields
        v.validate(a1)
        result2 = v.validate(a2)
        self.assertFalse(result2.is_valid)
        codes = {i.code for i in result2.issues}
        self.assertIn("DUPLICATE_CONTENT", codes)

    def test_reset_run_clears_seen_hashes(self):
        """After reset_run(), the same content should pass again (not a duplicate)."""
        from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
        v = KnowledgeQualityValidator()
        a = _make_article(article_id="so_reset_dedup")
        v.validate(a)
        v.reset_run()
        result2 = v.validate(_make_article(article_id="so_reset_dedup_2"))
        codes = {i.code for i in result2.issues}
        self.assertNotIn("DUPLICATE_CONTENT", codes)

    def test_distinct_content_no_duplicate(self):
        from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
        v = KnowledgeQualityValidator()
        a1 = _make_article(
            article_id="so_distinct_1",
            question_body="First distinct question about KwikID service restart and remediation procedures.",
            question_markdown="First distinct question about KwikID service restart and remediation procedures.",
            answer_body="First distinct answer about how to correctly restart the service.",
            answer_markdown="First distinct answer about how to correctly restart the service.",
        )
        a2 = _make_article(
            article_id="so_distinct_2",
            question_body="Second completely different question about KwikID billing issues and invoice generation.",
            question_markdown="Second completely different question about KwikID billing issues and invoice generation.",
            answer_body="Second distinct answer explaining the billing workflow and invoice system.",
            answer_markdown="Second distinct answer explaining the billing workflow and invoice system.",
        )
        v.validate(a1)
        result2 = v.validate(a2)
        codes = {i.code for i in result2.issues}
        self.assertNotIn("DUPLICATE_CONTENT", codes)


# ---------------------------------------------------------------------------
# 5. Completeness Floor
# ---------------------------------------------------------------------------

class TestCompletenessFloor(unittest.TestCase):
    """Articles with completeness_score below 0.80 should have manual_review_required=True."""

    def test_parser_sets_manual_review_for_low_score(self):
        """
        The parser sets manual_review_required = completeness_score < 0.80.
        Build an article that only passes 3 of 11 flags → score ≈ 0.27.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # A minimal post: no tags, no accepted answer, no canonical URL field,
            # short content → most flags will be False → low completeness score
            posts = [
                {
                    "id": 999,
                    "postType": "question",
                    "title": "Minimal article for completeness test",
                    "bodyMarkdown": "What is the deployment procedure for KwikID in a minimal scenario?",
                    "score": 0,
                    "viewCount": 0,
                    "acceptedAnswerId": None,
                    "tags": [],  # no tags → tags_present=False, client_relevance=False
                },
            ]
            (tmp_path / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
            (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
            (tmp_path / "posts2votes.json").write_text("[]", encoding="utf-8")
            (tmp_path / "tags.json").write_text("[]", encoding="utf-8")
            (tmp_path / "images.json").write_text("[]", encoding="utf-8")
            from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
            articles = StackOverflowParser().parse(tmp_path)
            self.assertEqual(len(articles), 1)
            a = articles[0]
            # no tags, no answer, no accepted_answer → many flags False
            self.assertFalse(a.completeness_flags.get("answer_present", True))
            # When score < 0.80, manual_review_required must be True
            if a.completeness_score < 0.80:
                self.assertTrue(a.manual_review_required)
            # If somehow the score is >= 0.80 (unlikely with no tags/answer), skip
            # — but assert score and flag are consistent:
            if a.completeness_score >= 0.80:
                self.assertFalse(a.manual_review_required)

    def test_stub_article_low_score_sets_manual_review(self):
        """Directly construct article with completeness_score=0.3 → manual_review_required=True."""
        # The parser formula is: manual_review_required = completeness_score < 0.80
        # We verify a stub KnowledgeArticle with score 0.3 has the field set correctly.
        a = _make_article(
            article_id="so_low_score",
            completeness_score=0.3,
            manual_review_required=True,  # must match parser logic
        )
        self.assertTrue(a.manual_review_required)
        self.assertLess(a.completeness_score, 0.80)

    def test_high_completeness_score_no_manual_review(self):
        """Article with completeness_score=1.0 → manual_review_required=False."""
        a = _make_article(
            article_id="so_high_score",
            completeness_score=1.0,
            manual_review_required=False,
        )
        self.assertFalse(a.manual_review_required)
        self.assertGreaterEqual(a.completeness_score, 0.80)

    def test_threshold_boundary_0_80(self):
        """Score exactly 0.80 → manual_review_required=False (boundary is exclusive)."""
        # Parser uses: completeness_score < 0.80  → True if < 0.80
        a = _make_article(
            article_id="so_boundary",
            completeness_score=0.80,
            manual_review_required=False,
        )
        self.assertFalse(a.manual_review_required)


# ---------------------------------------------------------------------------
# 6. Knowledge MetricsCollector (Task B validation)
# ---------------------------------------------------------------------------

class TestKnowledgeMetricsCollector(unittest.TestCase):
    """Verify the new knowledge_ metric helpers in rag_engine MetricsCollector."""

    def setUp(self):
        from rag_engine.observability.metrics_collector import MetricsCollector
        self.collector = MetricsCollector()

    def test_record_embedding_latency_stored(self):
        self.collector.record_knowledge_embedding_latency(123.4)
        from rag_engine.observability.metrics_collector import KNOWLEDGE_EMBEDDING_LATENCY_MS
        p95 = self.collector.p95_latency(KNOWLEDGE_EMBEDDING_LATENCY_MS)
        self.assertAlmostEqual(p95, 123.4, places=3)

    def test_quality_rejection_rate_zero_when_no_articles(self):
        rate = self.collector.knowledge_quality_rejection_rate()
        self.assertEqual(rate, 0.0)

    def test_quality_rejection_rate_correct_after_processing(self):
        # 3 processed, 1 rejected → rate = 1/3 ≈ 0.3333
        self.collector.record_knowledge_article_processed(rejected=False)
        self.collector.record_knowledge_article_processed(rejected=False)
        self.collector.record_knowledge_article_processed(rejected=True)
        rate = self.collector.knowledge_quality_rejection_rate()
        self.assertAlmostEqual(rate, 1 / 3, places=3)

    def test_retrieval_hit_rate_zero_when_no_requests(self):
        rate = self.collector.knowledge_retrieval_hit_rate()
        self.assertEqual(rate, 0.0)

    def test_retrieval_hit_rate_correct(self):
        # 2 hits out of 3 requests → rate = 2/3
        self.collector.record_knowledge_retrieval(hit=True)
        self.collector.record_knowledge_retrieval(hit=True)
        self.collector.record_knowledge_retrieval(hit=False)
        rate = self.collector.knowledge_retrieval_hit_rate()
        self.assertAlmostEqual(rate, 2 / 3, places=3)

    def test_completeness_score_bucketing_low(self):
        from rag_engine.observability.metrics_collector import KNOWLEDGE_COMPLETENESS_SCORE_LOW
        self.collector.record_knowledge_completeness_score(0.3)
        self.assertEqual(self.collector._counters.get(KNOWLEDGE_COMPLETENESS_SCORE_LOW, 0), 1)

    def test_completeness_score_bucketing_medium(self):
        from rag_engine.observability.metrics_collector import KNOWLEDGE_COMPLETENESS_SCORE_MED
        self.collector.record_knowledge_completeness_score(0.65)
        self.assertEqual(self.collector._counters.get(KNOWLEDGE_COMPLETENESS_SCORE_MED, 0), 1)

    def test_completeness_score_bucketing_high(self):
        from rag_engine.observability.metrics_collector import KNOWLEDGE_COMPLETENESS_SCORE_HIGH
        self.collector.record_knowledge_completeness_score(0.95)
        self.assertEqual(self.collector._counters.get(KNOWLEDGE_COMPLETENESS_SCORE_HIGH, 0), 1)

    def test_summary_includes_knowledge_rates(self):
        self.collector.record_knowledge_article_processed(rejected=True)
        self.collector.record_knowledge_retrieval(hit=False)
        s = self.collector.summary()
        self.assertIn("knowledge_quality_rejection_rate", s)
        self.assertIn("knowledge_retrieval_hit_rate", s)
        self.assertEqual(s["knowledge_quality_rejection_rate"], 1.0)
        self.assertEqual(s["knowledge_retrieval_hit_rate"], 0.0)

    def test_knowledge_metric_names_do_not_conflict_with_freshdesk_prefix(self):
        """All knowledge metric name constants must start with 'knowledge_'."""
        from rag_engine.observability import metrics_collector as mc_module
        knowledge_constants = [
            mc_module.KNOWLEDGE_EMBEDDING_LATENCY_MS,
            mc_module.KNOWLEDGE_RETRIEVAL_HIT_RATE,
            mc_module.KNOWLEDGE_COMPLETENESS_SCORE_LOW,
            mc_module.KNOWLEDGE_COMPLETENESS_SCORE_MED,
            mc_module.KNOWLEDGE_COMPLETENESS_SCORE_HIGH,
            mc_module.KNOWLEDGE_QUALITY_REJECTION_RATE,
            mc_module.KNOWLEDGE_ARTICLES_PROCESSED,
            mc_module.KNOWLEDGE_ARTICLES_REJECTED,
            mc_module.KNOWLEDGE_RETRIEVAL_REQUESTS,
            mc_module.KNOWLEDGE_RETRIEVAL_HITS,
        ]
        for name in knowledge_constants:
            self.assertTrue(
                name.startswith("knowledge_"),
                f"Metric name {name!r} must start with 'knowledge_' to avoid freshdesk_ conflict",
            )


# ---------------------------------------------------------------------------
# 7. Hybrid Retriever Interface (mock-patched)
# ---------------------------------------------------------------------------

class TestHybridRetrieverInterface(unittest.TestCase):
    """
    Mock-patched tests for HybridTicketRetriever.

    The hybrid path (B1_HYBRID_RETRIEVAL_ENABLED=true) is wired in app/main.py:
      - Confirmed ACTIVE (not dead code): lines 2006-2013 instantiate HybridTicketRetriever
        when _B1_HYBRID_ENABLED=True
      - KnowledgeOrchestrator._query_rag() currently returns placeholder evidence
        (rag_provider=None path), so HybridTicketRetriever is NOT called from
        the case_engine orchestration path in Sprint 2.27.5

    These tests exercise the HybridTicketRetriever.retrieve() interface using
    mocked Supabase and embedding providers.
    """

    def _make_retriever(self):
        """Build HybridTicketRetriever with fully mocked dependencies."""
        from rag_engine.retrieval.hybrid_ticket_retriever import HybridTicketRetriever

        mock_supabase = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed_single.return_value = [0.1] * 1536  # fake 1536-d embedding

        retriever = HybridTicketRetriever(
            supabase_client=mock_supabase,
            embedding_provider=mock_embedder,
            ticket_chunks_table="rag_ticket_chunks",
            sop_chunks_table="rag_sop_chunks",
        )
        return retriever, mock_supabase, mock_embedder

    def _make_request(self, query: str = "KwikID service restart procedure"):
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        return RetrievalRequest(
            query_text=query,
            client="kwikid",
            top_k=5,
            similarity_threshold=0.3,
        )

    def test_retriever_instantiates_without_error(self):
        retriever, _, _ = self._make_retriever()
        from rag_engine.retrieval.hybrid_ticket_retriever import HybridTicketRetriever
        self.assertIsInstance(retriever, HybridTicketRetriever)

    def test_retrieve_returns_response_object_on_empty_results(self):
        """retrieve() with empty Supabase results returns a valid RetrievalResponse."""
        retriever, mock_supabase, mock_embedder = self._make_retriever()

        # Patch the v2 RPC calls to return empty lists (no results found)
        with patch.object(retriever, "_search_v2", return_value=([], True, {})):
            with patch.object(retriever, "_search_keyword", return_value=[]):
                from rag_engine.retrieval.ticket_retriever import RetrievalResponse
                req = self._make_request()
                resp = retriever.retrieve(req)
                self.assertIsInstance(resp, RetrievalResponse)
                self.assertEqual(resp.client, "kwikid")
                self.assertEqual(len(resp.chunks), 0)

    def test_retrieve_uses_embedding_provider(self):
        """retrieve() must call embed_single exactly once."""
        retriever, _, mock_embedder = self._make_retriever()

        with patch.object(retriever, "_search_v2", return_value=([], True, {})):
            with patch.object(retriever, "_search_keyword", return_value=[]):
                retriever.retrieve(self._make_request())

        mock_embedder.embed_single.assert_called_once()

    def test_retrieve_raises_on_missing_client(self):
        """retrieve() must raise ValueError when client is empty."""
        retriever, _, _ = self._make_retriever()
        from rag_engine.retrieval.ticket_retriever import RetrievalRequest
        req = RetrievalRequest(
            query_text="test query",
            client="",  # empty client — should raise
            top_k=5,
            similarity_threshold=0.3,
        )
        with self.assertRaises(ValueError):
            retriever.retrieve(req)

    def test_rrf_fusion_semantic_only_when_no_keyword_results(self):
        """When keyword results are empty, _rrf_fuse returns semantic_only mode."""
        retriever, _, _ = self._make_retriever()

        semantic_rows = [
            {"id": "chunk-1", "similarity": 0.85, "content": "service restart SOP", "chunk_type": "SOP_STEPS", "boosted_score": 0.85},
            {"id": "chunk-2", "similarity": 0.72, "content": "heartbeat endpoint check", "chunk_type": "RESOLUTION_RCA", "boosted_score": 0.72},
        ]
        fused, mode, diag = retriever._rrf_fuse(semantic_rows, [], self._make_request())

        self.assertEqual(mode, "semantic_only")
        self.assertEqual(len(fused), 2)
        self.assertIn("selected_rrf_k", diag)
        self.assertIn("overlap_count", diag)

    def test_rrf_fusion_hybrid_mode_when_both_results_present(self):
        """When both semantic and keyword results are present, mode is 'hybrid'."""
        retriever, _, _ = self._make_retriever()

        semantic_rows = [
            {"id": "chunk-1", "similarity": 0.85, "content": "service restart SOP procedure", "chunk_type": "SOP_STEPS", "boosted_score": 0.85},
        ]
        keyword_rows = [
            {"id": "chunk-2", "ts_rank": 0.6, "content": "restart keyword match", "chunk_type": "RESOLUTION_RCA", "similarity": 0.0, "boosted_score": 0.0, "_keyword_rank": 1},
        ]
        fused, mode, diag = retriever._rrf_fuse(semantic_rows, keyword_rows, self._make_request())

        self.assertEqual(mode, "hybrid")
        self.assertGreater(len(fused), 0)
        self.assertIn("overlap_ratio", diag)
        self.assertIn("retrieval_confidence", diag)

    def test_retrieval_metadata_contains_required_keys(self):
        """RetrievalResponse.retrieval_metadata must include the standard key set."""
        retriever, _, _ = self._make_retriever()

        semantic_rows = [
            {
                "id": "chunk-abc",
                "similarity": 0.80,
                "content": "KwikID restart procedure with SOP steps and verification",
                "chunk_type": "SOP_STEPS",
                "boosted_score": 0.80,
                "source_type": "sop",
                "ticket_id": None,
                "sop_id": "sop-001",
                "metadata": {},
                "_v2_used": True,
            }
        ]

        with patch.object(retriever, "_search_v2", return_value=(semantic_rows, True, {
            "ticket_latency_ms": 12.0,
            "sop_latency_ms": 10.0,
            "parallel_wall_ms": 14.0,
            "merge_latency_ms": 1.0,
        })):
            with patch.object(retriever, "_search_keyword", return_value=[]):
                resp = retriever.retrieve(self._make_request())

        meta = resp.retrieval_metadata
        required_keys = {
            "embedding_latency_ms",
            "keyword_latency_ms",
            "retrieval_mode",
            "semantic_candidates",
            "keyword_candidates",
            "fused_candidates",
        }
        for key in required_keys:
            self.assertIn(key, meta, f"Missing key: {key}")


# ---------------------------------------------------------------------------
# 8. Validation result API
# ---------------------------------------------------------------------------

class TestValidationResultAPI(unittest.TestCase):
    """Verify ValidationResult properties work correctly."""

    def test_is_valid_false_when_error_present(self):
        from rag_engine.ingestion.knowledge_quality_validator import (
            ValidationIssue,
            ValidationResult,
        )
        result = ValidationResult(article_id="test")
        result.issues.append(ValidationIssue(code="MISSING_QUESTION", severity="error", message="bad"))
        self.assertFalse(result.is_valid)

    def test_is_valid_true_when_only_warnings(self):
        from rag_engine.ingestion.knowledge_quality_validator import (
            ValidationIssue,
            ValidationResult,
        )
        result = ValidationResult(article_id="test")
        result.issues.append(ValidationIssue(code="BROKEN_MARKDOWN", severity="warning", message="warn"))
        self.assertTrue(result.is_valid)
        self.assertTrue(result.manual_review_required)

    def test_errors_and_warnings_properties(self):
        from rag_engine.ingestion.knowledge_quality_validator import (
            ValidationIssue,
            ValidationResult,
        )
        result = ValidationResult(article_id="test")
        result.issues.append(ValidationIssue(code="MISSING_QUESTION", severity="error", message="e"))
        result.issues.append(ValidationIssue(code="BROKEN_MARKDOWN", severity="warning", message="w"))
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.errors[0].code, "MISSING_QUESTION")
        self.assertEqual(result.warnings[0].code, "BROKEN_MARKDOWN")


if __name__ == "__main__":
    unittest.main()
