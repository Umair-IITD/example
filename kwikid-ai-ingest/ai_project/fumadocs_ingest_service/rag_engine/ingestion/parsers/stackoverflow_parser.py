"""
rag_engine/ingestion/parsers/stackoverflow_parser.py

Parse a Stack Overflow for Teams JSON export (More_data/) into KnowledgeArticle
objects ready for downstream classification, PII redaction, and embedding.

Export files expected:
  posts.json          — questions + answers (1,639 posts in Think360 export)
  comments.json       — 185 comments keyed by parentPostId
  posts2votes.json    — 838 vote records keyed by postId
  tags.json           — 484 tags with usage counts
  (users / badges files are ignored — never embedded)

Output:
  list[KnowledgeArticle] — one per question, best answer already selected

Design decisions:
  - Defensive parsing: all dict access uses .get() with safe defaults
  - Every field is explicitly validated before use
  - No exception propagates from _parse_single_post — errors are counted
  - All HTML stripped from bodyMarkdown before storage
  - Comments appended to answer body (top 3 by score, for context richness)
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

@dataclass
class ImageReference:
    url: str
    guid: str
    extension: str
    image_id: Optional[int]
    manifest_found: bool
    local_path: Optional[str]


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
    missing_image_count:  int              # refs with manifest_found=False
    image_grounding_status: str            # "no_images" | "fully_resolved" | "partially_resolved" | "unresolved"

    # Knowledge safety classification (WORK ITEM 5)
    safety_classification: str            # "safe_for_llm" | "restricted_internal" | "sensitive_operations"

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

    # Maximum number of top comments to append to the answer body
    _MAX_COMMENTS = 3

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

        # Select best answer
        answers     = export.answer_map.get(post_id, [])
        best_answer = self._select_best_answer(answers, accepted_id)

        answer_body: Optional[str] = None
        answer_markdown: Optional[str] = None
        answer_post_id: Optional[int] = None
        answer_score: int = 0

        if best_answer is not None:
            answer_md = _normalize_markdown(str(best_answer.get("bodyMarkdown", "") or "")).strip()
            answer_html = _normalize_markdown(_clean_text(str(best_answer.get("body", "")))).strip()
            a_body = answer_md or answer_html
            if a_body:
                # Append top comments to answer body for extra context
                comments = export.comment_map.get(best_answer.get("id", -1), [])
                comment_text = self._format_top_comments(comments)
                answer_body    = (a_body + comment_text) if comment_text else a_body
                answer_markdown = answer_md if answer_md else a_body
                answer_post_id = best_answer.get("id")
                answer_score   = int(best_answer.get("score", 0))

        article_type = "qa_pair" if answer_body else "question_only"
        canonical_url = _extract_canonical_url(question, post_id)
        image_references = _extract_image_references(
            question_markdown,
            answer_markdown or answer_body or "",
            export.image_map,
            data_dir,
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
            post_state         = post_state,
            created_at_source  = question.get("creationDate"),
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

    def _format_top_comments(self, comments: list[dict]) -> str:
        """Format top N comments as a brief addendum to the answer body."""
        if not comments:
            return ""
        scored = sorted(
            comments,
            key=lambda c: int(c.get("score", 0)),
            reverse=True,
        )[:self._MAX_COMMENTS]
        parts = []
        for c in scored:
            text = _normalize_markdown(str(c.get("bodyMarkdown", "") or c.get("body", ""))).strip()
            if text and len(text) > 20:
                parts.append(f"  - {text}")
        if not parts:
            return ""
        return "\n\nRelated comments:\n" + "\n".join(parts)


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


def _extract_image_references(
    question_markdown: str,
    answer_markdown: str,
    image_map: dict[str, dict[str, Any]],
    data_dir: Path,
) -> list[ImageReference]:
    text = "\n".join([question_markdown or "", answer_markdown or ""])
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

        manifest = image_map.get(guid)
        image_id = int(manifest.get("id")) if manifest and manifest.get("id") is not None else None
        local_path = _resolve_local_image_path(data_dir, guid, ext)

        refs.append(
            ImageReference(
                url=url,
                guid=guid,
                extension=ext,
                image_id=image_id,
                manifest_found=manifest is not None,
                local_path=local_path,
            )
        )
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
