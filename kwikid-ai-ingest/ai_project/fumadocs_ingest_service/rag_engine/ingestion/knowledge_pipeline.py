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
import html as _html_module
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from rag_engine.chunking.chunk_quality_filter import ChunkQualityConfig, ChunkQualityFilter
from rag_engine.chunking.knowledge_chunker import KnowledgeChunker
from rag_engine.config.rag_settings import RagEngineSettings
from rag_engine.embedding.base import EmbeddingProvider
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass, KnowledgeClassifier
from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator
from rag_engine.ingestion.parsers.stackoverflow_parser import KnowledgeArticle, StackOverflowParser
from rag_engine.ingestion.tenant_mapper import TenantMapper
from observability.metrics import (
    record_knowledge_document_ingested,
    record_knowledge_document_rejected,
    record_knowledge_image_grounding_failure,
    record_knowledge_manual_review_required,
    record_knowledge_quality_failure,
)

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII redaction patterns (applied before any storage)
# ---------------------------------------------------------------------------
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b'), "[PRIVATE_IP]"),
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),                             "[SERVER_IP]"),
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'),   "[EMAIL]"),
    (re.compile(
        r'(?i)(password|passwd|pwd|secret|token|api[_\-]?key|bearer)'
        r'\s*[:=]\s*\S+',
    ),                                                                          r'\1=[REDACTED_CREDENTIAL]'),
    (re.compile(r'(?i)\b(?:bearer)\s+[A-Za-z0-9\-._~+/]+=*\b'),              "Bearer [REDACTED_TOKEN]"),
    (re.compile(r'(?i)\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis)://[^\s]+'), "[REDACTED_DB_CONNECTION]"),
    (re.compile(r'(?i)\bssh\s+[^\n]*?@[^ \n]+:[^\s]+'),                      "ssh [REDACTED_SSH_TARGET]"),
    (re.compile(r'(?i)\b(?:AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z\-_]{35}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9\-]{10,})\b'), "[REDACTED_API_KEY]"),
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
    chunks_rejected:     int   = 0   # quality filter rejections
    embeddings_generated: int  = 0
    pii_redactions:      int   = 0
    manual_review_flagged: int = 0
    duration_s:          float = 0.0

    def log_summary(self) -> None:
        LOGGER.info(
            "Knowledge ingestion: %d inserted, %d updated, %d skipped, "
            "%d rejected, %d failed | %d chunks embedded, %d quality-filtered | "
            "%d PII redactions | %d manual-review flags | %.1fs",
            self.articles_inserted, self.articles_updated, self.articles_skipped,
            self.articles_rejected, self.articles_failed,
            self.embeddings_generated, self.chunks_rejected,
            self.pii_redactions, self.manual_review_flagged, self.duration_s,
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
        self._validator  = KnowledgeQualityValidator()

        # Quality filter — shared across articles within a run; reset per run
        _qf_config = ChunkQualityConfig(
            min_content_chars       = settings.chunk_quality_min_content_chars,
            min_alpha_chars         = settings.chunk_quality_min_alpha_chars,
            min_token_count         = settings.chunk_quality_min_token_count,
            min_unique_token_ratio  = settings.chunk_quality_min_unique_ratio,
            max_boilerplate_density = settings.chunk_quality_max_boilerplate,
            encoded_token_ratio_max = settings.chunk_quality_encoded_token_ratio_max,
            pure_alpha_ratio_min    = settings.chunk_quality_pure_alpha_ratio_min,
        )
        self._quality_filter = ChunkQualityFilter(_qf_config)

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
        self._quality_filter.reset_batch()
        self._validator.reset_run()

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
                result.chunks_rejected    += article_result.get("chunks_rejected", 0)
                result.pii_redactions     += article_result["pii_redactions"]
                result.manual_review_flagged += article_result.get("manual_review_flagged", 0)
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
            # Human-feedback articles never have images
            "image_metadata":     [],
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
        """Process a single article through validate → classify → dedup → upsert plan."""
        out: dict = {
            "inserted": 0, "updated": 0, "skipped": 0,
            "rejected": 0, "chunks": 0, "pii_redactions": 0,
            "manual_review_flagged": 0,
            "chunk_records": [],
            "chunks_rejected": 0,
        }

        # Quality validation gate (WORK ITEM 4)
        validation = self._validator.validate(article)
        if not validation.is_valid:
            out["rejected"] = 1
            for issue in validation.errors:
                LOGGER.warning(
                    "Knowledge quality rejection article=%s code=%s: %s",
                    article.article_id, issue.code, issue.message,
                )
                record_knowledge_document_rejected(issue.code)
            return out

        if validation.manual_review_required:
            out["manual_review_flagged"] = 1
            record_knowledge_manual_review_required()
            LOGGER.info(
                "Knowledge article flagged for manual review article=%s warnings=%s",
                article.article_id, [i.code for i in validation.warnings],
            )

        # Emit image grounding failure metrics (WORK ITEM 7)
        for ref in getattr(article, "image_references", []):
            if not ref.manifest_found:
                record_knowledge_image_grounding_failure("no_manifest")
            elif ref.local_path is None:
                record_knowledge_image_grounding_failure("no_local_asset")

        # Classify
        clf = self._classifier.classify(article)
        if not clf.is_embeddable:
            out["rejected"] = 1
            LOGGER.debug(
                "Rejected article %s: class=%s reason=%s",
                article.article_id, clf.knowledge_class.value, clf.reject_reason,
            )
            record_knowledge_document_rejected(clf.reject_reason or clf.knowledge_class.value)
            return out

        # PII redaction
        q_body, q_n = _redact_pii(article.question_body)
        a_body, a_n = _redact_pii(article.answer_body or "")
        if not a_body:
            a_body = None
        title, t_n  = _redact_pii(article.title)
        out["pii_redactions"] = q_n + a_n + t_n
        if getattr(article, "manual_review_required", False):
            out["manual_review_flagged"] = 1

        # Reconstruct document with inline OCR injection.
        # Build a GUID → metadata lookup from image_metadata (populated by parser OCR).
        # Only include images where OCR actually ran and produced text.
        ocr_map: dict[str, dict] = {
            meta["image_guid"]: meta
            for meta in getattr(article, "image_metadata", [])
            if meta.get("image_guid")
            and meta.get("ocr_required")
            and meta.get("ocr_text")
        }
        if ocr_map:
            # Replace ![alt](SO_CDN_url) with [IMAGE: cleaned_ocr_text] at original position.
            # Images without OCR text are removed (same as before).
            # This preserves the reading order: text → [IMAGE: ocr] → text → code → ...
            q_body = _inject_ocr_inline(q_body, ocr_map)
            if a_body:
                a_body = _inject_ocr_inline(a_body, ocr_map)

        # Build embed text
        embed_text   = _build_embed_text_raw(
            title          = title,
            question_body  = q_body,
            answer_body    = a_body,
            knowledge_class= clf.knowledge_class,
            canonical_url  = getattr(article, "canonical_url", None),
            tags_raw       = article.tags_raw,
            completeness_score=getattr(article, "completeness_score", None),
            image_refs     = [ref.url for ref in getattr(article, "image_references", [])],
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
            # OCR image metadata (Part 2 — Subagent 1 populates this on KnowledgeArticle)
            "image_metadata":     getattr(article, "image_metadata", []),
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
            record_knowledge_document_rejected("no_chunks_produced")
            return out

        out["chunks"] = len(chunk_records)
        record_knowledge_document_ingested(clf.knowledge_class.value)

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
        """
        Split embed_text into semantic chunks using KnowledgeChunker and
        apply ChunkQualityFilter before building DB-ready records.
        """
        chunker = KnowledgeChunker(
            knowledge_class      = knowledge_class,
            chunk_target_tokens  = self._settings.chunk_target_tokens,
            chunk_overlap_tokens = self._settings.chunk_overlap_tokens,
            max_input_tokens     = self._settings.embedding_max_input_tokens,
            embedding_model      = self._settings.embedding_model,
            min_chunk_chars      = self._settings.knowledge_min_chunk_chars,
        )
        semantic_chunks = chunker.chunk(
            embed_text,
            article_id    = article_id,
            index_version = self._settings.index_version,
        )
        if not semantic_chunks:
            return []

        # Build raw record dicts for quality filtering
        raw_records: list[dict] = []
        for kc in semantic_chunks:
            raw_records.append({
                "id":              kc.chunk_id,
                "article_id":      article_id,
                "source_post_id":  source_post_id,
                "chunk_index":     kc.chunk_index,
                "chunk_type":      kc.chunk_type,
                "content":         kc.content,
                "word_count":      kc.word_count,
                "content_hash":    kc.content_hash,
                "embedding":       None,
                "clients":         clients,
                "tags_raw":        tags_raw,
                "knowledge_class": knowledge_class.value,
                "quality_score":   quality_score,
                "question_score":  question_score,
                "answer_score":    answer_score,
                "ingested_at":     datetime.now(timezone.utc).isoformat(),
                "index_version":   self._settings.index_version,
            })

        # Quality gate — reject boilerplate, tiny, or repetitive prose chunks.
        # Code chunks (CODE_SAMPLE, COMMAND) bypass the gate: short code blocks
        # (CSV schemas, single commands) are semantically valuable despite having
        # few whitespace-delimited tokens and failing min_token_count.
        code_records  = [r for r in raw_records if r["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
        prose_records = [r for r in raw_records if r["chunk_type"] not in ("CODE_SAMPLE", "COMMAND")]

        valid_prose, rejected = self._quality_filter.filter_batch(
            prose_records,
            content_key="content",
            context_key="article_id",
        )
        if rejected:
            LOGGER.info(
                "Knowledge article %s: %d/%d prose chunks rejected by quality filter",
                article_id, len(rejected), len(prose_records),
            )
            record_knowledge_quality_failure(count=len(rejected))

        return valid_prose + code_records

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

# Regex to strip raw image URL markdown strings from embed text.
# Used as a FALLBACK for images that have no OCR text — images with OCR
# are replaced inline by _inject_ocr_inline() before this runs.
_IMAGE_MD_RE = re.compile(
    r"!\[[^\]]*\]\(https?://[^\)]*stackoverflowteams[^\)]*\.(png|jpg|jpeg|gif|webp)\)",
    re.IGNORECASE,
)
# Strip bare CDN image URLs that appear without markdown alt-text wrappers.
_IMAGE_URL_BARE_RE = re.compile(
    r"https?://stackoverflowteams\.com/c/[^/]+/images/s/"
    r"[0-9a-fA-F\-]{32,36}\.(png|jpg|jpeg|gif|webp)",
    re.IGNORECASE,
)
# Strip embedded base64 data URIs (e.g. data:image/jpeg;base64,<20KB blob>).
# These appear when engineers paste images directly into SO bodyMarkdown instead
# of uploading to the CDN. The binary blob has zero semantic content.
_IMAGE_DATA_URI_RE = re.compile(
    r"!\[[^\]]*\]\(data:image/[^;]+;base64,[A-Za-z0-9+/=\r\n\s]+\)",
    re.IGNORECASE | re.DOTALL,
)
# Extract StackOverflow image GUID (UUID v4 with dashes) from any SO CDN URL.
# Matches the same GUID format produced by StackOverflowParser._extract_image_references().
_SO_IMAGE_GUID_RE = re.compile(
    r"/images/s/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.",
    re.IGNORECASE,
)

# Minimum OCR text length to inject — filters single-char extractions,
# empty results, and pure noise from decorative image regions.
_MIN_OCR_INJECT_CHARS = 20


def _clean_ocr_for_injection(raw_ocr: str) -> str:
    """
    Filter raw OCR output to retain only knowledge-bearing lines.

    The OCR engine extracts all text it finds in the image, including:
    - Browser chrome (tab labels, URL bar, reload button)
    - OS taskbar text (time, tray icons)
    - Repeated decorative labels
    - Single-character artifacts from image boundaries

    Filtering rules (each applied per line):
      - Skip lines shorter than 3 chars
      - Skip lines that are purely numeric or date/time patterns
        (timestamps, frame numbers, page numbers — not operational knowledge)

    Returns the cleaned text, or "" if nothing useful remains.
    """
    kept: list[str] = []
    for line in raw_ocr.split("\n"):
        line = line.strip()
        if len(line) < 3:
            continue
        # Pure timestamps / numeric junk: "12:34", "2024-01-15", "99", "  3  "
        if re.fullmatch(r"[\d:.\-/\s]+", line):
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    return result if len(result) >= _MIN_OCR_INJECT_CHARS else ""


def _inject_ocr_inline(text: str, ocr_map: dict[str, dict]) -> str:
    """
    Replace StackOverflow image markdown with inline OCR text, preserving position.

    For each ``![alt](https://stackoverflowteams.com/.../GUID.png)`` in text:
      - If OCR text is available for that GUID: replace with ``[IMAGE: <cleaned_ocr>]``
      - If no OCR text (image not OCR'd, OCR failed, or text too short): remove the
        markdown entirely (same behaviour as the previous _IMAGE_MD_RE.sub("", text)).

    This preserves the READING ORDER of the original article.  OCR text appears at
    the exact document position where the engineer placed the screenshot, not appended
    at the end as a detached blob.

    Args:
        text:    Question or answer body after PII redaction (still contains image markdown).
        ocr_map: {image_guid (lowercase, with dashes): image_metadata dict}
                 Only includes images where ocr_required=True and ocr_text is not None.

    Returns:
        Text with image markdown replaced by [IMAGE: ...] markers or removed.
    """
    def _replace(m: re.Match) -> str:
        # Extract StackOverflow image GUID from the matched URL string
        guid_m = _SO_IMAGE_GUID_RE.search(m.group(0))
        if not guid_m:
            return ""  # Malformed URL — drop
        guid = guid_m.group(1).lower()
        meta = ocr_map.get(guid)
        if not meta:
            return ""  # No OCR for this image — drop (same as before)
        raw_ocr = (meta.get("ocr_text") or "").strip()
        cleaned = _clean_ocr_for_injection(raw_ocr)
        if not cleaned:
            return ""  # OCR ran but result is noise — drop
        return f"\n[IMAGE: {cleaned}]\n"

    # Replace image markdown with OCR or empty (preserves position for OCR'd images)
    result = _IMAGE_MD_RE.sub(_replace, text)
    # Strip bare CDN URLs that are not wrapped in markdown alt-text syntax
    result = _IMAGE_URL_BARE_RE.sub("", result)
    return result


def _sanitize_for_embedding(text: str) -> str:
    """
    Prepare text for embedding by:
      1. Unescaping HTML entities (Q1): &lt; → <, &amp; → &, etc.
      2. Stripping any remaining SO CDN image markdown (Q4 fallback):
         at this point, images with OCR have already been replaced inline by
         _inject_ocr_inline(); only images with no OCR text remain as ![](url).
      3. Stripping bare SO CDN image URLs not wrapped in markdown.
      4. Stripping embedded base64 data URIs (binary blobs with no semantic content).
    """
    text = _html_module.unescape(text)
    # Fallback strip for images not replaced by _inject_ocr_inline (no OCR available)
    text = _IMAGE_MD_RE.sub("", text)
    text = _IMAGE_URL_BARE_RE.sub("", text)
    # Strip embedded base64 data URIs (e.g. so_1679's 20KB JPEG blob)
    text = _IMAGE_DATA_URI_RE.sub("", text)
    return text


def _build_embed_text_raw(
    *,
    title:          str,
    question_body:  str,
    answer_body:    Optional[str],
    knowledge_class: KnowledgeClass,
    canonical_url: Optional[str] = None,
    tags_raw: Optional[list[str]] = None,
    completeness_score: Optional[float] = None,
    image_refs: Optional[list[str]] = None,
) -> str:
    """
    Build embed-ready text from a Q&A article.

    Structure:
        QUESTION: {title}
        [CANONICAL_URL: {url}]
        [TAGS: {tag1}, {tag2}]
        [COMPLETENESS_SCORE: {score}]
        {question_body}

        ANSWER:
        {answer_body}

    Image URL markdown strings are stripped BEFORE embedding to prevent CDN
    URLs from polluting the vector space (Q4).  HTML entities are unescaped
    first so the embedded text is human-readable (Q1).

    Note: image_refs parameter is accepted for API compatibility but image URLs
    are NOT appended to the embed text — they are stored separately in the
    image_metadata JSONB column and do not belong in semantic embeddings.
    """
    # Q1 + Q4: sanitize before building embed text
    title_clean        = _sanitize_for_embedding(title)
    question_body_clean = _sanitize_for_embedding(question_body)
    answer_body_clean  = _sanitize_for_embedding(answer_body) if answer_body else None

    parts = [f"QUESTION: {title_clean}"]
    if canonical_url:
        parts.append(f"CANONICAL_URL: {canonical_url}")
    if tags_raw:
        parts.append(f"TAGS: {', '.join(tags_raw)}")
    if completeness_score is not None:
        parts.append(f"COMPLETENESS_SCORE: {completeness_score:.3f}")
    # image_refs intentionally NOT appended — image URLs have no semantic value
    # for vector embeddings; OCR text is stored in image_metadata JSONB (article level),
    # NOT in the body, per design: structured metadata path, not embedding path.
    parts.append(question_body_clean)
    if answer_body_clean:
        label = "VERIFIED ANSWER" if knowledge_class == KnowledgeClass.VERIFIED_REPLY else "ANSWER"
        parts.append(f"{label}:\n{answer_body_clean}")
    return "\n\n".join(parts)
