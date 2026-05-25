"""
rag_engine/ingestion/knowledge_pipeline.py

Phase B3 knowledge ingestion pipeline.

Flow:
    StackOverflowParser.parse()
        → PostFilter (delete/meta/length guards)
        → TenantMapper.map_tags()
        → KnowledgeClassifier.classify()
        → PII Redactor
        → KnowledgeDocumentBuilder (Q+A → embed-ready text)
        → SHA256 dedup check against rag_knowledge_articles
        → Text chunker (reuses word/token approach from SOP pipeline)
        → BatchEmbedding (embed_batch, circuit breaker, retry)
        → Upsert rag_knowledge_articles + rag_knowledge_chunks

Key invariants:
  - ESCALATION articles NEVER reach rag_knowledge_chunks
  - PII is redacted BEFORE any storage (article table + chunks)
  - Content hash dedup: unchanged articles produce 0 DB writes (idempotent)
  - Chunk IDs are deterministic: uuid5(NAMESPACE_URL, "knowledge:{article_id}:{idx}:{version}")
  - All errors per article are caught and counted; the run does NOT abort
  - Dry-run mode: no DB writes, logs intent only

Usage:
    pipeline = KnowledgePipeline(settings, supabase_client, embedder)
    result = pipeline.run(data_dir=Path("../More_data"), dry_run=True)
    result.log_summary()
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from rag_engine.config.rag_settings import RagEngineSettings
from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass, KnowledgeClassifier
from rag_engine.ingestion.parsers.stackoverflow_parser import KnowledgeArticle, StackOverflowParser
from rag_engine.ingestion.tenant_mapper import TenantMapper

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII redaction patterns (applied before any storage)
# ---------------------------------------------------------------------------
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),                             "[SERVER_IP]"),
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'),   "[EMAIL]"),
    (re.compile(
        r'(?i)(password|passwd|pwd|secret|token|api[_\-]?key|bearer)'
        r'\s*[:=]\s*\S+',
    ),                                                                          r'\1=[REDACTED_CREDENTIAL]'),
    (re.compile(r'\b[A-Za-z0-9+/]{40,}={0,2}\b'),                            "[TOKEN]"),
]


def _redact_pii(text: str) -> tuple[str, int]:
    """Apply PII redaction patterns. Returns (redacted_text, redaction_count)."""
    count = 0
    for pattern, replacement in _PII_PATTERNS:
        new_text, n = pattern.subn(replacement, text)
        count += n
        text = new_text
    return text, count


# ---------------------------------------------------------------------------
# Chunk ID generation (deterministic, matches SOP pipeline convention)
# ---------------------------------------------------------------------------

def _knowledge_chunk_id(article_id: str, chunk_index: int, index_version: str) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"knowledge:{article_id}:{chunk_index}:{index_version}",
    ))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Simple word-based chunker for knowledge documents
# ---------------------------------------------------------------------------

def _chunk_text(
    text: str,
    target_words: int = 350,
    overlap_words: int = 50,
    min_chunk_chars: int = 100,
) -> list[str]:
    """
    Split text into overlapping chunks by word count.

    For knowledge articles (300–1500 words typically), this produces 1–4 chunks.
    Short articles fit in a single chunk.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + target_words, len(words))
        chunk = " ".join(words[start:end])
        if len(chunk) >= min_chunk_chars:
            chunks.append(chunk)
        elif chunks:
            # Append short tail to previous chunk (avoid tiny orphan chunks)
            chunks[-1] = chunks[-1] + " " + chunk
        else:
            # First chunk is short — keep it anyway
            chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap_words
        if start < 0:
            start = 0

    return chunks


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeIngestionResult:
    articles_processed:  int   = 0
    articles_inserted:   int   = 0
    articles_updated:    int   = 0
    articles_skipped:    int   = 0   # content unchanged
    articles_rejected:   int   = 0   # classifier rejected
    articles_failed:     int   = 0   # unexpected error
    chunks_created:      int   = 0
    embeddings_generated: int  = 0
    pii_redactions:      int   = 0
    duration_s:          float = 0.0

    def log_summary(self) -> None:
        LOGGER.info(
            "Knowledge ingestion: %d inserted, %d updated, %d skipped, "
            "%d rejected, %d failed | %d chunks embedded | %d PII redactions | %.1fs",
            self.articles_inserted, self.articles_updated, self.articles_skipped,
            self.articles_rejected, self.articles_failed,
            self.embeddings_generated, self.pii_redactions, self.duration_s,
        )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

class KnowledgePipeline:
    """
    Orchestrates Phase B3 knowledge ingestion.

    All public methods return KnowledgeIngestionResult — they never raise.
    Per-article errors are caught and counted.

    Usage:
        pipeline = KnowledgePipeline(settings, supabase_client, embedder)
        result = pipeline.run(data_dir=Path("../More_data"), dry_run=False)
        result.log_summary()
    """

    def __init__(
        self,
        settings: RagEngineSettings,
        supabase_client: Any,
        embedding_provider: EmbeddingProvider,
        tenant_mapper: Optional[TenantMapper] = None,
    ) -> None:
        self._settings   = settings
        self._client     = supabase_client
        self._embedder   = embedding_provider
        self._mapper     = tenant_mapper or TenantMapper.from_env()
        self._classifier = KnowledgeClassifier()
        self._parser     = StackOverflowParser()

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(
        self,
        data_dir: Path,
        *,
        dry_run: bool = False,
        client_filter: Optional[str] = None,
    ) -> KnowledgeIngestionResult:
        """
        Full ingestion run for a More_data/ directory.

        Args:
            data_dir:      Path to the SO for Teams export directory
            dry_run:       If True, parse and classify but do not write to DB
            client_filter: If set, only process articles whose clients includes
                           this slug (or global articles)
        """
        result  = KnowledgeIngestionResult()
        t_start = time.monotonic()

        LOGGER.info(
            "KnowledgePipeline: starting run data_dir=%s dry_run=%s client_filter=%s",
            data_dir, dry_run, client_filter,
        )

        # Step 1: Parse
        articles = self._parser.parse(data_dir)
        if not articles:
            LOGGER.warning("KnowledgePipeline: no articles parsed from %s", data_dir)
            result.duration_s = time.monotonic() - t_start
            return result

        # Step 2: Assign clients via TenantMapper
        for article in articles:
            article.clients = self._mapper.map_tags(article.tags_raw)

        # Step 3: Apply client_filter if requested
        if client_filter:
            articles = [
                a for a in articles
                if not a.clients or client_filter in a.clients
            ]
            LOGGER.info("KnowledgePipeline: %d articles after client_filter=%s", len(articles), client_filter)

        # Step 4: Process each article
        to_embed: list[dict] = []

        for article in articles:
            result.articles_processed += 1
            try:
                article_result = self._process_article(article, dry_run=dry_run)
                result.articles_inserted  += article_result["inserted"]
                result.articles_updated   += article_result["updated"]
                result.articles_skipped   += article_result["skipped"]
                result.articles_rejected  += article_result["rejected"]
                result.chunks_created     += article_result["chunks"]
                result.pii_redactions     += article_result["pii_redactions"]
                if article_result.get("chunk_records"):
                    to_embed.extend(article_result["chunk_records"])
            except Exception as exc:  # noqa: BLE001
                result.articles_failed += 1
                LOGGER.error(
                    "KnowledgePipeline: unexpected error processing article %s: %s",
                    article.article_id, exc, exc_info=True,
                )

        # Step 5: Batch-embed all pending chunks
        if to_embed and not dry_run:
            embedded = self._batch_embed_and_upsert(to_embed)
            result.embeddings_generated = embedded

        result.duration_s = time.monotonic() - t_start
        result.log_summary()
        return result

    def ingest_verified_reply(
        self,
        *,
        title: str,
        query_text: str,
        final_response: str,
        client: str,
        source_ticket_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> KnowledgeIngestionResult:
        """
        Ingest a human-reviewed VERIFIED_REPLY as a knowledge article.

        Called by the feedback endpoint when human_action=APPROVED or EDITED.
        The final_response is treated as the authoritative answer.

        Security:
          - client must be a non-empty string (validated by caller)
          - content is PII-redacted before storage
          - quality_score is set to 0.90 (APPROVED) - high trust
        """
        result  = KnowledgeIngestionResult()
        t_start = time.monotonic()

        # Build a synthetic KnowledgeArticle from the human feedback
        # Cap at 2e9 to stay within PostgreSQL INTEGER range (max 2,147,483,647).
        post_id_hash = int(hashlib.sha256(f"{client}:{title}:{query_text}".encode()).hexdigest()[:8], 16) % 2_000_000_000
        article_id   = f"verified_{client}_{post_id_hash}"

        q_body_redacted, q_pii = _redact_pii(query_text)
        a_body_redacted, a_pii = _redact_pii(final_response)
        result.pii_redactions  = q_pii + a_pii

        embed_text   = _build_embed_text_raw(
            title=title,
            question_body=q_body_redacted,
            answer_body=a_body_redacted,
            knowledge_class=KnowledgeClass.VERIFIED_REPLY,
        )
        content_hash = _sha256(embed_text)

        # Check for duplicate
        if not dry_run:
            existing = self._fetch_existing_article(article_id)
            if existing and existing.get("content_hash") == content_hash:
                result.articles_skipped = 1
                result.duration_s = time.monotonic() - t_start
                return result

        article_record = {
            "article_id":         article_id,
            "source":             "human_feedback",
            "source_post_id":     post_id_hash,
            "article_type":       "qa_pair",
            "title":              title[:500],
            "question_body":      q_body_redacted[:5000],
            "answer_body":        a_body_redacted[:5000],
            "answer_post_id":     None,
            "answer_score":       10,
            "question_score":     5,
            "view_count":         0,
            "accepted_answer_id": 1,
            "tags_raw":           [client],
            "clients":            [client],
            "knowledge_class":    KnowledgeClass.VERIFIED_REPLY.value,
            "quality_score":      0.90,
            "content_hash":       content_hash,
            "is_active":          True,
            "post_state":         "Published",
            "created_at_source":  None,
            "ingested_at":        datetime.now(timezone.utc).isoformat(),
            "updated_at":         datetime.now(timezone.utc).isoformat(),
            "index_version":      self._settings.index_version,
            "ingestion_run_id":   None,
        }

        chunks_raw = self._build_chunk_records(
            article_id      = article_id,
            embed_text      = embed_text,
            clients         = [client],
            tags_raw        = [client],
            knowledge_class = KnowledgeClass.VERIFIED_REPLY,
            quality_score   = 0.90,
            question_score  = 5,
            answer_score    = 10,
            source_post_id  = post_id_hash,
        )

        if not dry_run:
            try:
                self._upsert_article(article_record)
                embedded = self._batch_embed_and_upsert(chunks_raw)
                result.articles_inserted   = 1
                result.chunks_created      = len(chunks_raw)
                result.embeddings_generated = embedded
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("ingest_verified_reply failed for %s: %s", article_id, exc, exc_info=True)
                result.articles_failed = 1
        else:
            LOGGER.info(
                "[DRY RUN] Would ingest VERIFIED_REPLY article=%s chunks=%d",
                article_id, len(chunks_raw),
            )
            result.articles_inserted = 1
            result.chunks_created    = len(chunks_raw)

        result.duration_s = time.monotonic() - t_start
        return result

    # ── Private: per-article processing ───────────────────────────────────────

    def _process_article(self, article: KnowledgeArticle, *, dry_run: bool) -> dict:
        """Process a single article through classify → dedup → upsert plan."""
        out: dict = {
            "inserted": 0, "updated": 0, "skipped": 0,
            "rejected": 0, "chunks": 0, "pii_redactions": 0,
            "chunk_records": [],
        }

        # Classify
        clf = self._classifier.classify(article)
        if not clf.is_embeddable:
            out["rejected"] = 1
            LOGGER.debug(
                "Rejected article %s: class=%s reason=%s",
                article.article_id, clf.knowledge_class.value, clf.reject_reason,
            )
            return out

        # PII redaction
        q_body, q_n = _redact_pii(article.question_body)
        a_body, a_n = _redact_pii(article.answer_body or "")
        if not a_body:
            a_body = None
        title, t_n  = _redact_pii(article.title)
        out["pii_redactions"] = q_n + a_n + t_n

        # Build embed text
        embed_text   = _build_embed_text_raw(
            title          = title,
            question_body  = q_body,
            answer_body    = a_body,
            knowledge_class= clf.knowledge_class,
        )
        content_hash = _sha256(embed_text)

        # Dedup check
        is_update = False
        if not dry_run:
            existing = self._fetch_existing_article(article.article_id)
            if existing:
                if existing.get("content_hash") == content_hash:
                    out["skipped"] = 1
                    return out
                is_update = True

        # Build DB record
        article_record = {
            "article_id":         article.article_id,
            "source":             article.source,
            "source_post_id":     article.source_post_id,
            "article_type":       article.article_type,
            "title":              title[:500],
            "question_body":      q_body[:8000],
            "answer_body":        (a_body[:8000] if a_body else None),
            "answer_post_id":     article.answer_post_id,
            "answer_score":       article.answer_score,
            "question_score":     article.question_score,
            "view_count":         article.view_count,
            "accepted_answer_id": article.accepted_answer_id,
            "tags_raw":           article.tags_raw,
            "clients":            article.clients,
            "knowledge_class":    clf.knowledge_class.value,
            "quality_score":      clf.quality_score,
            "content_hash":       content_hash,
            "is_active":          True,
            "post_state":         article.post_state,
            "created_at_source":  article.created_at_source,
            "ingested_at":        datetime.now(timezone.utc).isoformat(),
            "updated_at":         datetime.now(timezone.utc).isoformat(),
            "index_version":      self._settings.index_version,
        }

        # Build chunk records
        chunk_records = self._build_chunk_records(
            article_id      = article.article_id,
            embed_text      = embed_text,
            clients         = article.clients,
            tags_raw        = article.tags_raw,
            knowledge_class = clf.knowledge_class,
            quality_score   = clf.quality_score,
            question_score  = article.question_score,
            answer_score    = article.answer_score,
            source_post_id  = article.source_post_id,
        )

        if not chunk_records:
            out["rejected"] = 1
            LOGGER.warning("No chunks produced for article %s — skipping", article.article_id)
            return out

        out["chunks"] = len(chunk_records)

        if dry_run:
            action = "update" if is_update else "insert"
            LOGGER.info(
                "[DRY RUN] Would %s article=%s class=%s quality=%.3f chunks=%d clients=%s",
                action, article.article_id, clf.knowledge_class.value,
                clf.quality_score, len(chunk_records), article.clients,
            )
            out["inserted" if not is_update else "updated"] = 1
            return out

        # Persist article record
        if is_update:
            self._delete_article_chunks(article.article_id)
        self._upsert_article(article_record)

        out["inserted" if not is_update else "updated"] = 1
        out["chunk_records"] = chunk_records
        return out

    # ── Private: chunk building ────────────────────────────────────────────────

    def _build_chunk_records(
        self,
        *,
        article_id:      str,
        embed_text:      str,
        clients:         list[str],
        tags_raw:        list[str],
        knowledge_class: KnowledgeClass,
        quality_score:   float,
        question_score:  int,
        answer_score:    int,
        source_post_id:  int,
    ) -> list[dict]:
        """Split embed_text into chunks and build DB-ready dicts."""
        raw_chunks = _chunk_text(
            embed_text,
            target_words   = 350,
            overlap_words  = 50,
            min_chunk_chars= self._settings.knowledge_min_chunk_chars,
        )
        if not raw_chunks:
            return []

        # Map knowledge_class to chunk_type label
        chunk_type = self._knowledge_class_to_chunk_type(knowledge_class)

        records: list[dict] = []
        for idx, content in enumerate(raw_chunks):
            content_hash = _sha256(content)
            chunk_id     = _knowledge_chunk_id(article_id, idx, self._settings.index_version)
            records.append({
                "id":              chunk_id,
                "article_id":      article_id,
                "source_post_id":  source_post_id,
                "chunk_index":     idx,
                "chunk_type":      chunk_type,
                "content":         content,
                "word_count":      len(content.split()),
                "content_hash":    content_hash,
                "embedding":       None,  # filled during batch embed
                "clients":         clients,
                "tags_raw":        tags_raw,
                "knowledge_class": knowledge_class.value,
                "quality_score":   quality_score,
                "question_score":  question_score,
                "answer_score":    answer_score,
                "ingested_at":     datetime.now(timezone.utc).isoformat(),
                "index_version":   self._settings.index_version,
            })
        return records

    @staticmethod
    def _knowledge_class_to_chunk_type(klass: KnowledgeClass) -> str:
        """Map KnowledgeClass to the chunk_type stored in the DB."""
        return {
            KnowledgeClass.VERIFIED_REPLY:  "VERIFIED_REPLY",
            KnowledgeClass.TROUBLESHOOTING: "TROUBLESHOOTING",
            KnowledgeClass.FAQ:             "FAQ_ANSWER",
            KnowledgeClass.POLICY:          "POLICY",
            KnowledgeClass.RCA:             "RCA",
        }.get(klass, "FAQ_ANSWER")

    # ── Private: batch embedding + upsert ─────────────────────────────────────

    def _batch_embed_and_upsert(self, chunk_records: list[dict]) -> int:
        """Embed chunks in batches and upsert to rag_knowledge_chunks."""
        batch_size    = self._settings.embedding_batch_size
        embedded_total = 0
        db_keys       = {k for k in chunk_records[0] if k != "embedding"} | {"embedding"} if chunk_records else set()

        for batch_start in range(0, len(chunk_records), batch_size):
            batch = chunk_records[batch_start : batch_start + batch_size]
            texts = [c["content"] for c in batch]

            try:
                embed_result = self._embedder.embed_batch(texts)
                for chunk, embedding in zip(batch, embed_result.embeddings):
                    chunk["embedding"] = embedding
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "Knowledge embedding batch %d failed (%d chunks): %s",
                    batch_start // batch_size + 1, len(batch), exc,
                )
                continue

            rows = [
                {k: v for k, v in c.items() if k in db_keys}
                for c in batch
                if c.get("embedding") is not None
            ]
            if not rows:
                continue

            try:
                self._client.table(self._settings.knowledge_chunks_table).upsert(
                    rows,
                    on_conflict="article_id,chunk_index,index_version",
                ).execute()
                embedded_total += len(rows)
                LOGGER.debug(
                    "Knowledge batch %d: upserted %d chunks",
                    batch_start // batch_size + 1, len(rows),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "Knowledge chunk upsert failed for batch %d: %s",
                    batch_start // batch_size + 1, exc,
                )

        return embedded_total

    # ── Private: Supabase helpers ──────────────────────────────────────────────

    def _fetch_existing_article(self, article_id: str) -> Optional[dict]:
        try:
            resp = (
                self._client.table(self._settings.knowledge_articles_table)
                .select("article_id, content_hash, is_active")
                .eq("article_id", article_id)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Could not fetch existing knowledge article %s: %s", article_id, exc)
            return None

    def _upsert_article(self, record: dict) -> None:
        self._client.table(self._settings.knowledge_articles_table).upsert(
            record, on_conflict="article_id"
        ).execute()

    def _delete_article_chunks(self, article_id: str) -> None:
        try:
            self._client.table(self._settings.knowledge_chunks_table).delete().eq(
                "article_id", article_id
            ).execute()
            LOGGER.debug("Deleted old chunks for knowledge article %s", article_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to delete old knowledge chunks for %s: %s", article_id, exc)


# ---------------------------------------------------------------------------
# Free function (used by ingest_verified_reply)
# ---------------------------------------------------------------------------

def _build_embed_text_raw(
    *,
    title:          str,
    question_body:  str,
    answer_body:    Optional[str],
    knowledge_class: KnowledgeClass,
) -> str:
    parts = [f"QUESTION: {title}", question_body]
    if answer_body:
        label = "VERIFIED ANSWER" if knowledge_class == KnowledgeClass.VERIFIED_REPLY else "ANSWER"
        parts.append(f"{label}:\n{answer_body}")
    return "\n\n".join(parts)
