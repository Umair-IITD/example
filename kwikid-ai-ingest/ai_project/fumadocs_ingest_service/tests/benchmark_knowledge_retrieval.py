"""
tests/benchmark_knowledge_retrieval.py

Sprint 2.36 — KwikID Knowledge Layer Retrieval Benchmark
30 queries drawn from the actual StackOverflow-for-Teams corpus topics.

The knowledge corpus contains internal developer/support-engineer Q&A:
deployment procedures, per-client operational fixes, AWS/DB operations,
API integration runbooks, and platform administration.

Run AFTER B3 migrations applied and corpus ingested:
    python tests/benchmark_knowledge_retrieval.py

A query is a "hit" if at least one rag_knowledge_chunks result appears in
the top-K results returned by the retriever.
PASS threshold: >= 60% hit rate (knowledge layer adds value on this corpus).
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(SERVICE_ROOT, ".env"))


@dataclass
class BenchmarkQuery:
    query: str
    expected_class: str
    expected_topic: str
    description: str = ""


BENCHMARK_QUERIES: list[BenchmarkQuery] = [
    # Deployment / Release (5)
    BenchmarkQuery("How to release the frontend agent portal to production?", "VERIFIED_REPLY", "DEPLOY"),
    BenchmarkQuery("How to deploy agent portal on Fino on-prem server?", "TROUBLESHOOTING", "DEPLOY"),
    BenchmarkQuery("How to add a new service on the SaaS portal?", "VERIFIED_REPLY", "DEPLOY"),
    BenchmarkQuery("How to install td-agent on RHEL Linux server?", "VERIFIED_REPLY", "DEPLOY"),
    BenchmarkQuery("How to add fluentd login agent for an application?", "VERIFIED_REPLY", "DEPLOY"),

    # Client-specific ops — Unity (4)
    BenchmarkQuery("Unity Production how to repush CBS cases?", "FAQ", "UNITY"),
    BenchmarkQuery("How to add agent created timestamp when key is missing in Unity DB?", "VERIFIED_REPLY", "UNITY"),
    BenchmarkQuery("Unity send OTP and verify OTP API details", "FAQ", "UNITY"),
    BenchmarkQuery("Unity how to hit webhook event for a specific client?", "VERIFIED_REPLY", "UNITY"),

    # Client-specific ops — CBI (4)
    BenchmarkQuery("CBI DKYC retrigger failure doc Aadhaar upload CKYCR upload failure", "TROUBLESHOOTING", "CBI"),
    BenchmarkQuery("CBI video recovery procedure", "FAQ", "CBI"),
    BenchmarkQuery("How to release audit lock for CBI production?", "TROUBLESHOOTING", "CBI"),
    BenchmarkQuery("CBI bulk CBS trigger script how to run?", "FAQ", "CBI"),

    # Client-specific ops — RBL / BOB / BFL (4)
    BenchmarkQuery("How to rotate AWS credentials on RBL UAT server?", "VERIFIED_REPLY", "RBL"),
    BenchmarkQuery("Call connectivity issues in RBL how to debug?", "VERIFIED_REPLY", "RBL"),
    BenchmarkQuery("BOB production agent location missing or half location fix", "VERIFIED_REPLY", "BOB"),
    BenchmarkQuery("How to add missing audit status or feedback in BFL production?", "FAQ", "BFL"),

    # Database / Storage operations (4)
    BenchmarkQuery("How to check Aadhaar UIDAI response in ClickHouse?", "VERIFIED_REPLY", "DB"),
    BenchmarkQuery("How to remove multiple records from message in queue in DynamoDB?", "VERIFIED_REPLY", "DB"),
    BenchmarkQuery("How to check storage on Linux server and delete large files?", "VERIFIED_REPLY", "DB"),
    BenchmarkQuery("BoltDB database empty list of SLOT BOOKING even though data exists", "TROUBLESHOOTING", "DB"),

    # API / Integration (4)
    BenchmarkQuery("How to get access token for refactored APIs authentication?", "VERIFIED_REPLY", "API"),
    BenchmarkQuery("How to check if agent exists in the database?", "VERIFIED_REPLY", "API"),
    BenchmarkQuery("Axios monkey patch for API signature verification", "TROUBLESHOOTING", "API"),
    BenchmarkQuery("API to get total volume of clients from a start date to end date", "VERIFIED_REPLY", "API"),

    # Platform administration (5)
    BenchmarkQuery("How to add a new user to CHAAND identity management?", "FAQ", "ADMIN"),
    BenchmarkQuery("How to generate summary PDF for the KYC session?", "FAQ", "ADMIN"),
    BenchmarkQuery("How to change ownership of user on RHEL s390x server?", "VERIFIED_REPLY", "ADMIN"),
    BenchmarkQuery("How to create an S3 bucket using CLI command?", "VERIFIED_REPLY", "ADMIN"),
    BenchmarkQuery("SUMI standard unified ML interface what is it?", "FAQ", "ADMIN"),
]


@dataclass
class BenchmarkResult:
    query: str
    expected_class: str
    expected_topic: str
    top1_content: Optional[str] = None
    top1_score: float = 0.0
    top1_knowledge_class: Optional[str] = None
    top1_chunk_type: Optional[str] = None
    top1_image_metadata: list = field(default_factory=list)
    latency_ms: float = 0.0
    top3_results: list = field(default_factory=list)
    total_chunks: int = 0
    knowledge_chunks: int = 0
    hit: bool = False
    error: Optional[str] = None


def run_benchmark(top_k: int = 5, client_id: str = "unity_bank") -> None:
    try:
        from supabase import create_client
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        from rag_engine.retrieval.ticket_retriever import TicketRetriever, RetrievalRequest
        from rag_engine.config.rag_settings import get_rag_settings
    except ImportError as exc:
        print(f"[FAIL] Import error: {exc}")
        return

    settings = get_rag_settings()

    try:
        supabase = create_client(
            os.getenv("SUPABASE_URL", ""),
            os.getenv("SUPABASE_KEY", ""),
        )
        embedder = OpenAIEmbeddingProvider(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        retriever = TicketRetriever(supabase, embedder, settings)
    except Exception as exc:
        print(f"[FAIL] Could not initialise retriever: {exc}")
        return

    results: list[BenchmarkResult] = []
    total = len(BENCHMARK_QUERIES)

    print(f"\n{'='*70}")
    print(f"KNOWLEDGE LAYER RETRIEVAL BENCHMARK — {total} queries")
    print(f"Client: {client_id}  |  Top-K: {top_k}")
    print(f"{'='*70}\n")

    for i, bq in enumerate(BENCHMARK_QUERIES, 1):
        t0 = time.monotonic()
        res = BenchmarkResult(query=bq.query, expected_class=bq.expected_class, expected_topic=bq.expected_topic)
        try:
            request = RetrievalRequest(
                query_text=bq.query,
                client=client_id,
                top_k=top_k,
                include_sop=True,
                exclude_escalation=True,
                index_version=settings.index_version,
            )
            response = retriever.retrieve(request)
            res.latency_ms = (time.monotonic() - t0) * 1000
            res.total_chunks = len(response.chunks)

            knowledge_chunks = [
                c for c in response.chunks
                if c.source_table == "rag_knowledge_chunks"
            ]
            res.knowledge_chunks = len(knowledge_chunks)

            if knowledge_chunks:
                top = knowledge_chunks[0]
                res.top1_content = top.content[:250]
                res.top1_score = top.boosted_score
                res.top1_knowledge_class = top.knowledge_class
                res.top1_chunk_type = top.chunk_type
                res.top1_image_metadata = getattr(top, "image_metadata", [])
                res.hit = True
                res.top3_results = [
                    {
                        "preview": c.content[:100],
                        "score": round(c.boosted_score, 4),
                        "class": c.knowledge_class,
                        "chunk_type": c.chunk_type,
                        "has_images": len(getattr(c, "image_metadata", [])) > 0,
                    }
                    for c in knowledge_chunks[:3]
                ]
        except Exception as exc:
            res.error = str(exc)

        results.append(res)

        hit_str = "[HIT] " if res.hit else "[MISS]"
        topic_str = f"[{bq.expected_topic}]"
        print(f"[{i:02d}/{total}] {hit_str} {topic_str} {bq.query[:60]}")
        if res.error:
            print(f"         Error: {res.error[:80]}")
        elif not res.hit:
            print(f"         No knowledge chunks (total_chunks={res.total_chunks})")
        else:
            print(f"         score={res.top1_score:.4f} class={res.top1_knowledge_class} images={len(res.top1_image_metadata)}")
            print(f"         preview: {res.top1_content[:100] if res.top1_content else 'N/A'}")

    _print_summary(results, top_k)


def _print_summary(results: list[BenchmarkResult], top_k: int) -> None:
    hits = [r for r in results if r.hit]
    misses = [r for r in results if not r.hit and not r.error]
    errors = [r for r in results if r.error]
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    chunks_with_images = sum(
        1 for r in results if r.hit and len(r.top1_image_metadata) > 0
    )

    print(f"\n\n{'='*70}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"Total queries:           {len(results)}")
    print(f"Knowledge hits (top-1):  {len(hits)}  ({len(hits)/len(results)*100:.0f}%)")
    print(f"Misses:                  {len(misses)}")
    print(f"Errors:                  {len(errors)}")

    if latencies:
        avg_lat = sum(latencies) / len(latencies)
        max_lat = max(latencies)
        sorted_lat = sorted(latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        print(f"Avg latency:             {avg_lat:.0f}ms")
        print(f"Max latency:             {max_lat:.0f}ms")
        print(f"P95 latency:             {sorted_lat[p95_idx]:.0f}ms")

    image_meta_total = sum(r.hit for r in results)
    print(f"Results with image meta: {chunks_with_images}/{image_meta_total}")

    topic_map: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for r in results:
        topic_map[r.expected_topic].append(r)

    print(f"\nHits by topic:")
    for topic, topic_results in sorted(topic_map.items()):
        topic_hits = sum(1 for r in topic_results if r.hit)
        bar = "#" * topic_hits + "-" * (len(topic_results) - topic_hits)
        print(f"  {topic:<12}:  {topic_hits}/{len(topic_results)}  [{bar}]  ({topic_hits/len(topic_results)*100:.0f}%)")

    hit_rate = len(hits) / len(results)
    print(f"\n{'='*70}")
    if hit_rate >= 0.60:
        print(f"VERDICT: PASS ({hit_rate*100:.0f}% >= 60% hit rate — knowledge layer is operational)")
    elif hit_rate >= 0.40:
        print(f"VERDICT: MARGINAL ({hit_rate*100:.0f}% — knowledge layer partially contributing)")
    else:
        print(f"VERDICT: FAIL (<40% hit rate — knowledge base or retrieval issue)")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run_benchmark()
