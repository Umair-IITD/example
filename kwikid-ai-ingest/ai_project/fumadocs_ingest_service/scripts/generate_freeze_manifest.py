"""
scripts/generate_freeze_manifest.py

Sprint 2.37 — Knowledge Layer Freeze Manifest Generator

Writes data/freeze/knowledge_v2_1_freeze_manifest.json from live DB counts.
Run once after certification; do NOT re-run after corpus freeze without a new cert.

Usage:
    python scripts/generate_freeze_manifest.py
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv
load_dotenv(SERVICE_ROOT / ".env")

FREEZE_DIR = SERVICE_ROOT / "data" / "freeze"
MANIFEST_PATH = FREEZE_DIR / "knowledge_v2_1_freeze_manifest.json"

# Certification results — from certify_freeze.py run on 2026-07-03
CERT_CHECKS_PASSED = 24
CERT_CHECKS_TOTAL = 24
CERT_BENCHMARK_QUERIES = 30
CERT_BENCHMARK_HITS = 30
CERT_AVG_LATENCY_MS = 439
CERT_P95_LATENCY_MS = 516


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SERVICE_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def generate_manifest() -> dict:
    from supabase import create_client

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # Live corpus counts
    articles = sb.table("rag_knowledge_articles").select(
        "article_id", count="exact"
    ).eq("is_active", True).execute()
    article_count = articles.count or 0

    chunks = sb.table("rag_knowledge_chunks").select(
        "article_id", count="exact"
    ).eq("index_version", "v2").execute()
    chunk_count = chunks.count or 0

    # Image metadata count
    img_rows = sb.table("rag_knowledge_articles").select(
        "image_metadata"
    ).execute()
    total_images = sum(
        len(r.get("image_metadata") or []) for r in img_rows.data
    )
    ocr_images = sum(
        sum(1 for m in (r.get("image_metadata") or []) if m.get("ocr_text"))
        for r in img_rows.data
    )

    # Quality score distribution
    q_rows = sb.table("rag_knowledge_articles").select(
        "quality_score"
    ).execute()
    scores = [r.get("quality_score") or 0.0 for r in q_rows.data if r.get("quality_score")]
    avg_quality = round(sum(scores) / len(scores), 4) if scores else 0.0

    manifest = {
        "version": "2.1",
        "certified_at": "2026-07-03T00:00:00+00:00",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "branch": "major-architecture-change",
        "corpus": {
            "articles": article_count,
            "chunks": chunk_count,
            "total_images": total_images,
            "ocr_images": ocr_images,
            "index_version": "v2",
            "avg_quality_score": avg_quality,
            "source_dir": "stackoverflow",
        },
        "certification": {
            "checks_passed": CERT_CHECKS_PASSED,
            "checks_total": CERT_CHECKS_TOTAL,
            "pass_rate": f"{CERT_CHECKS_PASSED}/{CERT_CHECKS_TOTAL}",
            "benchmark_queries": CERT_BENCHMARK_QUERIES,
            "benchmark_hits": CERT_BENCHMARK_HITS,
            "benchmark_hit_rate_pct": round(CERT_BENCHMARK_HITS / CERT_BENCHMARK_QUERIES * 100, 1),
            "avg_latency_ms": CERT_AVG_LATENCY_MS,
            "p95_latency_ms": CERT_P95_LATENCY_MS,
            "verdict": "PASS",
        },
        "model_versions": {
            "embedding_model": "text-embedding-3-small",
            "embedding_dimensions": 1536,
            "chunker_version": "v2",
            "min_code_chars_threshold": 60,
            "chunk_target_tokens": 1200,
            "chunk_overlap_tokens": 150,
            "index_version": "v2",
        },
        "invariants": {
            "exclude_escalation": True,
            "index_version_required": "v2",
            "upsert_key": ["article_id", "chunk_index", "index_version"],
            "quality_score_floor": 0.55,
            "knowledge_similarity_boost": 0.08,
        },
        "freeze_status": "FROZEN",
        "freeze_note": (
            "Knowledge Layer v2.1 is FROZEN. No ingestion, parser, OCR, "
            "chunking, retrieval, or embedding changes may be made without "
            "running scripts/certify_freeze.py and getting 24/24 checks PASS."
        ),
    }

    FREEZE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    print("Generating Knowledge Layer v2.1 freeze manifest...")
    m = generate_manifest()
    print(f"  Articles:        {m['corpus']['articles']}")
    print(f"  Chunks:          {m['corpus']['chunks']}")
    print(f"  Images:          {m['corpus']['total_images']} ({m['corpus']['ocr_images']} OCR)")
    print(f"  Certification:   {m['certification']['pass_rate']} checks PASS")
    print(f"  Benchmark:       {m['certification']['benchmark_hits']}/{m['certification']['benchmark_queries']} hits")
    print(f"  Git commit:      {m['git_commit'][:12]}")
    print(f"\nWritten to: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
