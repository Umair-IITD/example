"""
scripts/ingest_knowledge.py

Phase B3: CLI for ingesting Stack Overflow for Teams knowledge into rag_knowledge_chunks.

Usage:
    # Dry-run (no DB writes — shows what WOULD be ingested)
    python scripts/ingest_knowledge.py --dry-run --source ../More_data

    # Live ingestion for all clients
    python scripts/ingest_knowledge.py --source ../More_data

    # Live ingestion for a single client only
    python scripts/ingest_knowledge.py --source ../More_data --client unity_bank

    # Show what tenants would be mapped from the tags
    python scripts/ingest_knowledge.py --source ../More_data --dry-run --verbose

Prerequisites:
    - SQL migrations B3_001 and B3_002 must be applied to Supabase
    - SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY must be set in .env
    - More_data/ directory must contain posts.json, comments.json,
      posts2votes.json, tags.json
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()


def _check_env() -> list[str]:
    """Return list of missing required environment variables."""
    missing = []
    for var in ("SUPABASE_URL", "SUPABASE_KEY", "OPENAI_API_KEY"):
        if not os.getenv(var, "").strip():
            missing.append(var)
    return missing


def _resolve_source_dir(source_arg: str) -> Path:
    """Resolve --source argument to an absolute path."""
    p = Path(source_arg)
    if not p.is_absolute():
        p = Path(__file__).parent.parent / p
    return p.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase B3: Ingest Stack Overflow Teams knowledge into rag_knowledge_chunks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        default=os.getenv("B3_KNOWLEDGE_SOURCE_DIR", "../More_data"),
        help="Path to the More_data/ export directory (default: ../More_data or B3_KNOWLEDGE_SOURCE_DIR env)",
    )
    parser.add_argument(
        "--client",
        default=None,
        help="Only ingest articles for this client slug (plus global articles). Default: all clients.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Parse and classify without writing to DB. Shows summary statistics.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print per-article classification details (useful for dry-run inspection).",
    )
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("Phase B3: Knowledge Base Ingestion")
    print("=" * 60)

    # Check environment
    missing = _check_env()
    if missing and not args.dry_run:
        print(f"[FAIL] Missing required environment variables: {', '.join(missing)}")
        print("       Set them in .env or export them before running.")
        return 1
    if missing and args.dry_run:
        print(f"[WARN] Missing env vars (dry-run — no DB access needed): {', '.join(missing)}")

    # Resolve source directory
    source_dir = _resolve_source_dir(args.source)
    print(f"[INFO] Source directory: {source_dir}")
    if not source_dir.exists():
        print(f"[FAIL] Source directory does not exist: {source_dir}")
        print("       Pass --source <path> or set B3_KNOWLEDGE_SOURCE_DIR in .env")
        return 1

    required_files = ["posts.json", "comments.json", "posts2votes.json", "tags.json"]
    missing_files = [f for f in required_files if not (source_dir / f).exists()]
    if missing_files:
        print(f"[FAIL] Missing required files in {source_dir}: {', '.join(missing_files)}")
        return 1

    print(f"[INFO] Mode: {'DRY RUN' if args.dry_run else 'LIVE INGESTION'}")
    if args.client:
        print(f"[INFO] Client filter: {args.client}")
    else:
        print("[INFO] Client filter: all clients")
    print()

    # Import dependencies (deferred to allow --dry-run even without full env)
    try:
        from rag_engine.config.rag_settings import get_rag_settings
        from rag_engine.ingestion.knowledge_pipeline import KnowledgePipeline
        from rag_engine.ingestion.tenant_mapper import TenantMapper
    except ImportError as exc:
        print(f"[FAIL] Import error — check dependencies: {exc}")
        return 1

    rag_settings = get_rag_settings()
    tenant_mapper = TenantMapper.from_env()

    # Validate --client against known clients
    if args.client and args.client not in tenant_mapper.known_clients:
        print(f"[WARN] Client {args.client!r} not in known client list.")
        print(f"       Known clients: {sorted(tenant_mapper.known_clients)}")
        print("       Proceeding anyway — global articles will still be ingested.")

    # Build pipeline (supabase client not needed for dry-run)
    if not args.dry_run:
        try:
            from supabase import create_client
            from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider

            supabase = create_client(
                os.getenv("SUPABASE_URL", ""),
                os.getenv("SUPABASE_KEY", ""),
            )
            embedder = OpenAIEmbeddingProvider(
                api_key  = os.getenv("OPENAI_API_KEY", ""),
                base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
            pipeline = KnowledgePipeline(
                rag_settings,
                supabase,
                embedder,
                tenant_mapper=tenant_mapper,
            )
        except Exception as exc:
            print(f"[FAIL] Could not initialise pipeline: {exc}")
            return 1
    else:
        # Dry-run: use a mock pipeline that only parses + classifies
        from rag_engine.ingestion.knowledge_pipeline import KnowledgePipeline
        from rag_engine.ingestion.tenant_mapper import TenantMapper

        # We need a stub supabase and embedder for dry-run
        class _NullSupabase:
            def table(self, *a, **kw):
                return self
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a): return self
            def execute(self): return type("R", (), {"data": []})()

        class _NullEmbedder:
            def embed_batch(self, texts):
                return type("R", (), {"embeddings": [[0.0]*1536]*len(texts), "total_tokens": 0})()
            def embed_single(self, text):
                return [0.0] * 1536

        pipeline = KnowledgePipeline(
            rag_settings,
            _NullSupabase(),
            _NullEmbedder(),
            tenant_mapper=tenant_mapper,
        )

    # Run
    print("Starting ingestion run...")
    t_start = time.monotonic()
    try:
        result = pipeline.run(
            source_dir,
            dry_run      = args.dry_run,
            client_filter= args.client,
        )
    except Exception as exc:
        print(f"\n[FAIL] Pipeline run failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    duration = time.monotonic() - t_start

    # Summary
    print()
    print("=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)
    print(f"  Articles processed :  {result.articles_processed}")
    print(f"  Articles inserted  :  {result.articles_inserted}")
    print(f"  Articles updated   :  {result.articles_updated}")
    print(f"  Articles skipped   :  {result.articles_skipped}  (content unchanged)")
    print(f"  Articles rejected  :  {result.articles_rejected}  (classifier filtered)")
    print(f"  Articles failed    :  {result.articles_failed}   (unexpected error)")
    print(f"  Chunks created     :  {result.chunks_created}")
    print(f"  Embeddings generated: {result.embeddings_generated}")
    print(f"  PII redactions     :  {result.pii_redactions}")
    print(f"  Duration           :  {duration:.1f}s")
    if args.dry_run:
        print()
        print("  [DRY RUN] No data was written to the database.")
    print("=" * 60)
    print()

    return 0 if result.articles_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
