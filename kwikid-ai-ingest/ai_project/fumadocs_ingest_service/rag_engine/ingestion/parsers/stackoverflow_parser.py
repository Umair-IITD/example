"""
rag_engine/ingestion/parsers/stackoverflow_parser.py

Parse a Stack Overflow for Teams JSON export (More_data/) into KnowledgeArticle
objects ready for downstream classification, PII redaction, and embedding.

Export files expected:
  posts.json          — questions + answers (1,639 posts in Think360 export)
  comments.json       — 192 comments keyed by postId
  posts2votes.json    — 838 vote records keyed by postId
  tags.json           — 484 tags with usage counts
  (users / badges files are ignored — never embedded)

Output:
  list[KnowledgeArticle] — one per question, ALL answers included

Design decisions:
  - Defensive parsing: all dict access uses .get() with safe defaults
  - Every field is explicitly validated before use
  - No exception propagates from _parse_single_post — errors are counted
  - All answers included: accepted first, then by score descending, separated by ---
  - Comments included for question AND all answers (no cap — corpus has 192 total)
  - Image classification and OCR metadata are attached to every ImageReference
    (OCR text is populated later during migration ingestion, not here)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

_STACK_BASE_URL = "https://stackoverflowteams.com/c/kwikid"
_IMAGE_URL_RE = re.compile(
    r"https?://stackoverflowteams\.com/c/[^/]+/images/s/"
    r"([0-9a-fA-F\-]{32,36})\.(png|jpg|jpeg|gif|webp)",
    flags=re.IGNORECASE,
)
_GENERIC_TAGS = {
    "stackoverflow-for-teams",
    "sop",
    "runbook",
    "faq",
    "howto",
    "help",
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

# Image classification labels (deterministic, context-based)
IMAGE_CLASS_WHATSAPP_CHAT = "WHATSAPP_CHAT"
IMAGE_CLASS_FLOWCHART     = "FLOWCHART"
IMAGE_CLASS_STACKTRACE    = "STACKTRACE"
IMAGE_CLASS_ERROR_DIALOG  = "ERROR_DIALOG"
IMAGE_CLASS_CONFIG_SCREEN = "CONFIG_SCREEN"
IMAGE_CLASS_TABLE         = "TABLE"
IMAGE_CLASS_SCREENSHOT    = "SCREENSHOT"
IMAGE_CLASS_UI_SCREEN     = "UI_SCREEN"
IMAGE_CLASS_TEXT_ONLY     = "TEXT_ONLY"
IMAGE_CLASS_OTHER         = "OTHER"


@dataclass
class ImageOCRMetadata:
    """
    Structured metadata produced for every image reference, whether or not
    OCR was executed.  ocr_text is populated at migration-ingest time when
    ocr_required=True AND the local PNG file is present.
    """
    image_guid:      str
    image_path:      str            # local path as string; "" when unavailable
    image_class:     str            # one of IMAGE_CLASS_* constants above
    ocr_required:    bool
    ocr_text:        Optional[str]  # None until OCR is executed
    ocr_confidence:  Optional[float]
    ocr_version:     str            # "rapidocr-1.0" | "none"

    def to_dict(self) -> dict:
        return {
            "image_guid":     self.image_guid,
            "image_path":     self.image_path,
            "image_class":    self.image_class,
            "ocr_required":   self.ocr_required,
            "ocr_text":       self.ocr_text,
            "ocr_confidence": self.ocr_confidence,
            "ocr_version":    self.ocr_version,
        }


@dataclass
class ImageReference:
    url: str
    guid: str
    extension: str
    image_id: Optional[int]
    manifest_found: bool
    local_path: Optional[str]
    # Populated by _extract_image_references after classification + OCR decision
    ocr_metadata: Optional[ImageOCRMetadata] = None


@dataclass
class KnowledgeArticle:
    """One Q&A article ready for classification and embedding."""
    article_id:         str                # "so_{question_post_id}"
    source:             str                # "stackoverflow_for_teams"
    source_post_id:     int                # question post ID
    article_type:       str                # "qa_pair" | "question_only"
    canonical_url:      str

    title:              str
    question_body:      str                # PII-redacted question text
    answer_body:        Optional[str]      # PII-redacted answer text (None if no answer)
    question_markdown:  str
    answer_markdown:    Optional[str]
    answer_post_id:     Optional[int]

    answer_score:       int
    question_score:     int
    view_count:         int
    accepted_answer_id: Optional[int]

    tags_raw:           list[str]          # original SO tags
    clients:            list[str]          # mapped tenant slugs (set by TenantMapper later)
    image_references:   list[ImageReference]
    completeness_flags: dict[str, bool]
    completeness_score: float
    manual_review_required: bool

    # Image grounding counters (WORK ITEM 1)
    image_count:          int              # total image references found
    resolved_image_count: int              # refs with manifest_found=True
    missing_image_count:  int             # refs with manifest_found=False
    image_grounding_status: str            # "no_images" | "fully_resolved" | "partially_resolved" | "unresolved"

    # Knowledge safety classification (WORK ITEM 5)
    safety_classification: str            # "safe_for_llm" | "restricted_internal" | "sensitive_operations"

    # OCR / image classification metadata (one dict per image in image_references)
    image_metadata: list[dict]            # serialised ImageOCRMetadata.to_dict()

    post_state:         str                # "Published" | "Deleted" | etc.
    created_at_source:  Optional[str]      # ISO datetime string from SO


@dataclass
class StackOverflowExport:
    """Holds the fully loaded JSON data from one More_data/ directory."""
    posts:       list[dict]
    comments:    list[dict]
    votes:       list[dict]
    tags:        list[dict]
    images:      list[dict]

    # Derived lookup maps (built in __post_init__)
    answer_map:  dict[int, list[dict]]  = field(default_factory=dict, init=False)
    vote_map:    dict[int, int]         = field(default_factory=dict, init=False)
    comment_map: dict[int, list[dict]]  = field(default_factory=dict, init=False)
    image_map:   dict[str, dict]        = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._build_maps()

    def _build_maps(self) -> None:
        """Build efficient lookup dicts from raw lists."""
        # answer_map: question_id → [answer_post, ...]
        for post in self.posts:
            if post.get("postType") == "answer":
                parent_id = post.get("parentId")
                if isinstance(parent_id, int):
                    self.answer_map.setdefault(parent_id, []).append(post)

        # vote_map: post_id → vote_count (sum of all vote VoteType=2 upvotes)
        for vote in self.votes:
            post_id = vote.get("postId")
            if isinstance(post_id, int):
                self.vote_map[post_id] = self.vote_map.get(post_id, 0) + 1

        # comment_map: parent_post_id → [comment, ...]
        for comment in self.comments:
            parent_id = comment.get("postId")
            if isinstance(parent_id, int):
                self.comment_map.setdefault(parent_id, []).append(comment)

        for image in self.images:
            guid = str(image.get("imageGuid", "")).strip().lower()
            if guid:
                self.image_map[guid] = image


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class StackOverflowParser:
    """
    Parses a More_data/ directory export into a list of KnowledgeArticle objects.

    Usage:
        parser = StackOverflowParser()
        articles = parser.parse(Path("../More_data"))
        print(f"Parsed {len(articles)} articles")
    """

    # Minimum comment length to include (filters "thanks!" noise)
    _MIN_COMMENT_CHARS = 20

    # Cached OCR engine — loaded once per process, reused for all images.
    # RapidOCR loads ONNX models on first instantiation (~2-3s); reloading
    # it per image multiplies that cost by the image count (1,255 images →
    # ~9 hours). This singleton keeps models in memory across all parse calls.
    _ocr_engine: Optional["RapidOCR"] = None  # type: ignore[name-defined]

    def parse(self, data_dir: Path) -> list[KnowledgeArticle]:
        """
        Load all JSON files and return KnowledgeArticle objects for each question.

        Only question posts (postType == "question") produce articles.
        Deleted posts and posts without title are silently skipped.
        """
        export = self._load_export(data_dir)
        if export is None:
            return []

        questions = [
            p for p in export.posts
            if p.get("postType") == "question"
        ]
        LOGGER.info(
            "StackOverflowParser: %d total posts, %d questions found in %s",
            len(export.posts), len(questions), data_dir,
        )

        articles: list[KnowledgeArticle] = []
        errors = 0
        for post in questions:
            try:
                article = self._parse_single_post(post, export, data_dir)
                if article is not None:
                    articles.append(article)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                LOGGER.warning(
                    "StackOverflowParser: failed to parse post id=%s: %s",
                    post.get("id", "?"), exc,
                )

        LOGGER.info(
            "StackOverflowParser: %d articles parsed, %d skipped/failed",
            len(articles), len(questions) - len(articles) + errors,
        )
        return articles

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_export(self, data_dir: Path) -> Optional[StackOverflowExport]:
        """Load export JSON files. images.json is optional but preferred."""
        required = {
            "posts":    "posts.json",
            "comments": "comments.json",
            "votes":    "posts2votes.json",
            "tags":     "tags.json",
        }
        optional = {"images": "images.json"}
        loaded: dict[str, list] = {}
        for key, filename in required.items():
            path = data_dir / filename
            if not path.exists():
                LOGGER.error("StackOverflowParser: required file not found: %s", path)
                return None
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                # Some exports wrap in {"items": [...]}; handle both
                if isinstance(raw, dict):
                    raw = raw.get("items", list(raw.values())[0] if raw else [])
                loaded[key] = raw if isinstance(raw, list) else []
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("StackOverflowParser: failed to read %s: %s", path, exc)
                return None

        for key, filename in optional.items():
            path = data_dir / filename
            if not path.exists():
                loaded[key] = []
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw = raw.get("items", list(raw.values())[0] if raw else [])
                loaded[key] = raw if isinstance(raw, list) else []
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("StackOverflowParser: failed to read optional %s: %s", path, exc)
                loaded[key] = []

        return StackOverflowExport(
            posts    = loaded["posts"],
            comments = loaded["comments"],
            votes    = loaded["votes"],
            tags     = loaded["tags"],
            images   = loaded["images"],
        )

    def _parse_single_post(
        self,
        question: dict,
        export: StackOverflowExport,
        data_dir: Path,
    ) -> Optional[KnowledgeArticle]:
        """Build a KnowledgeArticle from a single question post + its best answer."""
        post_id    = question.get("id")
        post_state = str(question.get("postState", "Published"))
        title      = _normalize_markdown(str(question.get("title", ""))).strip()
        body_md    = _normalize_markdown(str(question.get("bodyMarkdown", "") or "")).strip()
        body_html  = _normalize_markdown(_clean_text(str(question.get("body", "")))).strip()
        question_markdown = body_md or body_html

        if not isinstance(post_id, int):
            return None  # Malformed post

        if not title:
            return None  # No content — skip

        q_score     = int(question.get("score", 0))
        view_count  = int(question.get("viewCount", 0))
        accepted_id = question.get("acceptedAnswerId")
        if isinstance(accepted_id, int) and accepted_id <= 0:
            accepted_id = None
        tags_raw    = _parse_tags(question)

        # Include question-level comments in question body
        q_comments = export.comment_map.get(post_id, [])
        q_comment_text = self._format_comments(q_comments)
        if q_comment_text:
            question_markdown = question_markdown + q_comment_text

        # Collect ALL valid answers sorted: accepted first, then score desc, then ID
        all_answers = export.answer_map.get(post_id, [])
        valid_answers = sorted(
            [a for a in all_answers if a.get("postState", "Published") != "Deleted"],
            key=lambda a: (
                1 if (accepted_id is not None and a.get("id") == accepted_id) else 0,
                int(a.get("score", 0)),
                int(a.get("id", 0)),
            ),
            reverse=True,
        )

        answer_body: Optional[str] = None
        answer_markdown: Optional[str] = None
        answer_post_id: Optional[int] = None
        answer_score: int = 0
        answer_parts: list[str] = []
        answer_md_parts: list[str] = []

        for i, ans in enumerate(valid_answers):
            ans_md   = _normalize_markdown(str(ans.get("bodyMarkdown", "") or "")).strip()
            ans_html = _normalize_markdown(_clean_text(str(ans.get("body", "")))).strip()
            a_text   = ans_md or ans_html
            if not a_text:
                continue

            # Include all comments for this specific answer
            ans_comments = export.comment_map.get(ans.get("id", -1), [])
            comment_text = self._format_comments(ans_comments)
            if comment_text:
                a_text = a_text + comment_text

            answer_md_parts.append(ans_md or a_text)

            if i == 0:
                # Primary answer: carry metadata for article record
                answer_post_id = ans.get("id")
                answer_score   = int(ans.get("score", 0))
                answer_parts.append(a_text)
            else:
                score  = int(ans.get("score", 0))
                header = f"Additional Answer (score={score}):" if score > 0 else "Additional Answer:"
                answer_parts.append(f"{header}\n{a_text}")

        if answer_parts:
            answer_body     = "\n\n---\n\n".join(answer_parts)
            answer_markdown = "\n\n".join(answer_md_parts)

        article_type = "qa_pair" if answer_body else "question_only"
        canonical_url = _extract_canonical_url(question, post_id)

        # Build a context string that includes title and tags for image classification
        # (Rules 8 uses title/tag keywords not always present in bodyMarkdown alone)
        title_and_tags = f"{title} {' '.join(tags_raw)}"
        image_references = _extract_image_references(
            question_markdown,
            answer_markdown or answer_body or "",
            export.image_map,
            data_dir,
            title_and_tags=title_and_tags,
            parser=self,
        )
        completeness_flags = _build_completeness_flags(
            question_markdown=question_markdown,
            answer_markdown=answer_markdown,
            accepted_answer_id=accepted_id,
            image_refs=image_references,
            tags_raw=tags_raw,
            canonical_url=canonical_url,
            answer_body=answer_body,
        )
        completeness_score = _score_completeness(completeness_flags)

        # Image grounding counters (deterministic, derived from image_references)
        image_count          = len(image_references)
        resolved_image_count = sum(1 for r in image_references if r.manifest_found)
        missing_image_count  = image_count - resolved_image_count
        if image_count == 0:
            image_grounding_status = "no_images"
        elif missing_image_count == 0:
            image_grounding_status = "fully_resolved"
        elif resolved_image_count == 0:
            image_grounding_status = "unresolved"
        else:
            image_grounding_status = "partially_resolved"

        # Knowledge safety classification (deterministic, WORK ITEM 5)
        safety_classification = _classify_safety(
            title=title,
            question_markdown=question_markdown,
            answer_markdown=answer_markdown or "",
        )

        # Serialise OCR metadata for every image reference
        image_metadata = [
            ref.ocr_metadata.to_dict()
            for ref in image_references
            if ref.ocr_metadata is not None
        ]

        return KnowledgeArticle(
            article_id         = f"so_{post_id}",
            source             = "stackoverflow_for_teams",
            source_post_id     = post_id,
            article_type       = article_type,
            canonical_url      = canonical_url,
            title              = title,
            question_body      = question_markdown,
            answer_body        = answer_body,
            question_markdown  = question_markdown,
            answer_markdown    = answer_markdown,
            answer_post_id     = answer_post_id,
            answer_score       = answer_score,
            question_score     = q_score,
            view_count         = view_count,
            accepted_answer_id = accepted_id,
            tags_raw           = tags_raw,
            clients            = [],   # populated by TenantMapper
            image_references   = image_references,
            completeness_flags = completeness_flags,
            completeness_score = completeness_score,
            manual_review_required = completeness_score < 0.80,
            image_count          = image_count,
            resolved_image_count = resolved_image_count,
            missing_image_count  = missing_image_count,
            image_grounding_status = image_grounding_status,
            safety_classification  = safety_classification,
            image_metadata         = image_metadata,
            post_state         = post_state,
            created_at_source  = question.get("creationDate"),
        )

    # ── Image classification & OCR ─────────────────────────────────────────────

    @staticmethod
    def _classify_image(guid: str, alt_text: str, body_markdown: str) -> str:
        """
        Deterministic image classification using 10 ordered rules.

        Rules are evaluated in priority order; the first match wins.
        All matching is case-insensitive.

        Args:
            guid:          Image GUID (used for tie-breaking logging only).
            alt_text:      Alt-text extracted from the ![alt](url) markdown.
            body_markdown: Full bodyMarkdown of the post containing the image.

        Returns:
            One of the IMAGE_CLASS_* string constants.
        """
        alt  = (alt_text or "").lower()
        body = (body_markdown or "").lower()

        # Rule 1: WhatsApp chat
        if "whatsapp" in alt or "chat" in alt or "whatsapp" in body:
            return IMAGE_CLASS_WHATSAPP_CHAT

        # Rule 2: Flow diagram / chart
        if "flow" in alt or (
            "flow" in body and ("diagram" in body or "chart" in body or "steps" in body)
        ):
            return IMAGE_CLASS_FLOWCHART

        # Rule 3: Stack trace / traceback / exception
        if "stack" in body and "trace" in body:
            return IMAGE_CLASS_STACKTRACE
        if "traceback" in body or "exception" in body:
            return IMAGE_CLASS_STACKTRACE

        # Rule 4: Error dialog (error keyword + very short body = image IS the error)
        if "error" in body and len(body_markdown.strip()) < 100:
            return IMAGE_CLASS_ERROR_DIALOG

        # Rule 5: Configuration screen
        if "config" in body or "configuration" in body or "setting" in body:
            return IMAGE_CLASS_CONFIG_SCREEN

        # Rule 6: Table / structured data
        if "table" in body or "columns" in body or "rows" in body or "schema" in body:
            return IMAGE_CLASS_TABLE

        # Rule 7: Image is the entire answer (extremely short body)
        # Strip image markdown syntax before measuring length
        body_no_img = re.sub(
            r"!\[[^\]]*\]\([^)]+\)", "", body_markdown, flags=re.IGNORECASE
        ).strip()
        if len(body_no_img) < 30:
            return IMAGE_CLASS_SCREENSHOT

        # Rule 8: UI / portal / dashboard labels in title/tags (passed via body_markdown
        # augmented by caller — see _extract_image_references() title_and_tags param)
        ui_keywords = {"ui", "portal", "screen", "dashboard", "frontend"}
        if any(kw in body for kw in ui_keywords):
            return IMAGE_CLASS_UI_SCREEN

        # Rule 9: Long body with image inline → illustrative screenshot
        if len(body_markdown.strip()) > 200:
            return IMAGE_CLASS_SCREENSHOT

        # Rule 10: default
        return IMAGE_CLASS_OTHER

    @staticmethod
    def _should_ocr(image_class: str, body_markdown_len: int) -> bool:
        """
        Deterministic OCR decision based on image_class and surrounding text volume.

        Returns True if OCR should be executed for this image, False otherwise.
        """
        always_yes = {
            IMAGE_CLASS_WHATSAPP_CHAT,
            IMAGE_CLASS_STACKTRACE,
            IMAGE_CLASS_ERROR_DIALOG,
            IMAGE_CLASS_CONFIG_SCREEN,
            IMAGE_CLASS_TABLE,
            IMAGE_CLASS_FLOWCHART,
            IMAGE_CLASS_TEXT_ONLY,
        }
        if image_class in always_yes:
            return True

        if image_class == IMAGE_CLASS_SCREENSHOT:
            # Short body → image is primary content → OCR required
            return body_markdown_len < 100

        # UI_SCREEN and OTHER → no OCR
        return False

    @staticmethod
    def _run_ocr(local_path: str, guid: str, image_class: str) -> ImageOCRMetadata:
        """
        Run OCR on the image at local_path using RapidOCR.

        Degrades gracefully:
          - If rapidocr-onnxruntime is not installed → ocr_text=None
          - If file is unreadable or OCR crashes → ocr_text=None
        Never raises.

        Args:
            local_path:   Absolute path to the PNG file.
            guid:         Image GUID (for logging).
            image_class:  Determined image class (for ocr_version tag).

        Returns:
            ImageOCRMetadata with ocr_text populated on success, None on failure.
        """
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import]
            if StackOverflowParser._ocr_engine is None:
                StackOverflowParser._ocr_engine = RapidOCR()
            engine = StackOverflowParser._ocr_engine
            result, elapse = engine(local_path)
            if not result:
                return ImageOCRMetadata(
                    image_guid=guid,
                    image_path=local_path,
                    image_class=image_class,
                    ocr_required=True,
                    ocr_text=None,
                    ocr_confidence=None,
                    ocr_version="rapidocr-1.0",
                )
            # result is list of [bbox, text, confidence]
            texts       = [row[1] for row in result if row and len(row) > 1]
            confidences = [float(row[2]) for row in result if row and len(row) > 2]
            ocr_text       = "\n".join(texts) if texts else None
            ocr_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
            return ImageOCRMetadata(
                image_guid=guid,
                image_path=local_path,
                image_class=image_class,
                ocr_required=True,
                ocr_text=ocr_text,
                ocr_confidence=ocr_confidence,
                ocr_version="rapidocr-1.0",
            )
        except ImportError:
            LOGGER.debug(
                "rapidocr-onnxruntime not installed; skipping OCR for guid=%s", guid
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("OCR failed for guid=%s: %s", guid, exc)

        return ImageOCRMetadata(
            image_guid=guid,
            image_path=local_path,
            image_class=image_class,
            ocr_required=True,
            ocr_text=None,
            ocr_confidence=None,
            ocr_version="none",
        )

    def _select_best_answer(
        self,
        answers: list[dict],
        accepted_id: Optional[int],
    ) -> Optional[dict]:
        """Return the best answer: accepted answer first, else highest-scored."""
        if not answers:
            return None

        if accepted_id is not None:
            for a in answers:
                if a.get("id") == accepted_id:
                    return a

        # Fallback: highest score (ties broken by ID for determinism)
        valid = [a for a in answers if a.get("postState", "Published") != "Deleted"]
        if not valid:
            return None
        return max(valid, key=lambda a: (int(a.get("score", 0)), int(a.get("id", 0))))

    def _format_comments(self, comments: list[dict]) -> str:
        """Format all substantive comments as an addendum. Sorted by score desc."""
        if not comments:
            return ""
        scored = sorted(comments, key=lambda c: int(c.get("score", 0)), reverse=True)
        parts = []
        for c in scored:
            text = _normalize_markdown(str(c.get("bodyMarkdown", "") or c.get("body", ""))).strip()
            if text and len(text) >= self._MIN_COMMENT_CHARS:
                parts.append(f"  - {text}")
        if not parts:
            return ""
        return "\n\nComments:\n" + "\n".join(parts)

    def _format_top_comments(self, comments: list[dict]) -> str:
        """Kept for backward compatibility — delegates to _format_comments."""
        return self._format_comments(comments)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _clean_text(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _parse_tags(post: dict) -> list[str]:
    """Extract tags from a post dict. Handles both list and pipe-separated string formats."""
    raw = post.get("tags")
    if isinstance(raw, list):
        return [str(t).strip().lower() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip().lower() for t in re.split(r"[|,;]", raw) if t.strip()]
    return []


def _normalize_markdown(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _extract_canonical_url(question: dict[str, Any], post_id: int) -> str:
    direct = (
        question.get("questionUrl")
        or question.get("url")
        or question.get("link")
        or question.get("permalink")
    )
    if direct and isinstance(direct, str):
        return direct.strip()
    return f"{_STACK_BASE_URL}/questions/{post_id}"


_ALT_TEXT_RE = re.compile(
    r"!\[([^\]]*)\]\(https?://stackoverflowteams\.com/c/[^/]+/images/s/"
    r"([0-9a-fA-F\-]{32,36})\.[^)]+\)",
    flags=re.IGNORECASE,
)


def _extract_image_references(
    question_markdown: str,
    answer_markdown: str,
    image_map: dict[str, dict[str, Any]],
    data_dir: Path,
    *,
    title_and_tags: str = "",
    parser: Optional["StackOverflowParser"] = None,
) -> list[ImageReference]:
    """
    Extract all image references from question + answer markdown.

    When `parser` is provided, also:
      1. Classifies each image using _classify_image()
      2. Decides whether OCR is required using _should_ocr()
      3. Executes OCR (via _run_ocr()) when required AND local file exists
      4. Attaches the resulting ImageOCRMetadata to each ImageReference

    `title_and_tags` is appended to the body context used for classification
    so that Rule 8 (UI/portal/screen keywords from title or tags) fires correctly.
    """
    combined_body = "\n".join(
        [question_markdown or "", answer_markdown or "", title_and_tags or ""]
    )
    text = "\n".join([question_markdown or "", answer_markdown or ""])

    # Build alt-text lookup: guid → alt-text (last occurrence wins)
    alt_text_map: dict[str, str] = {}
    for m in _ALT_TEXT_RE.finditer(text):
        alt_text_map[m.group(2).lower()] = m.group(1)

    refs: list[ImageReference] = []
    seen: set[str] = set()
    for match in _IMAGE_URL_RE.finditer(text):
        guid = match.group(1).lower()
        ext = match.group(2).lower()
        url = match.group(0)
        key = f"{guid}.{ext}"
        if key in seen:
            continue
        seen.add(key)

        manifest  = image_map.get(guid)
        image_id  = int(manifest.get("id")) if manifest and manifest.get("id") is not None else None
        local_path = _resolve_local_image_path(data_dir, guid, ext)

        # Build the ImageReference (ocr_metadata set below)
        ref = ImageReference(
            url=url,
            guid=guid,
            extension=ext,
            image_id=image_id,
            manifest_found=manifest is not None,
            local_path=local_path,
        )

        # Attach OCR metadata when parser is provided
        if parser is not None:
            alt_text    = alt_text_map.get(guid, "")
            image_class = parser._classify_image(guid, alt_text, combined_body)
            ocr_required = parser._should_ocr(image_class, len(combined_body))

            if ocr_required and local_path is not None:
                ocr_metadata = parser._run_ocr(local_path, guid, image_class)
            else:
                ocr_metadata = ImageOCRMetadata(
                    image_guid=guid,
                    image_path=str(local_path) if local_path else "",
                    image_class=image_class,
                    ocr_required=ocr_required,
                    ocr_text=None,
                    ocr_confidence=None,
                    ocr_version="none",
                )
            ref.ocr_metadata = ocr_metadata

        refs.append(ref)
    return refs


def _resolve_local_image_path(data_dir: Path, guid: str, ext: str) -> Optional[str]:
    images_dir = data_dir / "images"
    if not images_dir.exists():
        return None
    guid_no_dash = guid.replace("-", "")
    for candidate in (images_dir / f"{guid}.{ext}", images_dir / f"{guid_no_dash}.{ext}"):
        if candidate.exists():
            return str(candidate)
    return None


_MIN_CONTENT_LENGTH = 80   # characters — below this we consider content too thin
_PARSER_CORRUPTION_RE = re.compile(
    r"(?:â€|Ã©|\\u[0-9a-fA-F]{4}|&#[0-9]+;|&amp;amp;|<\?xml|<!DOCTYPE)",
)


def _build_completeness_flags(
    *,
    question_markdown: str,
    answer_markdown: Optional[str],
    accepted_answer_id: Optional[int],
    image_refs: list[ImageReference],
    tags_raw: list[str],
    canonical_url: str,
    answer_body: Optional[str] = None,
) -> dict[str, bool]:
    """
    Extended completeness scoring (WORK ITEM 3).

    Dimensions:
      question_present            — question body is non-empty
      answer_present              — at least one answer body exists
      accepted_answer_present     — accepted answer exists AND has content
      tags_present                — at least one tag attached
      canonical_url_present       — URL is absolute and http(s)
      markdown_present            — question has markdown content
      image_metadata_resolved     — all image refs have manifest metadata
      image_assets_resolved       — all image refs have a local binary path
      minimum_content_length      — total content >= _MIN_CONTENT_LENGTH chars
      no_parser_corruption        — no encoding corruption patterns detected
      client_relevance_detectable — meaningful (non-generic) tags present
    """
    q_text = question_markdown.strip()
    a_text = (answer_markdown or answer_body or "").strip()

    question_present        = bool(q_text)
    answer_present          = bool(a_text)
    accepted_answer_present = accepted_answer_id is not None and answer_present
    tags_present            = len(tags_raw) > 0
    canonical_present       = bool(canonical_url) and canonical_url.startswith("http")
    markdown_present        = bool(q_text)

    # Image grounding (split into metadata vs. binary)
    if image_refs:
        image_metadata_resolved = all(ref.manifest_found for ref in image_refs)
        image_assets_resolved   = all(ref.local_path is not None for ref in image_refs)
    else:
        image_metadata_resolved = True  # no images → vacuously true
        image_assets_resolved   = True

    total_content = len(q_text) + len(a_text)
    minimum_content_length = total_content >= _MIN_CONTENT_LENGTH

    combined = q_text + " " + a_text
    no_parser_corruption = not bool(_PARSER_CORRUPTION_RE.search(combined))

    client_relevance = _client_relevance_detectable(tags_raw)

    return {
        "question_present":            question_present,
        "answer_present":              answer_present,
        "accepted_answer_present":     accepted_answer_present,
        "tags_present":                tags_present,
        "canonical_url_present":       canonical_present,
        "markdown_present":            markdown_present,
        "image_metadata_resolved":     image_metadata_resolved,
        "image_assets_resolved":       image_assets_resolved,
        "minimum_content_length":      minimum_content_length,
        "no_parser_corruption":        no_parser_corruption,
        "client_relevance_detectable": client_relevance,
    }


# ---------------------------------------------------------------------------
# Knowledge Safety Classification (WORK ITEM 5)
# ---------------------------------------------------------------------------

# Patterns that indicate sensitive operational content requiring review
_SENSITIVE_OPERATIONS_RE = re.compile(
    r"(?i)\b("
    r"admin\s+access|super\s*admin|root\s+access|database\s+password|"
    r"master\s+key|service\s+account|iam\s+role|private\s+key|"
    r"internal\s+endpoint|vpn\s+config|firewall\s+rule|"
    r"connection\s+string|jdbc|dblink|redis\s+auth|"
    r"prod\s+env|production\s+secret|production\s+key"
    r")\b",
)

# Patterns that suggest restricted internal references (OK for agents, not end users)
_RESTRICTED_INTERNAL_RE = re.compile(
    r"(?i)\b("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"  # private IPs
    r"internal\s+api|staging\s+url|dev\.kwikid|staging\.kwikid|"
    r"slack\s+channel|jira\s+ticket|asana\s+task|"
    r"@kwikid\.com|@think360\.ai"
    r")\b",
)


def _classify_safety(
    *,
    title: str,
    question_markdown: str,
    answer_markdown: str,
) -> str:
    """
    Deterministic knowledge safety classification (WORK ITEM 5).

    Returns:
        "sensitive_operations"  — requires human review before serving to LLM
        "restricted_internal"   — acceptable for agent use, not end-user-facing
        "safe_for_llm"          — normal SOP/FAQ content, freely retrievable

    Rules are additive: most-restrictive wins.
    """
    combined = f"{title} {question_markdown} {answer_markdown}"

    if _SENSITIVE_OPERATIONS_RE.search(combined):
        return "sensitive_operations"

    if _RESTRICTED_INTERNAL_RE.search(combined):
        return "restricted_internal"

    return "safe_for_llm"


def _client_relevance_detectable(tags_raw: list[str]) -> bool:
    if not tags_raw:
        return False
    meaningful = [
        tag for tag in tags_raw
        if tag and tag.lower() not in _GENERIC_TAGS and len(tag.strip()) >= 3
    ]
    return len(meaningful) > 0


def _score_completeness(flags: dict[str, bool]) -> float:
    if not flags:
        return 0.0
    return round(sum(1.0 for value in flags.values() if value) / float(len(flags)), 3)
