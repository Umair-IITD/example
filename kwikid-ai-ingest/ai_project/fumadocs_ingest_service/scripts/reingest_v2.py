#!/usr/bin/env python3
"""
scripts/reingest_v2.py

Clean re-ingestion script for migrating B1 RAG data from index_version=v1
to index_version=v2 (token-aware chunking).

What it does:
  1. Reads source documents from Supabase B1 tables (rag_ticket_documents,
     rag_sop_library, rag_knowledge_articles)
  2. Re-chunks each document using the token-aware chunker_v2
     (replaces word-count-based v1 chunking)
  3. Re-embeds chunks using the OpenAI embedding API
  4. Upserts into the B1 chunk tables with index_version=v2
     (deterministic chunk IDs → safe idempotent re-runs)

Design:
  - v1 and v2 data coexist in the same tables (different index_version rows)
  - ACTIVE_INDEX_VERSION controls which version is served at query time
  - Zero-downtime migration: set WRITE_INDEX_VERSION=v2 here, then flip
    ACTIVE_INDEX_VERSION=v2 in the application once ingestion is validated
  - --dry-run flag prints what WOULD be done without writing anything
  - --rollback deletes all v2 rows for the specified client/table

Usage:
  # Full re-ingestion of all tables for a specific tenant
  python scripts/reingest_v2.py --client unity_bank

  # Dry run — show what would be processed without writing
  python scripts/reingest_v2.py --client unity_bank --dry-run

  # Reingest only ticket chunks
  python scripts/reingest_v2.py --client unity_bank --tables ticket

  # Rollback: delete all v2 rows for a client
  python scripts/reingest_v2.py --client unity_bank --rollback

  # All tenants (all rows in rag_ticket_documents)
  python scripts/reingest_v2.py --all-clients

Environment:
  SUPABASE_URL           — required
  SUPABASE_KEY           — required (service role key)
  OPENAI_API_KEY         — required for embedding
  EMBEDDING_MODEL        — default: text-embedding-3-small
  CHUNK_TARGET_TOKENS    — default: 1200
  EMBEDDING_MAX_INPUT_TOKENS — default: 7000
  CHUNK_OVERLAP_TOKENS   — default: 150
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("reingest_v2")

# Add the project root to the Python path so imports work from scripts/
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv()


# ── Ingestion result tracking ─────────────────────────────────────────────────

@dataclass
class ReingestStats:
    table: str = ""
    client: str = ""
    source_docs_fetched: int = 0
    chunks_produced: int = 0
    chunks_upserted: int = 0
    chunks_skipped: int = 0
    embed_errors: int = 0
    upsert_errors: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)


# ── Embedding client (minimal — no retry for script simplicity) ───────────────

def _embed_batch(texts: list[str], api_key: str, model: str) -> list[list[float]]:
    """Embed a batch of texts using the OpenAI API. Returns list of embedding vectors."""
    try:
        import openai  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "openai package not installed. Run: pip install openai"
        )
    client = openai.OpenAI(api_key=api_key)
    response = client.embeddings.create(input=texts, model=model)
    return [item.embedding for item in response.data]


# ── Chunker v2 adapter ────────────────────────────────────────────────────────

def _chunk_text(
    content: str,
    source_type: str,
    source_id: str,
    title: str,
    *,
    target_tokens: int,
    max_input_tokens: int,
    overlap_tokens: int,
) -> list[dict[str, Any]]:
    """Chunk content using chunker_v2 and return list of chunk dicts."""
    from app.chunker_v2 import SourceDocument, chunk_document_v2  # noqa: PLC0415

    doc = SourceDocument(
        source_type=source_type,
        source_id=source_id,
        content=content,
        title=title,
        heading=None,
        tags=[],
        creation_date=None,
        metadata={},
    )
    chunks = chunk_document_v2(
        doc,
        target_tokens=target_tokens,
        max_input_tokens=max_input_tokens,
        overlap_tokens=overlap_tokens,
        strategy="auto",
    )
    return [
        {
            "chunk_id": c.chunk_id,
            "content": c.content,
            "chunk_index": c.chunk_index,
            "token_count": c.token_count,
            "content_hash": c.content_hash,
        }
        for c in chunks
    ]


# ── Table-specific reingest functions ─────────────────────────────────────────

def _reingest_ticket_chunks(
    supabase: Any,
    client: str,
    *,
    target_tokens: int,
    max_input_tokens: int,
    overlap_tokens: int,
    embed_batch_size: int,
    api_key: str,
    model: str,
    upsert_batch_size: int,
    dry_run: bool,
    verbose: bool,
) -> ReingestStats:
    stats = ReingestStats(table="rag_ticket_chunks", client=client)
    t0 = time.perf_counter()

    LOGGER.info("[ticket] Fetching rag_ticket_documents for client=%s ...", client)
    resp = (
        supabase.table("rag_ticket_documents")
        .select("id, ticket_id, source_type, subject, document_text, automation_label, "
                "has_rca, has_sop, query_type, issue_area, environment, "
                "escalation_flag, ingestion_run_id, ticket_created_at")
        .eq("client", client)
        .execute()
    )
    docs = resp.data or []
    stats.source_docs_fetched = len(docs)
    LOGGER.info("[ticket] Fetched %d source documents", len(docs))

    chunk_rows: list[dict[str, Any]] = []

    for doc in docs:
        text = (doc.get("document_text") or "").strip()
        if not text:
            if verbose:
                LOGGER.debug("[ticket] Skipping empty document ticket_id=%s", doc.get("ticket_id"))
            continue

        chunks = _chunk_text(
            text,
            source_type=doc.get("source_type", "freshdesk"),
            source_id=doc.get("ticket_id", doc["id"]),
            title=doc.get("subject", ""),
            target_tokens=target_tokens,
            max_input_tokens=max_input_tokens,
            overlap_tokens=overlap_tokens,
        )

        for c in chunks:
            chunk_rows.append({
                "id_str":           c["chunk_id"],
                "document_id":      doc["id"],
                "ticket_id":        doc.get("ticket_id", ""),
                "source_type":      doc.get("source_type", "freshdesk"),
                "client":           client,
                "chunk_index":      c["chunk_index"],
                "chunk_type":       "QUERY_BODY",
                "chunk_total":      len(chunks),
                "content":          c["content"],
                "word_count":       len(c["content"].split()),
                "content_hash":     c["content_hash"],
                "automation_label": doc.get("automation_label", "AUTO_REPLY"),
                "escalation_flag":  doc.get("escalation_flag", False),
                "query_type":       doc.get("query_type"),
                "issue_area":       doc.get("issue_area"),
                "environment":      doc.get("environment"),
                "has_rca":          doc.get("has_rca", False),
                "has_sop":          doc.get("has_sop", False),
                "rca_quality_score": 0,
                "extra_metadata":   {},
                "ticket_created_at": doc.get("ticket_created_at"),
                "ingestion_run_id": doc.get("ingestion_run_id"),
                "index_version":    "v2",
            })

    stats.chunks_produced = len(chunk_rows)
    LOGGER.info("[ticket] Produced %d chunks (token-aware v2)", len(chunk_rows))

    if dry_run:
        LOGGER.info("[ticket] DRY RUN — skipping embed and upsert")
        stats.duration_s = time.perf_counter() - t0
        return stats

    _embed_and_upsert(
        supabase, chunk_rows, "rag_ticket_chunks",
        api_key=api_key, model=model,
        embed_batch_size=embed_batch_size,
        upsert_batch_size=upsert_batch_size,
        stats=stats, verbose=verbose,
    )

    stats.duration_s = time.perf_counter() - t0
    return stats


def _reingest_sop_chunks(
    supabase: Any,
    client: str,
    *,
    target_tokens: int,
    max_input_tokens: int,
    overlap_tokens: int,
    embed_batch_size: int,
    api_key: str,
    model: str,
    upsert_batch_size: int,
    dry_run: bool,
    verbose: bool,
) -> ReingestStats:
    stats = ReingestStats(table="rag_sop_chunks", client=client)
    t0 = time.perf_counter()

    LOGGER.info("[sop] Fetching rag_sop_library for client=%s ...", client)
    # SOP library uses clients[] — fetch global (empty) + client-specific
    resp = supabase.table("rag_sop_library").select(
        "sop_id, title, content, query_type, issue_area, clients, version, is_active"
    ).execute()
    all_sops = resp.data or []
    # Filter: global or contains client
    sops = [
        s for s in all_sops
        if s.get("is_active", True) and (
            not s.get("clients") or client in s.get("clients", [])
        )
    ]
    stats.source_docs_fetched = len(sops)
    LOGGER.info("[sop] Found %d applicable SOPs", len(sops))

    chunk_rows: list[dict[str, Any]] = []

    for sop in sops:
        text = (sop.get("content") or "").strip()
        if not text:
            continue
        chunks = _chunk_text(
            text,
            source_type="sop",
            source_id=sop["sop_id"],
            title=sop.get("title", ""),
            target_tokens=target_tokens,
            max_input_tokens=max_input_tokens,
            overlap_tokens=overlap_tokens,
        )
        for c in chunks:
            chunk_rows.append({
                "id_str":        c["chunk_id"],
                "sop_id":        sop["sop_id"],
                "chunk_heading": sop.get("title", ""),
                "chunk_index":   c["chunk_index"],
                "chunk_type":    "SOP_STEPS",
                "content":       c["content"],
                "word_count":    len(c["content"].split()),
                "content_hash":  c["content_hash"],
                "clients":       sop.get("clients", []),
                "query_type":    sop.get("query_type"),
                "issue_area":    sop.get("issue_area"),
                "sop_version":   sop.get("version", 1),
                "index_version": "v2",
            })

    stats.chunks_produced = len(chunk_rows)
    LOGGER.info("[sop] Produced %d SOP chunks", len(chunk_rows))

    if dry_run:
        LOGGER.info("[sop] DRY RUN — skipping embed and upsert")
        stats.duration_s = time.perf_counter() - t0
        return stats

    _embed_and_upsert(
        supabase, chunk_rows, "rag_sop_chunks",
        api_key=api_key, model=model,
        embed_batch_size=embed_batch_size,
        upsert_batch_size=upsert_batch_size,
        stats=stats, verbose=verbose,
    )

    stats.duration_s = time.perf_counter() - t0
    return stats


def _reingest_knowledge_chunks(
    supabase: Any,
    client: str,
    *,
    target_tokens: int,
    max_input_tokens: int,
    overlap_tokens: int,
    embed_batch_size: int,
    api_key: str,
    model: str,
    upsert_batch_size: int,
    dry_run: bool,
    verbose: bool,
) -> ReingestStats:
    stats = ReingestStats(table="rag_knowledge_chunks", client=client)
    t0 = time.perf_counter()

    LOGGER.info("[knowledge] Fetching rag_knowledge_articles for client=%s ...", client)
    resp = supabase.table("rag_knowledge_articles").select(
        "id, article_id, title, question_body, answer_body, "
        "knowledge_class, quality_score, clients, is_active"
    ).execute()
    all_articles = resp.data or []
    articles = [
        a for a in all_articles
        if a.get("is_active", True) and (
            not a.get("clients") or client in a.get("clients", [])
        ) and float(a.get("quality_score", 0)) >= 0.40
    ]
    stats.source_docs_fetched = len(articles)
    LOGGER.info("[knowledge] Found %d applicable articles (quality_score>=0.40)", len(articles))

    chunk_rows: list[dict[str, Any]] = []

    for article in articles:
        q_body = (article.get("question_body") or "").strip()
        a_body = (article.get("answer_body") or "").strip()
        text = "\n\n".join(filter(None, [q_body, a_body]))
        if not text:
            continue
        chunks = _chunk_text(
            text,
            source_type="knowledge",
            source_id=article["article_id"],
            title=article.get("title", ""),
            target_tokens=target_tokens,
            max_input_tokens=max_input_tokens,
            overlap_tokens=overlap_tokens,
        )
        for c in chunks:
            chunk_rows.append({
                "id_str":          c["chunk_id"],
                "article_id":      article["article_id"],
                "chunk_index":     c["chunk_index"],
                "chunk_type":      "KNOWLEDGE",
                "content":         c["content"],
                "word_count":      len(c["content"].split()),
                "content_hash":    c["content_hash"],
                "knowledge_class": article.get("knowledge_class", "FAQ"),
                "quality_score":   float(article.get("quality_score", 0.0)),
                "clients":         article.get("clients", []),
                "index_version":   "v2",
            })

    stats.chunks_produced = len(chunk_rows)
    LOGGER.info("[knowledge] Produced %d knowledge chunks", len(chunk_rows))

    if dry_run:
        LOGGER.info("[knowledge] DRY RUN — skipping embed and upsert")
        stats.duration_s = time.perf_counter() - t0
        return stats

    _embed_and_upsert(
        supabase, chunk_rows, "rag_knowledge_chunks",
        api_key=api_key, model=model,
        embed_batch_size=embed_batch_size,
        upsert_batch_size=upsert_batch_size,
        stats=stats, verbose=verbose,
    )

    stats.duration_s = time.perf_counter() - t0
    return stats


# ── Shared embed + upsert helper ──────────────────────────────────────────────

def _embed_and_upsert(
    supabase: Any,
    chunk_rows: list[dict[str, Any]],
    table: str,
    *,
    api_key: str,
    model: str,
    embed_batch_size: int,
    upsert_batch_size: int,
    stats: ReingestStats,
    verbose: bool,
) -> None:
    """Embed all chunks in batches, then upsert to the target table."""
    import uuid as _uuid  # noqa: PLC0415

    # Generate deterministic UUIDs from the chunk_id string
    for row in chunk_rows:
        chunk_id_str = row.pop("id_str")
        row["id"] = str(_uuid.uuid5(_uuid.NAMESPACE_URL, chunk_id_str))

    # Embed in batches
    texts = [r["content"] for r in chunk_rows]
    embeddings: list[list[float] | None] = [None] * len(texts)

    for batch_start in range(0, len(texts), embed_batch_size):
        batch_texts = texts[batch_start: batch_start + embed_batch_size]
        try:
            batch_embeddings = _embed_batch(batch_texts, api_key=api_key, model=model)
            for i, emb in enumerate(batch_embeddings):
                embeddings[batch_start + i] = emb
            if verbose:
                LOGGER.debug(
                    "[%s] Embedded batch %d-%d",
                    table, batch_start, batch_start + len(batch_texts) - 1,
                )
        except Exception as exc:
            stats.embed_errors += 1
            stats.errors.append(f"Embed batch {batch_start}: {exc}")
            LOGGER.warning("[%s] Embed error batch %d: %s", table, batch_start, exc)
            # Mark embeddings as None for this batch — skip upsert for these
            continue

    # Attach embeddings and filter out rows with None embedding
    rows_to_upsert = []
    for row, emb in zip(chunk_rows, embeddings):
        if emb is None:
            stats.chunks_skipped += 1
            continue
        row["embedding"] = emb
        rows_to_upsert.append(row)

    if not rows_to_upsert:
        LOGGER.warning("[%s] No chunks to upsert after embedding", table)
        return

    # Upsert in batches
    for batch_start in range(0, len(rows_to_upsert), upsert_batch_size):
        batch = rows_to_upsert[batch_start: batch_start + upsert_batch_size]
        try:
            supabase.table(table).upsert(
                batch,
                on_conflict="id",  # deterministic UUIDs — safe idempotent upsert
            ).execute()
            stats.chunks_upserted += len(batch)
            if verbose:
                LOGGER.debug(
                    "[%s] Upserted batch %d-%d (%d rows)",
                    table, batch_start, batch_start + len(batch) - 1, len(batch),
                )
        except Exception as exc:
            stats.upsert_errors += 1
            stats.errors.append(f"Upsert batch {batch_start}: {exc}")
            LOGGER.warning("[%s] Upsert error batch %d: %s", table, batch_start, exc)

    LOGGER.info(
        "[%s] Upserted %d/%d chunks (%d skipped, %d errors)",
        table, stats.chunks_upserted, len(rows_to_upsert),
        stats.chunks_skipped, stats.embed_errors + stats.upsert_errors,
    )


# ── Rollback helper ───────────────────────────────────────────────────────────

def _rollback_v2(supabase: Any, client: str, tables: list[str], dry_run: bool) -> None:
    """Delete index_version=v2 rows for the given client from the specified tables.

    rag_ticket_chunks uses a single-value `client` column — rollback is scoped to
    the specified client only. rag_sop_chunks and rag_knowledge_chunks are globally
    shared (clients[]) — all v2 rows are removed for those tables.
    """
    LOGGER.info("ROLLBACK: Removing v2 rows for client=%s tables=%s", client, tables)
    for table in tables:
        try:
            # Ticket chunks are per-tenant; scope the delete to the given client.
            per_client = table == "rag_ticket_chunks"
            q = supabase.table(table).select("id", count="exact").eq("index_version", "v2")
            if per_client:
                q = q.eq("client", client)

            if dry_run:
                count_resp = q.execute()
                count = getattr(count_resp, "count", "?")
                scope = f"client={client}" if per_client else "all clients"
                LOGGER.info(
                    "[%s] DRY RUN: would delete ~%s v2 rows (%s)", table, count, scope
                )
                continue

            dq = supabase.table(table).delete().eq("index_version", "v2")
            if per_client:
                dq = dq.eq("client", client)
            dq.execute()
            scope = f"client={client}" if per_client else "all clients"
            LOGGER.info("[%s] Deleted v2 rows (%s)", table, scope)
        except Exception as exc:
            LOGGER.error("[%s] Rollback failed: %s", table, exc)


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-ingest B1 RAG tables with token-aware v2 chunking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    client_group = parser.add_mutually_exclusive_group(required=True)
    client_group.add_argument(
        "--client", metavar="SLUG",
        help="Tenant slug to reingest (e.g. unity_bank)",
    )
    client_group.add_argument(
        "--all-clients", action="store_true",
        help="Reingest ALL tenants found in rag_ticket_documents",
    )
    client_group.add_argument(
        "--rollback", action="store_true",
        help="Delete all v2 rows for the specified --client",
    )

    parser.add_argument(
        "--tables",
        nargs="+",
        choices=["ticket", "sop", "knowledge", "all"],
        default=["all"],
        help="Which tables to reingest (default: all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing to the database",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Rows per upsert batch (default: 50)",
    )
    parser.add_argument(
        "--embed-batch-size", type=int, default=64,
        help="Texts per embedding API call (default: 64)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Verbose per-batch logging",
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not supabase_url or not supabase_key:
        LOGGER.error("SUPABASE_URL and SUPABASE_KEY must be set in environment")
        return 1
    if not openai_key:
        LOGGER.error("OPENAI_API_KEY must be set in environment")
        return 1

    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    target_tokens = int(os.getenv("CHUNK_TARGET_TOKENS", "1200"))
    max_input_tokens = int(os.getenv("EMBEDDING_MAX_INPUT_TOKENS", "7000"))
    overlap_tokens = int(os.getenv("CHUNK_OVERLAP_TOKENS", "150"))

    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError:
        LOGGER.error("supabase-py not installed. Run: pip install supabase")
        return 1

    supabase = create_client(supabase_url, supabase_key)

    # Determine which tables to process
    selected = set(args.tables)
    do_ticket = "all" in selected or "ticket" in selected
    do_sop = "all" in selected or "sop" in selected
    do_knowledge = "all" in selected or "knowledge" in selected
    active_tables = (
        (["rag_ticket_chunks"] if do_ticket else []) +
        (["rag_sop_chunks"] if do_sop else []) +
        (["rag_knowledge_chunks"] if do_knowledge else [])
    )

    # Rollback path
    if args.rollback:
        if not args.client:
            LOGGER.error("--rollback requires --client SLUG")
            return 1
        _rollback_v2(supabase, args.client, active_tables, args.dry_run)
        return 0

    # Determine tenants
    if args.all_clients:
        resp = (
            supabase.table("rag_ticket_documents")
            .select("client")
            .execute()
        )
        clients = list({row["client"] for row in (resp.data or []) if row.get("client")})
        if not clients:
            LOGGER.warning("No tenants found in rag_ticket_documents")
            return 0
        LOGGER.info("All-clients mode: found %d tenants: %s", len(clients), clients)
    else:
        clients = [args.client]

    total_start = time.perf_counter()
    all_stats: list[ReingestStats] = []

    common_kwargs = dict(
        target_tokens=target_tokens,
        max_input_tokens=max_input_tokens,
        overlap_tokens=overlap_tokens,
        embed_batch_size=args.embed_batch_size,
        api_key=openai_key,
        model=model,
        upsert_batch_size=args.batch_size,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    for client in clients:
        LOGGER.info("=== Reingesting client=%s ===", client)
        if do_ticket:
            stats = _reingest_ticket_chunks(supabase, client, **common_kwargs)
            all_stats.append(stats)
        if do_sop:
            stats = _reingest_sop_chunks(supabase, client, **common_kwargs)
            all_stats.append(stats)
        if do_knowledge:
            stats = _reingest_knowledge_chunks(supabase, client, **common_kwargs)
            all_stats.append(stats)

    total_s = time.perf_counter() - total_start

    # Summary report
    LOGGER.info("")
    LOGGER.info("=" * 60)
    LOGGER.info("REINGEST SUMMARY%s", " [DRY RUN]" if args.dry_run else "")
    LOGGER.info("=" * 60)
    for s in all_stats:
        LOGGER.info(
            "  %-30s client=%-15s docs=%d chunks_produced=%d upserted=%d skipped=%d "
            "errors=%d duration=%.1fs",
            s.table, s.client, s.source_docs_fetched, s.chunks_produced,
            s.chunks_upserted, s.chunks_skipped,
            s.embed_errors + s.upsert_errors, s.duration_s,
        )
        for err in s.errors[:3]:
            LOGGER.warning("    ERROR: %s", err)
        if len(s.errors) > 3:
            LOGGER.warning("    ... and %d more errors", len(s.errors) - 3)
    LOGGER.info("")
    LOGGER.info("Total duration: %.1fs", total_s)

    if not args.dry_run:
        LOGGER.info("")
        LOGGER.info("Next steps:")
        LOGGER.info("  1. Validate results: query /rag/chat with index_version=v2")
        LOGGER.info("  2. If validated: set ACTIVE_INDEX_VERSION=v2 in .env and restart")
        LOGGER.info("  3. To rollback v2: python scripts/reingest_v2.py --client %s --rollback",
                    clients[0] if len(clients) == 1 else "<slug>")

    return 0


if __name__ == "__main__":
    sys.exit(main())
