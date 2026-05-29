"""
rag_engine/chunking/knowledge_chunker.py

Semantic chunker for knowledge articles that separates code from prose.

Problem with the previous word-count chunker:
  Knowledge articles from StackOverflow for Teams routinely contain mixed
  content — prose explanations, shell commands, code blocks, YAML configs,
  and API payloads all in the same text block. Word-count splitting creates
  mixed-content chunks whose embeddings sit at blurry midpoints in the vector
  space, making them hard to retrieve precisely.

This chunker splits on structural boundaries first:

  1. Fenced code blocks (``` ... ```)       → CODE_SAMPLE chunks
  2. Shell command blocks ($ / ~$ / > / #!) → COMMAND chunks
  3. Prose paragraphs                        → chunk_type from KnowledgeClass

Then each prose segment is token-split (tiktoken) to the target window.
Code blocks are kept intact (never split mid-block) up to the embedding
hard cap; oversized code blocks are truncated with a warning.

Output chunk_type values:
  CODE_SAMPLE   — fenced code blocks
  COMMAND       — shell/CLI command sequences
  HOW_TO        — step-by-step procedures (TROUBLESHOOTING articles)
  CONCEPT       — explanatory prose (FAQ articles)
  VERIFIED_REPLY — human-reviewed Q+A (VERIFIED_REPLY class)
  TROUBLESHOOTING — mixed troubleshooting prose
  FAQ_ANSWER    — general FAQ prose
  POLICY        — policy / compliance prose
  RCA           — root cause analysis
"""
from __future__ import annotations

import logging
import re
import uuid
import hashlib
from dataclasses import dataclass, field
from typing import Optional

from rag_engine.ingestion.knowledge_classifier import KnowledgeClass
from rag_engine.utils.tokens import count_tokens, safe_split_by_tokens, truncate_to_token_limit

LOGGER = logging.getLogger(__name__)

# ── Structural detection patterns ──────────────────────────────────────────────

# Fenced code block: ```[lang]\n...\n```
_FENCED_CODE_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+-]*)?\n(.*?)```",
    re.DOTALL,
)

# Shell command lines: start with $, ~$, >, #!, or common shell prefixes
_COMMAND_LINE_RE = re.compile(
    r"^[\t ]*(?:\$\s|\~\$\s|\#\!\s|>\s|bash\s+-c\s+)",
    re.MULTILINE,
)

# Paragraph boundary (two or more newlines)
_PARA_BREAK_RE = re.compile(r"\n{2,}")

# Minimum characters for a code block to become a CODE_SAMPLE chunk
# (vs. being treated as inline formatting noise)
_MIN_CODE_CHARS = 60

# Maximum tokens for a code block chunk (hard cap before truncation)
_MAX_CODE_TOKENS = 1500


@dataclass
class KnowledgeChunk:
    """A single output chunk from KnowledgeChunker."""
    chunk_id:    str
    chunk_index: int
    chunk_type:  str
    content:     str
    word_count:  int
    content_hash: str
    is_code: bool = False

    @classmethod
    def from_content(
        cls,
        content: str,
        *,
        article_id: str,
        chunk_index: int,
        chunk_type: str,
        index_version: str,
        is_code: bool = False,
    ) -> "KnowledgeChunk":
        content = content.strip()
        chunk_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"knowledge:{article_id}:{chunk_index}:{index_version}",
        ))
        return cls(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            chunk_type=chunk_type,
            content=content,
            word_count=len(content.split()),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            is_code=is_code,
        )


class KnowledgeChunker:
    """
    Semantic chunker for knowledge articles.

    Separates code blocks from prose, assigns appropriate chunk_type per
    segment, and token-splits prose sections to the target window.

    Stateless — thread-safe.
    """

    def __init__(
        self,
        *,
        knowledge_class: KnowledgeClass = KnowledgeClass.FAQ,
        chunk_target_tokens: int = 800,
        chunk_overlap_tokens: int = 80,
        max_input_tokens: int = 7000,
        embedding_model: str = "text-embedding-3-small",
        min_chunk_chars: int = 60,
    ) -> None:
        self._class               = knowledge_class
        self._chunk_target_tokens  = chunk_target_tokens
        self._chunk_overlap_tokens = chunk_overlap_tokens
        self._max_input_tokens     = max_input_tokens
        self._model                = embedding_model
        self._min_chunk_chars      = min_chunk_chars
        self._prose_chunk_type     = self._map_class_to_chunk_type(knowledge_class)

    def chunk(
        self,
        text: str,
        *,
        article_id: str,
        index_version: str,
        start_index: int = 0,
    ) -> list[KnowledgeChunk]:
        """
        Split article text into semantic chunks.

        Args:
            text:          Full article embed text (title + question + answer).
            article_id:    Source article identifier for deterministic IDs.
            index_version: Index version string for ID stability.
            start_index:   Starting chunk_index offset (for multi-part ingestion).

        Returns:
            List of KnowledgeChunk, ordered by position in source text.
        """
        if not text or not text.strip():
            return []

        segments = self._split_into_segments(text)
        chunks: list[KnowledgeChunk] = []
        idx = start_index

        for seg_type, seg_content in segments:
            seg_content = seg_content.strip()
            if not seg_content or len(seg_content) < self._min_chunk_chars:
                continue

            if seg_type == "code":
                # Code blocks: keep intact, truncate only if necessary
                content, was_truncated = self._safe_code_block(seg_content, article_id)
                if content and len(content.strip()) >= self._min_chunk_chars:
                    chunks.append(KnowledgeChunk.from_content(
                        content,
                        article_id=article_id,
                        chunk_index=idx,
                        chunk_type=self._detect_code_type(content),
                        index_version=index_version,
                        is_code=True,
                    ))
                    idx += 1
            else:
                # Prose: token-split to target window
                parts = safe_split_by_tokens(
                    seg_content,
                    max_tokens=self._chunk_target_tokens,
                    overlap_tokens=self._chunk_overlap_tokens,
                    model=self._model,
                )
                for part in parts:
                    part = part.strip()
                    if len(part) < self._min_chunk_chars:
                        if chunks:
                            # Merge orphan tail into previous chunk
                            prev = chunks[-1]
                            merged = prev.content + "\n" + part
                            chunks[-1] = KnowledgeChunk.from_content(
                                merged,
                                article_id=article_id,
                                chunk_index=prev.chunk_index,
                                chunk_type=prev.chunk_type,
                                index_version=index_version,
                                is_code=prev.is_code,
                            )
                        continue
                    chunks.append(KnowledgeChunk.from_content(
                        part,
                        article_id=article_id,
                        chunk_index=idx,
                        chunk_type=self._prose_chunk_type,
                        index_version=index_version,
                        is_code=False,
                    ))
                    idx += 1

        return chunks

    # ── Private helpers ────────────────────────────────────────────────────────

    def _split_into_segments(self, text: str) -> list[tuple[str, str]]:
        """
        Split text into alternating (type, content) segments.
        type is either "code" or "prose".

        Code blocks are extracted first; remaining text is split into prose paragraphs.
        """
        segments: list[tuple[str, str]] = []
        last_end = 0

        for match in _FENCED_CODE_RE.finditer(text):
            start, end = match.span()

            # Prose before this code block
            if start > last_end:
                prose = text[last_end:start]
                for para in _PARA_BREAK_RE.split(prose):
                    para = para.strip()
                    if para:
                        segments.append(("prose", para))

            # The code block itself
            code_content = match.group(0)
            if len(code_content.strip()) >= _MIN_CODE_CHARS:
                segments.append(("code", code_content))
            else:
                # Tiny code snippet — treat as prose (inline code, not a block)
                segments.append(("prose", code_content))

            last_end = end

        # Remaining prose after the last code block
        if last_end < len(text):
            remaining = text[last_end:]
            for para in _PARA_BREAK_RE.split(remaining):
                para = para.strip()
                if para:
                    # Detect command-only blocks in prose
                    seg_type = "command" if self._is_command_block(para) else "prose"
                    segments.append((seg_type, para))

        return segments

    @staticmethod
    def _is_command_block(text: str) -> bool:
        """Return True if most lines of text look like shell commands."""
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            return False
        command_lines = sum(1 for l in lines if _COMMAND_LINE_RE.match(l))
        return command_lines / len(lines) >= 0.5

    @staticmethod
    def _detect_code_type(code_block: str) -> str:
        """Detect whether a code block is a COMMAND sequence or general CODE_SAMPLE."""
        lines = [l for l in code_block.splitlines() if l.strip() and not l.startswith("```")]
        if not lines:
            return "CODE_SAMPLE"
        command_lines = sum(1 for l in lines if _COMMAND_LINE_RE.match(l))
        ratio = command_lines / len(lines)
        return "COMMAND" if ratio >= 0.4 else "CODE_SAMPLE"

    def _safe_code_block(self, code: str, article_id: str) -> tuple[str, bool]:
        """Enforce max_input_tokens on a code block. Returns (content, was_truncated)."""
        token_count = count_tokens(code, self._model)
        if token_count <= _MAX_CODE_TOKENS:
            return code, False
        LOGGER.warning(
            "Knowledge article %s: code block has %d tokens — truncating to %d",
            article_id, token_count, _MAX_CODE_TOKENS,
        )
        return truncate_to_token_limit(code, _MAX_CODE_TOKENS, self._model), True

    @staticmethod
    def _map_class_to_chunk_type(klass: KnowledgeClass) -> str:
        return {
            KnowledgeClass.VERIFIED_REPLY:  "VERIFIED_REPLY",
            KnowledgeClass.TROUBLESHOOTING: "HOW_TO",
            KnowledgeClass.FAQ:             "CONCEPT",
            KnowledgeClass.POLICY:          "POLICY",
            KnowledgeClass.RCA:             "RCA",
        }.get(klass, "CONCEPT")
