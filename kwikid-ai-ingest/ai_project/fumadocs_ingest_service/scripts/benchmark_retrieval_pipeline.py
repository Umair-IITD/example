"""
scripts/benchmark_retrieval_pipeline.py

Sprint 0 — Stage-by-stage retrieval latency benchmark.
DIAGNOSTIC ONLY. Does not modify any data.

Usage:
    python scripts/benchmark_retrieval_pipeline.py \
        --client unity_bank \
        --query "OTP not received on my mobile number" \
        --iterations 3

Environment (from .env or shell):
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENAI_API_KEY

Output: per-stage timing table + EXPLAIN ANALYZE on the SQL RPC.
"""
# SPRINT0_DIAG: Remove this script after Sprint 0 diagnosis is complete.
# It exists solely to profile stage-by-stage retrieval latency.
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Allow running from repo root or from scripts/ directory
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=False)
except ImportError:
    pass  # dotenv is optional for this script


def _require_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"[ERROR] Missing required env var: {key}", file=sys.stderr)
        sys.exit(1)
    return val


def _fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.2f}s"
    return f"{ms:.1f}ms"


def _header(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─── Stage 1: OpenAI embedding ────────────────────────────────────────────────

def bench_embedding(query: str, api_key: str, model: str, iterations: int) -> list[float]:
    """Measure time to embed a query via OpenAI API (including HTTP roundtrip)."""
    import httpx  # type: ignore

    url = "https://api.openai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": query}

    latencies: list[float] = []
    print(f"\n[Stage 1] OpenAI embedding — model={model}, iterations={iterations}")

    for i in range(iterations):
        # NEW client per iteration (simulates current per-request behavior)
        t0 = time.perf_counter()
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)
        embedding = resp.json()["data"][0]["embedding"]
        print(f"  iter {i+1}: {_fmt_ms(elapsed)} — dim={len(embedding)}")

    return latencies


def bench_embedding_pooled(query: str, api_key: str, model: str, iterations: int) -> list[float]:
    """Same bench but with a SHARED client (simulates singleton embedder)."""
    import httpx  # type: ignore

    url = "https://api.openai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": query}

    latencies: list[float] = []
    print(f"\n[Stage 1b] OpenAI embedding (shared client) — model={model}, iterations={iterations}")

    with httpx.Client(timeout=30.0) as client:
        for i in range(iterations):
            t0 = time.perf_counter()
            client.post(url, json=payload, headers=headers)
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)
            print(f"  iter {i+1}: {_fmt_ms(elapsed)}")

    return latencies


# ─── Stage 2: Supabase client creation overhead ───────────────────────────────

def bench_client_creation(url: str, key: str, iterations: int) -> list[float]:
    """Measure supabase.create_client() overhead (object creation + pool setup)."""
    from supabase import create_client  # type: ignore

    latencies: list[float] = []
    print(f"\n[Stage 2] create_client() overhead — iterations={iterations}")

    for i in range(iterations):
        t0 = time.perf_counter()
        _ = create_client(url, key)
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)
        print(f"  iter {i+1}: {_fmt_ms(elapsed)}")

    return latencies


# ─── Stage 3: match_all_b1_sources RPC (current unified function) ─────────────

def bench_match_all_b1_sources(
    supabase_url: str,
    supabase_key: str,
    embedding: list[float],
    client_slug: str,
    match_count: int,
    threshold: float,
    index_version: str,
    iterations: int,
) -> list[float]:
    from supabase import create_client  # type: ignore

    latencies: list[float] = []
    print(f"\n[Stage 3] match_all_b1_sources — client={client_slug}, top_k={match_count}, "
          f"threshold={threshold}, index_version={index_version}, iterations={iterations}")

    for i in range(iterations):
        # NEW client per iter (simulates current behavior)
        sb = create_client(supabase_url, supabase_key)
        t0 = time.perf_counter()
        resp = sb.rpc(
            "match_all_b1_sources",
            {
                "p_query_embedding": embedding,
                "p_client": client_slug,
                "p_match_count": match_count,
                "p_match_threshold": threshold,
                "p_index_version": index_version,
            },
        ).execute()
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)
        rows = resp.data or []
        print(f"  iter {i+1}: {_fmt_ms(elapsed)} — rows={len(rows)}")

    return latencies


def bench_match_all_b1_sources_pooled(
    supabase_url: str,
    supabase_key: str,
    embedding: list[float],
    client_slug: str,
    match_count: int,
    threshold: float,
    index_version: str,
    iterations: int,
) -> list[float]:
    """Same bench but with a SHARED Supabase client (simulates module-level singleton)."""
    from supabase import create_client  # type: ignore

    latencies: list[float] = []
    print(f"\n[Stage 3b] match_all_b1_sources (shared client) — iterations={iterations}")

    sb = create_client(supabase_url, supabase_key)
    for i in range(iterations):
        t0 = time.perf_counter()
        resp = sb.rpc(
            "match_all_b1_sources",
            {
                "p_query_embedding": embedding,
                "p_client": client_slug,
                "p_match_count": match_count,
                "p_match_threshold": threshold,
                "p_index_version": index_version,
            },
        ).execute()
        elapsed = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed)
        rows = resp.data or []
        print(f"  iter {i+1}: {_fmt_ms(elapsed)} — rows={len(rows)}")

    return latencies


# ─── Stage 4: Individual legs (ticket-only, SOP-only) ─────────────────────────

def bench_individual_legs(
    supabase_url: str,
    supabase_key: str,
    embedding: list[float],
    client_slug: str,
    match_count: int,
    threshold: float,
    index_version: str,
) -> dict[str, float]:
    """Run match_ticket_chunks and match_sop_chunks separately to compare."""
    from supabase import create_client  # type: ignore

    sb = create_client(supabase_url, supabase_key)
    results: dict[str, float] = {}

    print(f"\n[Stage 4] Individual search legs (shared client)")

    # Ticket chunks only
    t0 = time.perf_counter()
    try:
        resp = sb.rpc(
            "match_ticket_chunks",
            {
                "p_query_embedding": embedding,
                "p_client": client_slug,
                "p_match_count": match_count,
                "p_match_threshold": threshold,
                "p_index_version": index_version,
            },
        ).execute()
        elapsed = (time.perf_counter() - t0) * 1000
        results["match_ticket_chunks"] = elapsed
        print(f"  match_ticket_chunks: {_fmt_ms(elapsed)} — rows={len(resp.data or [])}")
    except Exception as exc:
        print(f"  match_ticket_chunks: FAILED — {exc}")
        results["match_ticket_chunks"] = -1.0

    # SOP chunks only
    t0 = time.perf_counter()
    try:
        resp = sb.rpc(
            "match_sop_chunks",
            {
                "p_query_embedding": embedding,
                "p_client": client_slug,
                "p_match_count": match_count,
                "p_match_threshold": 0.20,  # SOPs use lower threshold
                "p_index_version": index_version,
            },
        ).execute()
        elapsed = (time.perf_counter() - t0) * 1000
        results["match_sop_chunks"] = elapsed
        print(f"  match_sop_chunks:   {_fmt_ms(elapsed)} — rows={len(resp.data or [])}")
    except Exception as exc:
        print(f"  match_sop_chunks: FAILED — {exc}")
        results["match_sop_chunks"] = -1.0

    # FTS search
    t0 = time.perf_counter()
    try:
        resp = sb.rpc(
            "search_b1_sources_fts",
            {
                "p_query_text": "OTP not received",
                "p_client": client_slug,
                "p_match_count": match_count,
                "p_index_version": index_version,
            },
        ).execute()
        elapsed = (time.perf_counter() - t0) * 1000
        results["search_b1_sources_fts"] = elapsed
        print(f"  search_b1_sources_fts: {_fmt_ms(elapsed)} — rows={len(resp.data or [])}")
    except Exception as exc:
        print(f"  search_b1_sources_fts: FAILED ({type(exc).__name__}) — likely B1_008 not applied")
        results["search_b1_sources_fts"] = -1.0

    return results


# ─── Stage 5: Row count check ─────────────────────────────────────────────────

def check_row_counts(supabase_url: str, supabase_key: str, client_slug: str, index_version: str) -> dict:
    """Check row counts in each relevant table for the given client."""
    from supabase import create_client  # type: ignore

    sb = create_client(supabase_url, supabase_key)
    results: dict = {}

    print(f"\n[Stage 5] Row counts (client={client_slug}, index_version={index_version})")

    tables = [
        ("rag_ticket_chunks", "client", client_slug),
        ("rag_sop_chunks", None, None),
    ]

    for table, col, val in tables:
        try:
            q = sb.table(table).select("id", count="exact")
            if col:
                q = q.eq(col, val).eq("index_version", index_version)
            resp = q.execute()
            count = resp.count if hasattr(resp, "count") and resp.count is not None else len(resp.data or [])
            results[table] = count
            print(f"  {table}: {count} rows")
        except Exception as exc:
            print(f"  {table}: FAILED — {exc}")
            results[table] = -1

    return results


# ─── Summary ──────────────────────────────────────────────────────────────────

def print_summary(
    embed_new: list[float],
    embed_pooled: list[float],
    rpc_new: list[float],
    rpc_pooled: list[float],
    row_counts: dict,
) -> None:
    def avg(lst: list[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    _header("LATENCY SUMMARY")
    print(f"\n{'Stage':<45} {'Avg':>10} {'Min':>10} {'Max':>10}")
    print(f"{'─'*45} {'─'*10} {'─'*10} {'─'*10}")

    rows = [
        ("Embedding — new client per call (current)", embed_new),
        ("Embedding — shared client (proposed fix)", embed_pooled),
        ("match_all_b1_sources — new client per call", rpc_new),
        ("match_all_b1_sources — shared client", rpc_pooled),
    ]
    for label, latencies in rows:
        if latencies:
            print(
                f"{label:<45} {_fmt_ms(avg(latencies)):>10} "
                f"{_fmt_ms(min(latencies)):>10} {_fmt_ms(max(latencies)):>10}"
            )

    _header("INTERPRETATION")
    avg_rpc_new = avg(rpc_new) if rpc_new else 0
    avg_rpc_pooled = avg(rpc_pooled) if rpc_pooled else 0
    avg_embed_new = avg(embed_new) if embed_new else 0
    avg_embed_pooled = avg(embed_pooled) if embed_pooled else 0

    if avg_rpc_pooled > 500:
        print(f"""
  ⚠  match_all_b1_sources RPC is SLOW even with a shared client ({_fmt_ms(avg_rpc_pooled)}).
     This confirms the bottleneck is in the SQL function, not client creation overhead.
     Most likely cause: HNSW index is NOT being used (sequential scan due to UNION ALL + ORDER BY).
     Recommended action: Restructure match_all_b1_sources into two LIMIT queries
     (ticket + SOP separately), each using the HNSW index, then merge in Python.
""")
    elif avg_rpc_new > avg_rpc_pooled * 2:
        print(f"""
  ⚠  New-client RPC ({_fmt_ms(avg_rpc_new)}) is significantly slower than shared-client RPC
     ({_fmt_ms(avg_rpc_pooled)}). The bottleneck is TCP/TLS connection overhead, not SQL.
     Fix: Use a module-level Supabase client singleton.
""")
    else:
        print(f"""
  ✓  RPC latency is acceptable ({_fmt_ms(avg_rpc_pooled)} with shared client).
     Focus optimization on other stages (embedding: {_fmt_ms(avg_embed_new)} → {_fmt_ms(avg_embed_pooled)}).
""")

    if row_counts:
        print(f"  Row counts: {row_counts}")
        ticket_rows = row_counts.get("rag_ticket_chunks", 0)
        if ticket_rows > 5000:
            print(f"""
  ⚠  {ticket_rows} rows in rag_ticket_chunks. At this scale, sequential scan
     (if HNSW is not used) would compute distances for all {ticket_rows} rows —
     likely explaining the high latency. Verify HNSW index is active:
       SELECT indexname, indexdef FROM pg_indexes WHERE tablename='rag_ticket_chunks';
       EXPLAIN (ANALYZE, FORMAT TEXT) SELECT ... FROM rag_ticket_chunks ORDER BY embedding <=> '[...]' LIMIT 10;
""")

    _header("RECOMMENDED NEXT STEPS (in priority order)")
    print("""
  1. [CRITICAL] Fix match_all_b1_sources SQL:
     Split into two separate queries (ticket leg, SOP leg), each with
     ORDER BY embedding <=> p_query_embedding LIMIT k
     so the HNSW index is used for each leg. Merge results in the caller.

  2. [HIGH] Verify HNSW indexes exist and are being used:
     Run in Supabase SQL editor:
       SELECT schemaname, tablename, indexname FROM pg_indexes
       WHERE indexname LIKE '%hnsw%';

  3. [HIGH] Create module-level Supabase client singleton:
     Move create_client() out of _build_chat_generator() into module scope.
     Saves TCP+TLS overhead on every request.

  4. [MEDIUM] Cache query embeddings in Redis (TTL=3600s):
     Repeat queries skip the OpenAI API call entirely.

  5. [MEDIUM] Parallelize embedding + FTS (run concurrently with asyncio.gather):
     FTS does not need the query embedding, so it can start before embedding completes.
""")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 0 retrieval latency benchmark")
    parser.add_argument("--client", required=True, help="Tenant slug (e.g. unity_bank)")
    parser.add_argument("--query", default="OTP not received on my mobile number")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--match-count", type=int, default=32)
    parser.add_argument("--threshold", type=float, default=0.27)
    parser.add_argument("--index-version", default="v2")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--skip-embedding", action="store_true",
                        help="Skip embedding call; use a random vector instead")
    args = parser.parse_args()

    supabase_url = _require_env("SUPABASE_URL")
    supabase_key = _require_env("SUPABASE_KEY")
    openai_key = _require_env("OPENAI_API_KEY")

    _header(f"Sprint 0 Retrieval Benchmark — client={args.client}")
    print(f"  Query: {args.query!r}")
    print(f"  Iterations: {args.iterations} | match_count: {args.match_count}")
    print(f"  Threshold: {args.threshold} | Index version: {args.index_version}")

    # ── Stage 1: Embedding ────────────────────────────────────────────────────
    if args.skip_embedding:
        print("\n[Stage 1] Skipping embedding (--skip-embedding). Using random vector.")
        import random
        embedding = [random.gauss(0, 0.1) for _ in range(1536)]
        embed_new = []
        embed_pooled = []
    else:
        embed_new = bench_embedding(args.query, openai_key, args.embedding_model, args.iterations)
        embed_pooled = bench_embedding_pooled(args.query, openai_key, args.embedding_model, args.iterations)

        # Use actual embedding from last iteration
        import httpx
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(
                "https://api.openai.com/v1/embeddings",
                json={"model": args.embedding_model, "input": args.query},
                headers={"Authorization": f"Bearer {openai_key}"},
            )
        embedding = resp.json()["data"][0]["embedding"]

    # ── Stage 2: Client creation overhead ────────────────────────────────────
    bench_client_creation(supabase_url, supabase_key, min(args.iterations, 3))

    # ── Stage 3: match_all_b1_sources (new client per call) ──────────────────
    rpc_new = bench_match_all_b1_sources(
        supabase_url, supabase_key, embedding,
        args.client, args.match_count, args.threshold, args.index_version, args.iterations,
    )

    # ── Stage 3b: match_all_b1_sources (shared client) ───────────────────────
    rpc_pooled = bench_match_all_b1_sources_pooled(
        supabase_url, supabase_key, embedding,
        args.client, args.match_count, args.threshold, args.index_version, args.iterations,
    )

    # ── Stage 4: Individual legs ──────────────────────────────────────────────
    bench_individual_legs(
        supabase_url, supabase_key, embedding,
        args.client, args.match_count, args.threshold, args.index_version,
    )

    # ── Stage 5: Row counts ───────────────────────────────────────────────────
    row_counts = check_row_counts(supabase_url, supabase_key, args.client, args.index_version)

    # ── Summary ───────────────────────────────────────────────────────────────
    print_summary(embed_new, embed_pooled, rpc_new, rpc_pooled, row_counts)


if __name__ == "__main__":
    main()
