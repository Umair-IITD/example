"""
scripts/ingest_sop.py

CLI for ingesting SOP (Standard Operating Procedure) documents into
rag_sop_library and rag_sop_chunks tables.

SOPs are the highest-quality retrieval source in the B1 RAG system.
Ingesting SOPs enables the +0.15 boosted_score in match_all_b1_sources
to surface them above individual ticket anecdotes.

Usage:
    # Ingest all .md files in data/sop/ (default directory)
    python scripts/ingest_sop.py

    # Ingest from a specific directory
    python scripts/ingest_sop.py --sop-dir data/sop/

    # Ingest a single SOP file
    python scripts/ingest_sop.py --sop-file data/sop/otp_delivery_failure_resolution.md

    # Dry run (no DB writes)
    python scripts/ingest_sop.py --dry-run

    # List all ingested SOPs
    python scripts/ingest_sop.py --list

    # Deactivate (soft-delete) a specific SOP by its sop_id
    python scripts/ingest_sop.py --deactivate <sop_id>

Prerequisites:
    - Supabase migrations B1_001 through B1_006 applied
    - .env with SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY
    - SOP files as .md with optional YAML frontmatter

Frontmatter format:
    ---
    title: "OTP Delivery Failure Resolution"
    query_type: "OTP_ISSUE"
    issue_area: "Authentication"
    clients: ""              # empty = global SOP; "unity_bank" = tenant-specific
    ---
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

# Force UTF-8 output on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("ingest_sop")

OK   = "[OK]"
FAIL = "[FAIL]"
INFO = "[INFO]"
WARN = "[WARN]"


def _section(title: str) -> None:
    print(f"\n{'='*66}")
    print(f"  {title}")
    print(f"{'='*66}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest SOP documents into rag_sop_library + rag_sop_chunks"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--sop-dir",
        type=Path,
        default=None,
        help="Directory containing .md SOP files (default: data/sop/)",
    )
    source.add_argument(
        "--sop-file",
        type=Path,
        default=None,
        help="Path to a single .md SOP file to ingest",
    )
    source.add_argument(
        "--list",
        action="store_true",
        help="List all SOPs currently in rag_sop_library and exit",
    )
    source.add_argument(
        "--deactivate",
        type=str,
        metavar="SOP_ID",
        help="Soft-delete a SOP by its sop_id (sets is_active=False)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate SOPs but make no DB writes",
    )
    parser.add_argument(
        "--clients",
        type=str,
        default=None,
        help="Comma-separated default client list when SOP frontmatter omits 'clients'",
    )
    parser.add_argument(
        "--commit-sha",
        type=str,
        default=None,
        help="Optional git commit SHA to attach to SOP source records",
    )

    args = parser.parse_args()

    # ── Load .env ───────────────────────────────────────────────────────────────
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)

    # ── Credentials check ───────────────────────────────────────────────────────
    sb_url  = os.getenv("SUPABASE_URL", "")
    sb_key  = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    emb_key = os.getenv("OPENAI_API_KEY", "") or os.getenv("EMBEDDING_API_KEY", "")

    if not (sb_url and sb_key and emb_key):
        missing = [k for k, v in {
            "SUPABASE_URL":   sb_url,
            "SUPABASE_KEY":   sb_key,
            "OPENAI_API_KEY": emb_key,
        }.items() if not v]
        print(f"\n  {FAIL}  Missing credentials: {', '.join(missing)}")
        print("       Set these in .env before running SOP ingestion.")
        return 1

    # ── Init clients ────────────────────────────────────────────────────────────
    try:
        from supabase import create_client
        from rag_engine.config.rag_settings import get_rag_settings
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        from rag_engine.ingestion.sop_pipeline import SopIngestionPipeline

        settings = get_rag_settings()
        sb       = create_client(sb_url, sb_key)
        embedder = OpenAIEmbeddingProvider(
            api_key=emb_key,
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        )
        pipeline = SopIngestionPipeline(
            settings=settings,
            supabase_client=sb,
            embedding_provider=embedder,
        )

    except ImportError as exc:
        print(f"\n  {FAIL}  Import error: {exc}")
        print("       Install dependencies: pip install supabase openai python-dotenv")
        return 1
    except Exception as exc:
        print(f"\n  {FAIL}  Initialization error: {exc}")
        return 1

    # ── --list ──────────────────────────────────────────────────────────────────
    if args.list:
        _section("Ingested SOPs — rag_sop_library")
        sops = pipeline.list_ingested()
        if not sops:
            print(f"  {WARN}  No SOPs found in rag_sop_library")
            return 0
        print(f"  {'SOP ID':<40}  {'Title':<38}  {'v':<3}  {'Clients':<20}  Active")
        print(f"  {'-'*40}  {'-'*38}  {'-'*3}  {'-'*20}  ------")
        for sop in sops:
            clients_str = ", ".join(sop.get("clients") or []) or "(global)"
            active      = "YES" if sop.get("is_active") else "no"
            print(
                f"  {sop['sop_id'][:38]:<40}  "
                f"{(sop.get('title') or '')[:36]:<38}  "
                f"{sop.get('version', 1):<3}  "
                f"{clients_str[:18]:<20}  {active}"
            )
        print(f"\n  {INFO} Total: {len(sops)} SOPs")
        return 0

    # ── --deactivate ────────────────────────────────────────────────────────────
    if args.deactivate:
        _section(f"Deactivating SOP: {args.deactivate}")
        ok = pipeline.deactivate_sop(args.deactivate)
        if ok:
            print(f"  {OK}  SOP {args.deactivate} deactivated (is_active=False)")
            return 0
        else:
            print(f"  {FAIL}  Failed to deactivate SOP {args.deactivate}")
            return 1

    # ── Ingest ──────────────────────────────────────────────────────────────────
    default_clients: list[str] = []
    if args.clients:
        default_clients = [c.strip() for c in args.clients.split(",") if c.strip()]

    print(f"\n{'='*66}")
    print(f"  SOP Ingestion — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if args.dry_run:
        print("  MODE: DRY RUN — no DB writes will be made")
    print(f"{'='*66}")

    if args.sop_file:
        # Single file
        sop_path = args.sop_file
        if not sop_path.exists():
            print(f"\n  {FAIL}  File not found: {sop_path}")
            return 1
        _section(f"Ingesting: {sop_path.name}")
        result = pipeline.ingest_file(
            sop_path,
            commit_sha=args.commit_sha,
            default_clients=default_clients or None,
            dry_run=args.dry_run,
        )

    else:
        # Directory (default: data/sop/)
        sop_dir = args.sop_dir or (BASE_DIR / "data" / "sop")
        if not sop_dir.exists():
            print(f"\n  {FAIL}  SOP directory not found: {sop_dir}")
            print("       Create it and add .md SOP files:")
            print(f"       mkdir {sop_dir}")
            return 1
        _section(f"Ingesting from directory: {sop_dir}")
        result = pipeline.ingest_directory(
            sop_dir,
            default_clients=default_clients or None,
            dry_run=args.dry_run,
        )

    # ── Summary ─────────────────────────────────────────────────────────────────
    _section("SOP Ingestion Summary")

    total = result.sops_inserted + result.sops_updated + result.sops_skipped + result.sops_failed
    if result.sops_inserted:
        print(f"  {OK}  New SOPs ingested:  {result.sops_inserted}")
    if result.sops_updated:
        print(f"  {OK}  SOPs updated:       {result.sops_updated}")
    if result.sops_skipped:
        print(f"  {INFO} SOPs unchanged:    {result.sops_skipped} (skipped)")
    if result.sops_failed:
        print(f"  {FAIL}  SOPs failed:        {result.sops_failed}")

    print(f"\n  {INFO} Chunks embedded:    {result.embeddings_generated}")
    print(f"  {INFO} Duration:           {result.duration_s:.1f}s")

    if result.sops_failed > 0:
        print(f"\n  {FAIL}  {result.sops_failed} SOP(s) failed — check logs above")
        return 1

    if total == 0:
        print(f"\n  {WARN}  No SOPs processed — check that .md files exist in the SOP directory")
        return 1

    print(f"\n  {OK}  SOP ingestion complete")
    if args.dry_run:
        print(f"       (dry run — no changes were made to the database)")
    else:
        print(f"       SOPs are now live in retrieval via match_all_b1_sources.")
        print(f"       Run: python scripts/validate_b1_retrieval.py --live --client unity_bank")

    return 0


if __name__ == "__main__":
    sys.exit(main())
