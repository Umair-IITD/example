"""
scripts/corpus_fingerprint.py

Sprint 2.37 — Corpus Fingerprint Generator and Verifier

Produces a deterministic SHA-256 fingerprint per article (title + question_body +
answer_body + image_metadata + ordered chunk content) and a global hash of all
per-article hashes.  Fingerprints are stable across re-runs as long as the corpus
is not re-ingested or modified.

Usage:
    python scripts/corpus_fingerprint.py --generate
    python scripts/corpus_fingerprint.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

from dotenv import load_dotenv
load_dotenv(SERVICE_ROOT / ".env")

FREEZE_DIR = SERVICE_ROOT / "data" / "freeze"
FINGERPRINT_PATH = FREEZE_DIR / "knowledge_v2_1_fingerprints.json"


def _article_hash(art: dict, chunks: list[dict]) -> str:
    """Deterministic SHA-256 of article content + ordered chunks."""
    h = hashlib.sha256()
    h.update((art.get("title") or "").encode("utf-8"))
    h.update((art.get("question_body") or "").encode("utf-8"))
    h.update((art.get("answer_body") or "").encode("utf-8"))
    h.update(json.dumps(art.get("image_metadata") or [], sort_keys=True, ensure_ascii=True).encode("utf-8"))
    for c in sorted(chunks, key=lambda x: x.get("chunk_index", 0)):
        h.update((c.get("content") or "").encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _global_hash(article_hashes: dict[str, str]) -> str:
    """SHA-256 of all sorted per-article hashes."""
    h = hashlib.sha256()
    for aid in sorted(article_hashes):
        h.update(article_hashes[aid].encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _load_corpus(sb) -> tuple[dict, dict]:
    arts: dict[str, dict] = {}
    offset = 0
    while True:
        r = sb.table("rag_knowledge_articles").select(
            "article_id,title,question_body,answer_body,image_metadata"
        ).range(offset, offset + 999).execute()
        for a in r.data:
            arts[a["article_id"]] = a
        if len(r.data) < 1000:
            break
        offset += 1000

    chunks_db: dict[str, list] = defaultdict(list)
    offset = 0
    while True:
        r = sb.table("rag_knowledge_chunks").select(
            "article_id,content,chunk_index"
        ).eq("index_version", "v2").range(offset, offset + 999).execute()
        for c in r.data:
            chunks_db[c["article_id"]].append(c)
        if len(r.data) < 1000:
            break
        offset += 1000

    return arts, chunks_db


def cmd_generate() -> None:
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    print("Loading corpus from DB...")
    arts, chunks_db = _load_corpus(sb)
    print(f"  {len(arts)} articles, {sum(len(v) for v in chunks_db.values())} chunks")

    article_hashes: dict[str, str] = {}
    article_meta: dict[str, dict] = {}
    for aid, art in arts.items():
        chunks = chunks_db.get(aid, [])
        ahash = _article_hash(art, chunks)
        article_hashes[aid] = ahash
        article_meta[aid] = {
            "title": (art.get("title") or "")[:80],
            "article_hash": ahash,
            "chunk_count": len(chunks),
        }

    fingerprints = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "2.1",
        "total_articles": len(arts),
        "total_chunks": sum(len(v) for v in chunks_db.values()),
        "global_hash": _global_hash(article_hashes),
        "articles": article_meta,
    }

    FREEZE_DIR.mkdir(parents=True, exist_ok=True)
    FINGERPRINT_PATH.write_text(json.dumps(fingerprints, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Global hash: {fingerprints['global_hash'][:20]}...")
    print(f"Written to: {FINGERPRINT_PATH}")


def cmd_verify() -> None:
    if not FINGERPRINT_PATH.exists():
        print("[FAIL] Fingerprint file not found. Run --generate first.")
        sys.exit(1)

    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    saved = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8"))
    print(f"Verifying against fingerprints generated at {saved['generated_at']}")
    print(f"  Expected: {saved['total_articles']} articles, {saved['total_chunks']} chunks")

    arts, chunks_db = _load_corpus(sb)
    print(f"  Current:  {len(arts)} articles, {sum(len(v) for v in chunks_db.values())} chunks")

    article_hashes: dict[str, str] = {}
    mismatches: list[str] = []
    missing: list[str] = []

    for aid, saved_meta in saved["articles"].items():
        if aid not in arts:
            missing.append(aid)
            continue
        art = arts[aid]
        chunks = chunks_db.get(aid, [])
        current_hash = _article_hash(art, chunks)
        article_hashes[aid] = current_hash
        if current_hash != saved_meta["article_hash"]:
            mismatches.append(f"{aid} (expected {saved_meta['article_hash'][:16]}..., got {current_hash[:16]}...)")

    new_articles = [aid for aid in arts if aid not in saved["articles"]]

    current_global = _global_hash(article_hashes)
    global_match = current_global == saved["global_hash"]

    print(f"\n  Missing articles:   {len(missing)}")
    print(f"  New articles:       {len(new_articles)}")
    print(f"  Hash mismatches:    {len(mismatches)}")
    print(f"  Global hash match:  {'YES' if global_match else 'NO'}")

    if mismatches:
        print("\n  Changed articles:")
        for m in mismatches[:10]:
            print(f"    {m}")

    if missing or new_articles or mismatches or not global_match:
        print("\n[FAIL] Corpus fingerprint mismatch — corpus has changed since freeze.")
        sys.exit(1)
    else:
        print("\n[PASS] Corpus fingerprint verified — corpus unchanged since freeze.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Corpus fingerprint tool")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="Generate fingerprint file")
    group.add_argument("--verify", action="store_true", help="Verify current corpus against saved fingerprints")
    args = parser.parse_args()

    if args.generate:
        cmd_generate()
    elif args.verify:
        cmd_verify()


if __name__ == "__main__":
    main()
