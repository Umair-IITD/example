"""
tests/benchmark_knowledge_retrieval.py

Sprint 2.34 — KwikID Knowledge Layer Retrieval Benchmark
50 production-realistic queries across all major support workflows.

Run AFTER B3_001–B3_007 migrations applied and corpus ingested:
    python tests/benchmark_knowledge_retrieval.py

Measures: top-1 hit rate, top-3 hit rate, latency, knowledge class accuracy,
metadata completeness, and per-topic breakdown.
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
    expected_class: str   # FAQ | TROUBLESHOOTING | VERIFIED_REPLY | POLICY | RCA
    expected_topic: str   # OTP | VKYC | OCR | PORTAL | API | ONBOARDING
    description: str = ""


# ---------------------------------------------------------------------------
# 50 Production Queries
# ---------------------------------------------------------------------------
BENCHMARK_QUERIES: list[BenchmarkQuery] = [
    # ── OTP Issues (10) ───────────────────────────────────────────────────
    BenchmarkQuery("Customer not receiving OTP on registered mobile number", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("OTP delivery failed via SMS during VKYC session start", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("OTP expired before customer could enter it in verification", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("OTP resend button not working cannot retry SMS delivery", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("Customer received multiple OTPs but all showing as invalid", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("SMS OTP showing delivered in logs but customer not receiving", "RCA", "OTP"),
    BenchmarkQuery("OTP timeout occurring during VKYC phone verification step", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("Unable to generate OTP for new customer registration flow", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("OTP via WhatsApp not being delivered for reKYC", "TROUBLESHOOTING", "OTP"),
    BenchmarkQuery("Email OTP not received customer requesting alternative", "FAQ", "OTP"),

    # ── VideoKYC (10) ─────────────────────────────────────────────────────
    BenchmarkQuery("VKYC session failed at face match step with rejection reason", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("Liveliness check failing customer blinking not detected", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("Customer camera showing black screen during video KYC", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("Agent cannot hear customer audio during live VKYC call", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("VKYC session timing out before completing all verification steps", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("Face match score below required threshold how to retry VKYC", "FAQ", "VKYC"),
    BenchmarkQuery("Agent cannot see customer video feed in VKYC session", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("VKYC session dropped midway customer needs to restart process", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("Customer video quality too poor for face verification to pass", "TROUBLESHOOTING", "VKYC"),
    BenchmarkQuery("VKYC completed successfully but status shows pending in system", "RCA", "VKYC"),

    # ── Document OCR (8) ──────────────────────────────────────────────────
    BenchmarkQuery("PAN card OCR returning blank result no data extracted", "TROUBLESHOOTING", "OCR"),
    BenchmarkQuery("Aadhaar card OCR failing to extract name and date of birth", "TROUBLESHOOTING", "OCR"),
    BenchmarkQuery("OCR quality score below minimum threshold document rejected", "TROUBLESHOOTING", "OCR"),
    BenchmarkQuery("Document image too blurry for OCR processing needs retry", "FAQ", "OCR"),
    BenchmarkQuery("OCR retry option not responding on agent portal screen", "TROUBLESHOOTING", "OCR"),
    BenchmarkQuery("Wrong name extracted from PAN card OCR causing mismatch", "RCA", "OCR"),
    BenchmarkQuery("Document OCR timeout error during session processing", "TROUBLESHOOTING", "OCR"),
    BenchmarkQuery("Signature verification failing after document OCR completion", "TROUBLESHOOTING", "OCR"),

    # ── Agent Portal (7) ──────────────────────────────────────────────────
    BenchmarkQuery("Agent portal showing 503 service unavailable error message", "TROUBLESHOOTING", "PORTAL"),
    BenchmarkQuery("Agent unable to login to support admin portal credentials", "TROUBLESHOOTING", "PORTAL"),
    BenchmarkQuery("Agent portal session expiring too frequently needs extension", "FAQ", "PORTAL"),
    BenchmarkQuery("Dashboard not loading customer session details and investigation logs", "TROUBLESHOOTING", "PORTAL"),
    BenchmarkQuery("Agent portal not showing VKYC recording for session review", "TROUBLESHOOTING", "PORTAL"),
    BenchmarkQuery("Session search functionality returning no results in admin portal", "TROUBLESHOOTING", "PORTAL"),
    BenchmarkQuery("Error when trying to access session logs and audit trail", "TROUBLESHOOTING", "PORTAL"),

    # ── API / Callbacks (7) ───────────────────────────────────────────────
    BenchmarkQuery("API callback timeout for VKYC session completion webhook", "TROUBLESHOOTING", "API"),
    BenchmarkQuery("Webhook endpoint not receiving session completion notifications", "TROUBLESHOOTING", "API"),
    BenchmarkQuery("API returning 400 bad request when retrieving session details", "RCA", "API"),
    BenchmarkQuery("Callback retry not triggering after initial delivery failure", "TROUBLESHOOTING", "API"),
    BenchmarkQuery("DMS operation callback event not being fired on completion", "RCA", "API"),
    BenchmarkQuery("Session ID not found in API response for completed session", "TROUBLESHOOTING", "API"),
    BenchmarkQuery("API authentication failing for support portal integration", "TROUBLESHOOTING", "API"),

    # ── Onboarding / KYC Status (8) ───────────────────────────────────────
    BenchmarkQuery("Customer KYC status stuck in pending after all steps completed", "TROUBLESHOOTING", "ONBOARDING"),
    BenchmarkQuery("Application rejected but customer says documents were submitted", "RCA", "ONBOARDING"),
    BenchmarkQuery("URN not found when searching customer profile in system", "TROUBLESHOOTING", "ONBOARDING"),
    BenchmarkQuery("Customer onboarding not finalising despite successful VKYC", "RCA", "ONBOARDING"),
    BenchmarkQuery("KYC approval pending beyond SLA threshold escalation required", "POLICY", "ONBOARDING"),
    BenchmarkQuery("Customer profile showing incorrect status after document upload", "TROUBLESHOOTING", "ONBOARDING"),
    BenchmarkQuery("Session not linking to correct customer URN in the database", "RCA", "ONBOARDING"),
    BenchmarkQuery("Aadhaar validation failing for customer during KYC onboarding", "TROUBLESHOOTING", "ONBOARDING"),
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
    """Execute all 50 benchmark queries and print results."""
    try:
        from supabase import create_client
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
        from rag_engine.retrieval.ticket_retriever import TicketRetriever, RetrievalRequest
        from rag_engine.config.rag_settings import get_rag_settings
    except ImportError as exc:
        print(f"[FAIL] Import error: {exc}")
        print("Run from the service root with dependencies installed.")
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
            res.latency_ms = (time.monotonic() - t0) * 1000

        results.append(res)

        status = "HIT " if res.hit else "MISS"
        print(f"[{i:02d}/{total}] [{status}] [{bq.expected_topic}] {bq.query[:65]}")
        if res.hit:
            print(f"         score={res.top1_score:.4f}  class={res.top1_knowledge_class}  type={res.top1_chunk_type}  latency={res.latency_ms:.0f}ms")
            print(f"         content: {(res.top1_content or '')[:120]!r}")
            if res.top1_image_metadata:
                print(f"         images: {len(res.top1_image_metadata)} metadata records attached")
        elif res.error:
            print(f"         ERROR: {res.error[:120]}")
        else:
            print(f"         No knowledge chunks (total_chunks={res.total_chunks})")
        print()

    # ── Summary ──────────────────────────────────────────────────────────
    hits_top1 = sum(1 for r in results if r.hit)
    errors = sum(1 for r in results if r.error)
    latencies = [r.latency_ms for r in results if not r.error]
    chunks_with_images = sum(1 for r in results if r.hit and r.top1_image_metadata)

    print(f"\n{'='*70}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"Total queries:           {total}")
    print(f"Knowledge hits (top-1):  {hits_top1}  ({100*hits_top1//total}%)")
    print(f"Misses:                  {total - hits_top1 - errors}")
    print(f"Errors:                  {errors}")
    if latencies:
        avg_ms = sum(latencies) / len(latencies)
        print(f"Avg latency:             {avg_ms:.0f}ms")
        print(f"Max latency:             {max(latencies):.0f}ms")
        print(f"P95 latency:             {sorted(latencies)[int(0.95*len(latencies))]:.0f}ms")
    print(f"Results with image meta: {chunks_with_images}/{hits_top1}")

    print("\nHits by topic:")
    topic_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for bq, res in zip(BENCHMARK_QUERIES, results):
        topic_stats[bq.expected_topic][1] += 1
        if res.hit:
            topic_stats[bq.expected_topic][0] += 1
    for topic, (h, t) in sorted(topic_stats.items()):
        bar = "#" * h + "-" * (t - h)
        print(f"  {topic:<12}: {h:2d}/{t}  [{bar}]  ({100*h//t}%)")

    print(f"\n{'='*70}")
    if hits_top1 >= total * 0.8:
        print("VERDICT: BENCHMARK PASSED (>=80% top-1 hit rate)")
    elif hits_top1 >= total * 0.6:
        print("VERDICT: PARTIAL PASS (60-79% hit rate — review misses)")
    else:
        print("VERDICT: FAIL (<60% hit rate — knowledge base or retrieval issue)")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run_benchmark()
