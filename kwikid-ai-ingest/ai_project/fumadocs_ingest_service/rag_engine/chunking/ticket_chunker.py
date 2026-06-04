"""
rag_engine/chunking/ticket_chunker.py

Section-aware, token-exact chunking for support ticket documents.

Design:
  Each RagTicketDocument → 2 or 3 TicketChunks by logical section:

  Chunk 0 — ISSUE_HEADER
    Metadata-rich header. Short (50–100 words, ~100–200 tokens).
    Used for: exact category matching, tenant-class lookup.

  Chunk 1+ — QUERY_BODY
    The customer's complaint text. Variable length.
    Primary semantic similarity target.
    Split with token overlap if it exceeds chunk_target_tokens.

  Chunk N+ — RESOLUTION_RCA
    Resolution steps + root cause. Variable length.
    Primary answer generation source.
    Split with token overlap if it exceeds chunk_target_tokens.

Token safety:
  Splitting uses tiktoken for exact token counting (cl100k_base for all OpenAI
  embedding models). If a chunk still exceeds embedding_max_input_tokens after
  splitting (e.g., a single word is a 10 000-token base64 blob), it is truncated
  with a warning logged and the metadata flag extra_metadata["truncated"]=True set.
  Truncation never aborts ingestion — the chunk is marked and upserted normally.

Backward compatibility:
  max_chunk_words / overlap_words are kept but unused when tiktoken is available.
  The token-based params (chunk_target_tokens, chunk_overlap_tokens) take precedence.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Optional

from rag_engine.schemas.chunk_schema import TicketChunk
from rag_engine.schemas.ticket_document import AutomationLabel, ChunkType, RagTicketDocument
from rag_engine.utils.tokens import (
    count_tokens,
    safe_split_by_tokens,
    truncate_to_token_limit,
)

LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _deterministic_id(ticket_id: str, chunk_index: int, index_version: str) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"freshdesk:{ticket_id}:{chunk_index}:{index_version}",
    ))


class TicketChunker:
    """
    Converts a RagTicketDocument into a list of TicketChunks.
    Stateless and thread-safe.
    """

    def __init__(
        self,
        *,
        # Legacy word-based params (fallback when tiktoken unavailable)
        max_chunk_words: int = 400,
        overlap_words: int = 20,
        min_query_body_chars: int = 80,
        index_version: str = "v1",
        # Token-aware params (primary strategy when tiktoken is available)
        chunk_target_tokens: int = 1200,
        chunk_overlap_tokens: int = 150,
        max_input_tokens: int = 7000,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        self._max_chunk_words      = max_chunk_words
        self._overlap_words        = overlap_words
        self._min_query_body_chars = min_query_body_chars
        self._index_version        = index_version
        self._max_input_tokens     = max_input_tokens
        self._embedding_model      = embedding_model

        # When max_chunk_words is explicitly set smaller than the default (400),
        # derive a stricter token target so the word-based limit is honored
        # regardless of whether tiktoken is available.
        _DEFAULT_MAX_CHUNK_WORDS = 400
        if max_chunk_words < _DEFAULT_MAX_CHUNK_WORDS:
            word_derived_tokens = max(1, int(max_chunk_words * 1.5))
            self._chunk_target_tokens  = min(chunk_target_tokens, word_derived_tokens)
            word_derived_overlap = max(0, int(overlap_words * 1.5))
            self._chunk_overlap_tokens = min(chunk_overlap_tokens, word_derived_overlap)
        else:
            self._chunk_target_tokens  = chunk_target_tokens
            self._chunk_overlap_tokens = chunk_overlap_tokens

    def chunk(
        self,
        doc: RagTicketDocument,
        document_id: Optional[str] = None,
        ingestion_run_id: Optional[str] = None,
    ) -> list[TicketChunk]:
        """
        Produce all token-safe chunks for a single document.
        chunk_total is set after all chunks are determined.
        """
        raw_chunks: list[tuple[ChunkType, str, bool]] = []  # (type, text, truncated)

        # ── ISSUE_HEADER ─────────────────────────────────────────────────────
        if doc.issue_header_text.strip():
            text, trunc = self._enforce_token_limit(
                doc.issue_header_text, "ISSUE_HEADER", doc.ticket_id
            )
            if text.strip():
                raw_chunks.append((ChunkType.ISSUE_HEADER, text, trunc))

        # ── QUERY_BODY ───────────────────────────────────────────────────────
        if (
            doc.query_body_text.strip()
            and len(doc.query_body_text) >= self._min_query_body_chars
        ):
            for part, trunc in self._split_section(
                doc.query_body_text, "QUERY_BODY", doc.ticket_id
            ):
                if part.strip():
                    raw_chunks.append((ChunkType.QUERY_BODY, part, trunc))

        # ── RESOLUTION_RCA ───────────────────────────────────────────────────
        if doc.resolution_rca_text.strip():
            for part, trunc in self._split_section(
                doc.resolution_rca_text, "RESOLUTION_RCA", doc.ticket_id
            ):
                if part.strip():
                    raw_chunks.append((ChunkType.RESOLUTION_RCA, part, trunc))

        if not raw_chunks:
            LOGGER.warning("No chunks produced for ticket %s", doc.ticket_id)
            return []

        chunk_total = len(raw_chunks)
        chunks: list[TicketChunk] = []
        base_extra = doc.extra_metadata_dict()

        for chunk_index, (chunk_type, content, was_truncated) in enumerate(raw_chunks):
            chunk_id = _deterministic_id(doc.ticket_id, chunk_index, self._index_version)
            token_count = count_tokens(content, self._embedding_model)

            extra = dict(base_extra)
            extra["token_count"] = token_count
            if was_truncated:
                extra["truncated"] = True

            chunk = TicketChunk(
                id=chunk_id,
                document_id=document_id,
                ticket_id=doc.ticket_id,
                chunk_index=chunk_index,
                chunk_type=chunk_type,
                chunk_total=chunk_total,
                content=content,
                word_count=_word_count(content),
                content_hash=_sha256(content),
                embedding=None,
                client=doc.client,
                source_type="freshdesk",
                automation_label=doc.automation_label,
                escalation_flag=doc.escalation_flag,
                query_type=doc.query_type,
                issue_area=doc.issue_area,
                environment=doc.environment,
                has_rca=doc.has_rca,
                has_sop=doc.has_sop,
                rca_quality_score=doc.rca_quality_score,
                ticket_created_at=doc.ticket_created_at,
                ingestion_run_id=ingestion_run_id,
                index_version=self._index_version,
                extra_metadata=extra,
            )
            chunks.append(chunk)

        return chunks

    # ── Private helpers ────────────────────────────────────────────────────────

    def _split_section(
        self,
        text: str,
        section_name: str,
        ticket_id: str,
    ) -> list[tuple[str, bool]]:
        """
        Split a section into token-bounded parts.
        Returns list of (text, was_truncated) tuples.
        Each part is guaranteed to be <= max_input_tokens.
        """
        parts = safe_split_by_tokens(
            text,
            max_tokens=self._chunk_target_tokens,
            overlap_tokens=self._chunk_overlap_tokens,
            model=self._embedding_model,
        )

        result: list[tuple[str, bool]] = []
        for part in parts:
            enforced, trunc = self._enforce_token_limit(part, section_name, ticket_id)
            if enforced.strip():
                result.append((enforced, trunc))
        return result

    def _enforce_token_limit(
        self,
        text: str,
        section_name: str,
        ticket_id: str,
    ) -> tuple[str, bool]:
        """
        If text exceeds max_input_tokens, truncate it and return (truncated_text, True).
        Otherwise returns (text, False).
        This is the last-resort safety net — normal splitting should prevent this.
        """
        token_count = count_tokens(text, self._embedding_model)
        if token_count <= self._max_input_tokens:
            return text, False

        LOGGER.warning(
            "Ticket %s | %s chunk has %d tokens (limit %d). "
            "Truncating. Some content will be lost.",
            ticket_id, section_name, token_count, self._max_input_tokens,
        )
        truncated = truncate_to_token_limit(text, self._max_input_tokens, self._embedding_model)
        return truncated, True
