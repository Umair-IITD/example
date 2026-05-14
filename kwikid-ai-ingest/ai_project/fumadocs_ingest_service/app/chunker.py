from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

WORD_PATTERN = re.compile(r"\S+")


@dataclass(frozen=True)
class SourceDocument:
    source_type: str
    source_id: str
    content: str
    title: str
    heading: str | None
    tags: list[str]
    creation_date: str | None
    metadata: dict[str, object]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_type: str
    source_id: str
    title: str
    heading: str | None
    tags: list[str]
    chunk_index: int
    content: str
    word_count: int
    content_hash: str
    metadata: dict[str, object]


def _word_count(text: str) -> int:
    return len(WORD_PATTERN.findall(text))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trim_to_words(text: str, max_words: int) -> str:
    words = WORD_PATTERN.findall(text)
    return " ".join(words[:max_words])


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _split_markdown_sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and current:
            sections.append("\n".join(current).strip())
            current = [line]
            continue
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def _with_overlap(content: str, overlap_words: int) -> tuple[list[str], int]:
    if overlap_words <= 0:
        return [], 0
    overlap = WORD_PATTERN.findall(content)[-overlap_words:]
    if not overlap:
        return [], 0
    overlap_text = " ".join(overlap)
    return [overlap_text], _word_count(overlap_text)


def _split_words_by_limits(text: str, *, max_words: int, max_chars: int) -> list[str]:
    words = WORD_PATTERN.findall(text)
    if not words:
        return []

    parts: list[str] = []
    current_words: list[str] = []
    current_chars = 0
    for word in words:
        projected_chars = len(word) if not current_words else current_chars + 1 + len(word)
        if current_words and (len(current_words) >= max_words or projected_chars > max_chars):
            parts.append(" ".join(current_words))
            current_words = [word]
            current_chars = len(word)
            continue
        current_words.append(word)
        current_chars = projected_chars

    if current_words:
        parts.append(" ".join(current_words))
    return [part for part in parts if part.strip()]


def _split_content_preserving_all(text: str, *, max_words: int, max_chars: int) -> list[str]:
    if _word_count(text) <= max_words and len(text) <= max_chars:
        return [text]
    return _split_words_by_limits(text, max_words=max_words, max_chars=max_chars)


def _build_chunk(doc: SourceDocument, chunk_index: int, content: str) -> Chunk:
    chunk_hash = _sha256(content)
    return Chunk(
        chunk_id=f"{doc.source_type}:{doc.source_id}:{chunk_index}:{chunk_hash[:12]}",
        source_type=doc.source_type,
        source_id=doc.source_id,
        title=doc.title,
        heading=doc.heading,
        tags=doc.tags,
        chunk_index=chunk_index,
        content=content,
        word_count=_word_count(content),
        content_hash=chunk_hash,
        metadata=doc.metadata,
    )


def chunk_document(
    doc: SourceDocument,
    min_words: int,
    target_words: int,
    max_words: int,
    overlap_words: int,
    *,
    strategy: Literal["auto", "paragraph", "heading_aware", "record_aware"] = "auto",
    max_chars: int = 6000,
    min_orphan_words: int = 50,
) -> list[Chunk]:
    chunk_strategy = strategy
    if strategy == "auto":
        if doc.source_type == "md":
            chunk_strategy = "heading_aware"
        elif doc.source_type in {"json", "excel", "freshdesk"}:
            chunk_strategy = "record_aware"
        else:
            chunk_strategy = "paragraph"

    if chunk_strategy == "heading_aware":
        paragraphs = _split_markdown_sections(doc.content)
    else:
        paragraphs = _split_paragraphs(doc.content)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    current: list[str] = []
    current_words = 0

    def flush_chunk(chunk_index: int) -> int:
        nonlocal current, current_words
        if not current:
            return chunk_index
        content = "\n\n".join(current).strip()
        wc = _word_count(content)
        if wc == 0:
            return chunk_index
        parts = _split_content_preserving_all(content, max_words=max_words, max_chars=max_chars)
        last_part = ""
        for part in parts:
            chunks.append(_build_chunk(doc, chunk_index, part))
            last_part = part
            chunk_index += 1
        # Lightweight overlap: carry the last N words into next chunk for continuity.
        current, current_words = _with_overlap(last_part, overlap_words)
        return chunk_index

    idx = 0
    for para in paragraphs:
        para_wc = _word_count(para)
        should_flush = current_words >= min_words and (current_words + para_wc) > target_words
        if should_flush:
            idx = flush_chunk(idx)

        current.append(para)
        current_words += para_wc

        if current_words >= max_words:
            idx = flush_chunk(idx)

    if current_words > 0:
        idx = flush_chunk(idx)

    if len(chunks) > 1 and chunks[-1].word_count < min_orphan_words:
        merged_content = f"{chunks[-2].content}\n\n{chunks[-1].content}".strip()
        if _word_count(merged_content) <= max_words and len(merged_content) <= max_chars:
            merged_chunk = _build_chunk(doc, chunks[-2].chunk_index, merged_content)
            chunks = [*chunks[:-2], merged_chunk]

    return chunks

