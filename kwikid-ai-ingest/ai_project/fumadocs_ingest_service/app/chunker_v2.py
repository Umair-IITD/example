"""
app/chunker_v2.py

Token-aware document chunker for index version v2.

Replaces the word-count-based app/chunker.py for all new ingestion runs.
The old chunker.py is retained for backward compatibility with v1 index data —
existing documents served via ACTIVE_INDEX_VERSION=v1 continue to work unchanged.

Key improvements over v1 (word-count):
  - Token counting via tiktoken (cl100k_base — same BPE encoder as text-embedding-3-small)
  - Hard cap at EMBEDDING_MAX_INPUT_TOKENS=7000 (15% safety margin below OpenAI's 8192 limit)
  - Target: CHUNK_TARGET_TOKENS=1200 tokens/chunk (≈900 words English prose)
  - Overlap: CHUNK_OVERLAP_TOKENS=150 tokens at split boundaries (prevents context loss)
  - Min chunk: MIN_CHUNK_TOKENS=80 tokens (orphan chunks are merged into previous)
  - Deterministic chunk IDs: "v2:{source_type}:{source_id}:{index}:{sha256[:12]}"
    → enables safe idempotent upsert (re-run = same IDs = no duplicates)
  - Handles oversized single segments via token-level splitting (not word-level)

Activation:
  Set WRITE_INDEX_VERSION=v2 in the environment to use this chunker for new ingestion.
  ACTIVE_INDEX_VERSION can be v1 or v2 independently — migration is zero-downtime.

Limitations:
  - Tiktoken optional dep. If not installed, falls back to word-count approximation
    (logs a one-time warning). Install tiktoken for production: pip install tiktoken==0.8.0
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

LOGGER = logging.getLogger(__name__)

WORD_PATTERN = re.compile(r"\S+")

# ── Tiktoken setup (optional dep) ────────────────────────────────────────────

_ENCODER = None
_TIKTOKEN_AVAILABLE = False
_TIKTOKEN_WARNING_LOGGED = False


def _get_encoder():
    global _ENCODER, _TIKTOKEN_AVAILABLE, _TIKTOKEN_WARNING_LOGGED
    if _ENCODER is not None:
        return _ENCODER
    try:
        import tiktoken  # noqa: PLC0415
        _ENCODER = tiktoken.get_encoding("cl100k_base")
        _TIKTOKEN_AVAILABLE = True
        LOGGER.info("chunker_v2: using tiktoken cl100k_base encoder")
    except ImportError:
        if not _TIKTOKEN_WARNING_LOGGED:
            LOGGER.warning(
                "chunker_v2: tiktoken not installed — falling back to word-count approximation "
                "(token counts will be estimates). Install: pip install tiktoken==0.8.0"
            )
            _TIKTOKEN_WARNING_LOGGED = True
    return _ENCODER


def _count_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    # Approximation: 1.3 tokens per word (English prose average)
    return int(len(WORD_PATTERN.findall(text)) * 1.3)


def _trim_to_tokens(text: str, max_tokens: int) -> str:
    """Hard-trim text to at most max_tokens tokens."""
    enc = _get_encoder()
    if enc is not None:
        token_ids = enc.encode(text)
        if len(token_ids) <= max_tokens:
            return text
        return enc.decode(token_ids[:max_tokens])
    # Word-based fallback
    words = WORD_PATTERN.findall(text)
    max_words = max(1, int(max_tokens / 1.3))
    return " ".join(words[:max_words])


def _overlap_suffix(text: str, overlap_tokens: int) -> str:
    """Return the last overlap_tokens tokens of text as a string."""
    if overlap_tokens <= 0:
        return ""
    enc = _get_encoder()
    if enc is not None:
        ids = enc.encode(text)
        if not ids:
            return ""
        return enc.decode(ids[-overlap_tokens:])
    # Word-based fallback
    words = WORD_PATTERN.findall(text)
    max_words = max(1, int(overlap_tokens / 1.3))
    return " ".join(words[-max_words:]) if words else ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceDocument:
    """Input document for chunking."""
    source_type: str                # "md", "json", "excel", "freshdesk", ...
    source_id: str                  # unique ID within source_type
    content: str
    title: str
    heading: str | None
    tags: list[str]
    creation_date: str | None
    metadata: dict                  # arbitrary key-value metadata preserved on chunks


@dataclass(frozen=True)
class ChunkV2:
    """A single chunk produced by the v2 token-aware chunker."""
    chunk_id: str                   # deterministic: "v2:{source_type}:{source_id}:{idx}:{hash[:12]}"
    source_type: str
    source_id: str
    title: str
    heading: str | None
    tags: list[str]
    chunk_index: int
    content: str
    token_count: int
    content_hash: str               # full sha256 of content
    metadata: dict
    index_version: str = "v2"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _split_markdown_sections(text: str) -> list[str]:
    """Split at heading boundaries (lines starting with #)."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and current:
            joined = "\n".join(current).strip()
            if joined:
                sections.append(joined)
            current = [line]
        else:
            current.append(line)
    if current:
        joined = "\n".join(current).strip()
        if joined:
            sections.append(joined)
    return sections


def _build_chunk(
    doc: SourceDocument,
    chunk_index: int,
    content: str,
    max_input_tokens: int,
) -> ChunkV2:
    # Hard cap: never send more tokens than the embedding model accepts
    if _count_tokens(content) > max_input_tokens:
        content = _trim_to_tokens(content, max_input_tokens)
        LOGGER.debug(
            "chunker_v2: trimmed chunk %s:%s:%d to %d tokens",
            doc.source_type, doc.source_id, chunk_index, max_input_tokens,
        )

    content_hash = _sha256(content)
    return ChunkV2(
        chunk_id=f"v2:{doc.source_type}:{doc.source_id}:{chunk_index}:{content_hash[:12]}",
        source_type=doc.source_type,
        source_id=doc.source_id,
        title=doc.title,
        heading=doc.heading,
        tags=doc.tags,
        chunk_index=chunk_index,
        content=content,
        token_count=_count_tokens(content),
        content_hash=content_hash,
        metadata=doc.metadata,
        index_version="v2",
    )


def _token_split_oversized(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Split a segment that exceeds max_tokens into sub-chunks using token-level sliding window."""
    enc = _get_encoder()
    parts: list[str] = []

    if enc is not None:
        all_ids = enc.encode(text)
        step = max(1, max_tokens - overlap_tokens)
        i = 0
        while i < len(all_ids):
            chunk_ids = all_ids[i: i + max_tokens]
            decoded = enc.decode(chunk_ids)
            if decoded.strip():
                parts.append(decoded)
            i += step
    else:
        # Word-based fallback
        words = WORD_PATTERN.findall(text)
        max_words = max(1, int(max_tokens / 1.3))
        step = max(1, int((max_tokens - overlap_tokens) / 1.3))
        i = 0
        while i < len(words):
            chunk_words = words[i: i + max_words]
            joined = " ".join(chunk_words)
            if joined.strip():
                parts.append(joined)
            i += step

    return [p for p in parts if p.strip()]


def _merge_short_tail(
    chunks: list[ChunkV2],
    min_chunk_tokens: int,
    max_input_tokens: int,
    doc: SourceDocument,
) -> list[ChunkV2]:
    """
    If the last chunk is shorter than min_chunk_tokens, merge it into the
    second-to-last chunk (if the combined result fits within max_input_tokens).
    """
    if len(chunks) <= 1:
        return chunks
    if chunks[-1].token_count >= min_chunk_tokens:
        return chunks

    merged_content = f"{chunks[-2].content}\n\n{chunks[-1].content}".strip()
    if _count_tokens(merged_content) <= max_input_tokens:
        merged = _build_chunk(doc, chunks[-2].chunk_index, merged_content, max_input_tokens)
        return [*chunks[:-2], merged]

    # Can't merge without exceeding limit — keep as is
    return chunks


# ── Public API ────────────────────────────────────────────────────────────────

def chunk_document_v2(
    doc: SourceDocument,
    *,
    target_tokens: int = 1200,
    max_input_tokens: int = 7000,
    overlap_tokens: int = 150,
    min_chunk_tokens: int = 80,
    strategy: Literal["auto", "paragraph", "heading_aware", "record_aware"] = "auto",
) -> list[ChunkV2]:
    """
    Token-aware document chunker — index version v2.

    Args:
        doc:              Source document to chunk.
        target_tokens:    Soft target token count per chunk. Chunks are flushed when
                          adding the next segment would exceed this.
        max_input_tokens: Hard cap. No chunk may exceed this (embedding model limit).
                          OpenAI text-embedding-3-small limit is 8192 tokens;
                          7000 gives ~15% safety margin for tokenizer drift.
        overlap_tokens:   Tokens copied from the end of chunk N to the start of chunk N+1.
                          Prevents context loss at split boundaries.
        min_chunk_tokens: Orphan chunks shorter than this are merged into the previous.
        strategy:         Splitting strategy. "auto" selects based on source_type:
                            md → heading_aware
                            json/excel/freshdesk → record_aware (paragraph-based)
                            otherwise → paragraph

    Returns:
        List of ChunkV2, sorted by chunk_index (0, 1, 2, ...).
        Empty list if document content is blank.
    """
    if not doc.content.strip():
        return []

    # ── Strategy selection ────────────────────────────────────────────────────
    effective_strategy = strategy
    if strategy == "auto":
        if doc.source_type == "md":
            effective_strategy = "heading_aware"
        elif doc.source_type in {"json", "excel", "freshdesk"}:
            effective_strategy = "record_aware"
        else:
            effective_strategy = "paragraph"

    # ── Segment extraction ────────────────────────────────────────────────────
    if effective_strategy == "heading_aware":
        segments = _split_markdown_sections(doc.content)
    else:
        segments = _split_paragraphs(doc.content)

    if not segments:
        return []

    # ── Token-aware accumulation ──────────────────────────────────────────────
    chunks: list[ChunkV2] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_index = 0
    pending_overlap = ""

    def flush() -> None:
        nonlocal current_parts, current_tokens, chunk_index, pending_overlap
        if not current_parts:
            return
        content = "\n\n".join(current_parts).strip()
        if not content:
            current_parts = []
            current_tokens = 0
            return
        chunks.append(_build_chunk(doc, chunk_index, content, max_input_tokens))
        chunk_index += 1
        pending_overlap = _overlap_suffix(content, overlap_tokens)
        current_parts = [pending_overlap] if pending_overlap else []
        current_tokens = _count_tokens(pending_overlap) if pending_overlap else 0

    for segment in segments:
        seg_tokens = _count_tokens(segment)

        if seg_tokens > max_input_tokens:
            # This segment alone exceeds the hard cap — must be split
            if current_parts:
                flush()
            for sub in _token_split_oversized(segment, max_input_tokens, overlap_tokens):
                chunks.append(_build_chunk(doc, chunk_index, sub, max_input_tokens))
                chunk_index += 1
            pending_overlap = _overlap_suffix(segment, overlap_tokens)
            current_parts = [pending_overlap] if pending_overlap else []
            current_tokens = _count_tokens(pending_overlap) if pending_overlap else 0
            continue

        projected = current_tokens + seg_tokens
        if current_parts and projected > target_tokens:
            flush()

        current_parts.append(segment)
        current_tokens += seg_tokens

        if current_tokens >= target_tokens:
            flush()

    flush()  # Emit any remaining content

    # ── Merge short tail chunk ────────────────────────────────────────────────
    chunks = _merge_short_tail(chunks, min_chunk_tokens, max_input_tokens, doc)

    LOGGER.debug(
        "chunker_v2: %s/%s → %d chunks (target=%d max=%d overlap=%d)",
        doc.source_type, doc.source_id, len(chunks),
        target_tokens, max_input_tokens, overlap_tokens,
    )
    return chunks
