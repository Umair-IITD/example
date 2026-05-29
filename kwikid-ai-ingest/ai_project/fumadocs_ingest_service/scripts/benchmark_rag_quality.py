#!/usr/bin/env python3
"""
scripts/benchmark_rag_quality.py

Domain-specific retrieval quality benchmark for KwikID RAG.

Runs curated queries against the live retrieval pipeline and computes:
  Recall@K     — fraction of queries that return ≥1 expected chunk type
  SOP hit rate — fraction of SOP-required queries that get a SOP_STEPS chunk
  MRR          — mean reciprocal rank of first relevant result
  Avg top sim  — average top-1 cosine similarity score

This is the QUALITY benchmark (did we get the right stuff?).
Use benchmark_retrieval_pipeline.py for LATENCY benchmarks (how fast?).

Usage:
  # Quality check v2 for unity_bank
  python scripts/benchmark_rag_quality.py --client unity_bank --index-version v2

  # Compare v1 vs v2
  python scripts/benchmark_rag_quality.py --client unity_bank --compare

  # Run a single ad-hoc query and see top-K results
  python scripts/benchmark_rag_quality.py --client unity_bank --query "OTP not received"

  # Save JSON report
  python scripts/benchmark_rag_quality.py --client unity_bank --index-version v2 --output report.json

Environment:
  SUPABASE_URL, SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY), OPENAI_API_KEY
  EMBEDDING_MODEL — default: text-embedding-3-small
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
LOGGER = logging.getLogger("benchmark_rag_quality")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"), override=False)


# ── Benchmark query set ────────────────────────────────────────────────────────
# Domain: KwikID — fintech customer support (OTP, video KYC, account management)
# Each entry describes what the retrieval system MUST return to be considered
# "passing" for that query scenario.

BENCHMARK_QUERIES: list[dict] = [
    # OTP delivery failures ─────────────────────────────────────────────────────
    {
        "query_id":             "otp_001",
        "query":                "customer not receiving OTP on mobile for login",
        "description":          "OTP delivery failure — primary SOP target",
        "expected_chunk_types": ["SOP_STEPS", "RESOLUTION_RCA"],
        "expected_sources":     ["sop"],
        "expect_sop":           True,
    },
    {
        "query_id":             "otp_002",
        "query":                "OTP SMS not delivered after multiple retry attempts",
        "description":          "OTP retry scenario — paraphrase of same SOP",
        "expected_chunk_types": ["SOP_STEPS"],
        "expected_sources":     ["sop"],
        "expect_sop":           True,
    },
    {
        "query_id":             "otp_003",
        "query":                "one-time password expired before user could enter it",
        "description":          "OTP expiry — semantic variant",
        "expected_chunk_types": ["SOP_STEPS", "RESOLUTION_RCA"],
        "expected_sources":     ["sop", "ticket"],
        "expect_sop":           True,
    },
    # Video KYC failures ────────────────────────────────────────────────────────
    {
        "query_id":             "kyc_001",
        "query":                "video KYC session failing or dropping connection",
        "description":          "VKYC session failure — should hit VKYC SOP",
        "expected_chunk_types": ["SOP_STEPS"],
        "expected_sources":     ["sop"],
        "expect_sop":           True,
    },
    {
        "query_id":             "kyc_002",
        "query":                "camera not working during video verification process",
        "description":          "VKYC camera issue — ticket + SOP",
        "expected_chunk_types": ["SOP_STEPS", "RESOLUTION_RCA"],
        "expected_sources":     ["sop", "ticket"],
        "expect_sop":           True,
    },
    {
        "query_id":             "kyc_003",
        "query":                "VKYC agent not available or session timeout",
        "description":          "VKYC agent availability issue",
        "expected_chunk_types": ["SOP_STEPS", "RESOLUTION_RCA"],
        "expected_sources":     ["sop", "ticket"],
        "expect_sop":           True,
    },
    # Account lockout ───────────────────────────────────────────────────────────
    {
        "query_id":             "lock_001",
        "query":                "user account locked and cannot log in",
        "description":          "Account lockout — should hit lockout SOP",
        "expected_chunk_types": ["SOP_STEPS"],
        "expected_sources":     ["sop"],
        "expect_sop":           True,
    },
    {
        "query_id":             "lock_002",
        "query":                "forgot password account blocked after wrong attempts",
        "description":          "Password + lockout combined scenario",
        "expected_chunk_types": ["SOP_STEPS", "RESOLUTION_RCA"],
        "expected_sources":     ["sop", "ticket"],
        "expect_sop":           True,
    },
    # API / integration errors ──────────────────────────────────────────────────
    {
        "query_id":             "api_001",
        "query":                "API returning 401 unauthorized on production environment",
        "description":          "API auth error — ticket/knowledge",
        "expected_chunk_types": ["RESOLUTION_RCA", "HOW_TO", "TROUBLESHOOTING"],
        "expected_sources":     ["ticket", "knowledge"],
        "expect_sop":           False,
    },
    {
        "query_id":             "api_002",
        "query":                "webhook not received by integration endpoint",
        "description":          "Webhook delivery failure",
        "expected_chunk_types": ["RESOLUTION_RCA", "TROUBLESHOOTING"],
        "expected_sources":     ["ticket", "knowledge"],
        "expect_sop":           False,
    },
    # Transaction failures ──────────────────────────────────────────────────────
    {
        "query_id":             "txn_001",
        "query":                "payment failed but amount was debited from customer account",
        "description":          "Debit-without-credit scenario — high priority",
        "expected_chunk_types": ["RESOLUTION_RCA", "QUERY_BODY"],
        "expected_sources":     ["ticket"],
        "expect_sop":           False,
    },
    # Document / onboarding ─────────────────────────────────────────────────────
    {
        "query_id":             "doc_001",
        "query":                "document upload failing during KYC onboarding",
        "description":          "Document upload failure",
        "expected_chunk_types": ["RESOLUTION_RCA", "SOP_STEPS"],
        "expected_sources":     ["ticket", "sop"],
        "expect_sop":           False,
    },
]


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    query_id:          str
    query:             str
    index_version:     str
    returned_count:    int
    sop_count:         int
    chunk_types:       list[str]
    sources:           list[str]
    top_similarity:    float
    recall_hit:        bool
    sop_hit:           bool         # always populated; True/False based on expect_sop
    mrr_rank:          Optional[int]
    latency_ms:        float
    error:             Optional[str] = None


@dataclass
class BenchmarkReport:
    client:             str
    index_version:      str
    top_k:              int
    n_queries:          int
    recall_at_k:        float
    sop_hit_rate:       float
    mrr:                float
    avg_diversity:      float
    avg_top_similarity: float
    avg_latency_ms:     float
    n_errors:           int
    query_results:      list[QueryResult] = field(default_factory=list)
    run_ts:             str = ""

    def print_table(self) -> None:
        w = 64
        print(f"\n{'='*w}")
        print(f"  BENCHMARK: {self.client} | index={self.index_version} | top_k={self.top_k}")
        print(f"{'='*w}")
        print(f"  Recall@{self.top_k:<3}        {self.recall_at_k:>7.1%}")
        print(f"  SOP hit rate        {self.sop_hit_rate:>7.1%}")
        print(f"  MRR                 {self.mrr:>7.4f}")
        print(f"  Avg chunk diversity {self.avg_diversity:>7.2f}")
        print(f"  Avg top similarity  {self.avg_top_similarity:>7.4f}")
        print(f"  Avg latency         {self.avg_latency_ms:>7.0f}ms")
        print(f"  Errors              {self.n_errors:>7}")
        print(f"{'='*w}")

    def print_failures(self) -> None:
        failures = [
            r for r in self.query_results
            if not r.recall_hit or not r.sop_hit
        ]
        if not failures:
            print("\n  All queries passed.\n")
            return
        print(f"\n  FAILING ({len(failures)} queries):")
        for r in failures:
            tags: list[str] = []
            if not r.recall_hit:
                tags.append("NO_RECALL")
            if not r.sop_hit:
                bq = next((q for q in BENCHMARK_QUERIES if q["query_id"] == r.query_id), {})
                if bq.get("expect_sop"):
                    tags.append("SOP_MISS")
            if r.error:
                tags.append(f"ERROR:{r.error[:30]}")
            tag_str = " ".join(f"[{t}]" for t in tags)
            print(f"    {tag_str} [{r.query_id}] {r.query[:65]!r}")
            print(f"      types={r.chunk_types[:5]}  top_sim={r.top_similarity:.3f}  {r.latency_ms:.0f}ms")


# ── Evaluator ─────────────────────────────────────────────────────────────────

class RAGQualityEvaluator:
    """
    Embeds queries and calls Supabase retrieval RPCs directly.
    Requires no running app server.
    """

    def __init__(
        self,
        *,
        supabase_client: Any,
        openai_api_key: str,
        embedding_model: str,
        client: str,
        top_k: int,
        similarity_threshold: float = 0.20,
    ) -> None:
        self._sb        = supabase_client
        self._api_key   = openai_api_key
        self._model     = embedding_model
        self._client    = client
        self._top_k     = top_k
        self._threshold = similarity_threshold

    # ── Public API ─────────────────────────────────────────────────────────────

    def benchmark(
        self,
        queries: list[dict],
        index_version: str,
        *,
        verbose: bool = False,
    ) -> BenchmarkReport:
        from datetime import datetime, timezone
        results: list[QueryResult] = []

        LOGGER.info(
            "Benchmark: %d queries | client=%s | index=%s | top_k=%d",
            len(queries), self._client, index_version, self._top_k,
        )

        for bq in queries:
            r = self._eval_query(bq, index_version=index_version, verbose=verbose)
            results.append(r)
            sop_tag = "[SOP_MISS]" if (bq.get("expect_sop") and not r.sop_hit) else ""
            rc_tag  = "[NO_RECALL]" if not r.recall_hit else "OK"
            LOGGER.info(
                "  %-10s %s %s types=%s sim=%.3f %.0fms",
                bq["query_id"], rc_tag, sop_tag,
                r.chunk_types[:4], r.top_similarity, r.latency_ms,
            )

        ok    = [r for r in results if r.error is None]
        sop_q = [bq for bq in queries if bq.get("expect_sop")]
        sop_h = sum(1 for r in results if r.sop_hit and
                    any(q["query_id"] == r.query_id for q in sop_q))

        mrr_vals = [1.0 / r.mrr_rank for r in ok if r.mrr_rank]
        divs     = [len(set(r.chunk_types)) for r in ok]
        sims     = [r.top_similarity for r in ok if r.top_similarity > 0]
        lats     = [r.latency_ms for r in ok]

        return BenchmarkReport(
            client             = self._client,
            index_version      = index_version,
            top_k              = self._top_k,
            n_queries          = len(queries),
            recall_at_k        = sum(1 for r in results if r.recall_hit) / len(results) if results else 0.0,
            sop_hit_rate       = sop_h / len(sop_q) if sop_q else 1.0,
            mrr                = sum(mrr_vals) / len(mrr_vals) if mrr_vals else 0.0,
            avg_diversity      = sum(divs) / len(divs) if divs else 0.0,
            avg_top_similarity = sum(sims) / len(sims) if sims else 0.0,
            avg_latency_ms     = sum(lats) / len(lats) if lats else 0.0,
            n_errors           = len(results) - len(ok),
            query_results      = results,
            run_ts             = datetime.now(timezone.utc).isoformat(),
        )

    def adhoc(self, query: str, index_version: str, *, verbose: bool = True) -> None:
        bq = {"query_id": "adhoc", "query": query,
              "expected_chunk_types": [], "expected_sources": [],
              "expect_sop": False}
        r = self._eval_query(bq, index_version=index_version, verbose=verbose)
        print(f"\nQuery:        {query!r}")
        print(f"Index:        {index_version}")
        print(f"Retrieved:    {r.returned_count} chunks ({r.sop_count} SOP)")
        print(f"Types:        {r.chunk_types}")
        print(f"Top sim:      {r.top_similarity:.4f}")
        print(f"Latency:      {r.latency_ms:.0f}ms")
        if r.error:
            print(f"Error:        {r.error}")

    # ── Private helpers ────────────────────────────────────────────────────────

    def _eval_query(self, bq: dict, *, index_version: str, verbose: bool) -> QueryResult:
        t0 = time.perf_counter()
        try:
            emb  = self._embed(bq["query"])
            rows = self._retrieve(emb, index_version=index_version)
        except Exception as exc:
            return QueryResult(
                query_id=bq["query_id"], query=bq["query"],
                index_version=index_version, returned_count=0,
                sop_count=0, chunk_types=[], sources=[],
                top_similarity=0.0, recall_hit=False, sop_hit=False,
                mrr_rank=None, latency_ms=(time.perf_counter() - t0) * 1000,
                error=str(exc)[:200],
            )

        cts     = [r.get("chunk_type", "UNKNOWN") for r in rows]
        srcs    = list({r.get("source_type", "unknown") for r in rows})
        sop_ct  = sum(1 for c in cts if c == "SOP_STEPS")
        top_sim = float(rows[0].get("similarity", 0.0)) if rows else 0.0

        exp_cts  = set(bq.get("expected_chunk_types", []))
        exp_srcs = set(bq.get("expected_sources", []))

        recall_hit = bool(
            (exp_cts & set(cts)) or (exp_srcs & set(srcs))
        ) if (exp_cts or exp_srcs) else True

        sop_hit = sop_ct >= 1 if bq.get("expect_sop") else (sop_ct >= 1)

        mrr_rank: Optional[int] = None
        for rank, row in enumerate(rows, 1):
            if row.get("chunk_type") in exp_cts or row.get("source_type") in exp_srcs:
                mrr_rank = rank
                break

        if verbose:
            for i, row in enumerate(rows[:5], 1):
                LOGGER.debug(
                    "    #%d type=%-18s source=%-8s sim=%.4f  %s",
                    i, row.get("chunk_type", "?"), row.get("source_type", "?"),
                    row.get("similarity", 0.0), str(row.get("id", ""))[:8],
                )

        return QueryResult(
            query_id=bq["query_id"], query=bq["query"],
            index_version=index_version, returned_count=len(rows),
            sop_count=sop_ct, chunk_types=cts[:self._top_k],
            sources=srcs, top_similarity=top_sim,
            recall_hit=recall_hit, sop_hit=sop_hit,
            mrr_rank=mrr_rank,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    def _embed(self, text: str) -> list[float]:
        try:
            import openai  # noqa: PLC0415
        except ImportError:
            raise RuntimeError("openai not installed: pip install openai")
        resp = openai.OpenAI(api_key=self._api_key).embeddings.create(
            input=[text], model=self._model
        )
        return resp.data[0].embedding

    def _retrieve(self, embedding: list[float], *, index_version: str) -> list[dict]:
        """Try v2 split-HNSW RPCs, fall back to match_all_b1_sources."""
        try:
            t_rows = self._sb.rpc("match_b1_ticket_chunks_v2", {
                "p_query_embedding": embedding,
                "p_client":          self._client,
                "p_match_count":     self._top_k * 3,
                "p_index_version":   index_version,
            }).execute().data or []
            s_rows = self._sb.rpc("match_b1_sop_chunks_v2", {
                "p_query_embedding": embedding,
                "p_client":          self._client,
                "p_match_count":     self._top_k * 3,
                "p_index_version":   index_version,
            }).execute().data or []
            combined = t_rows + s_rows
            combined.sort(key=lambda r: r.get("boosted_score", 0.0), reverse=True)
            return combined[:self._top_k]
        except Exception:
            pass

        try:
            return self._sb.rpc("match_all_b1_sources", {
                "p_query_embedding": embedding,
                "p_client":          self._client,
                "p_match_count":     self._top_k,
                "p_match_threshold": self._threshold,
                "p_index_version":   index_version,
            }).execute().data or []
        except Exception as exc:
            raise RuntimeError(f"All retrieval RPCs failed: {exc}") from exc


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quality benchmark for KwikID RAG retrieval",
    )
    parser.add_argument("--client",        required=True,  help="Tenant slug (e.g. unity_bank)")
    parser.add_argument("--index-version", default="v2",   help="Index version (default: v2)")
    parser.add_argument("--compare",       action="store_true",
                        help="Run v1 AND v2, print comparison delta")
    parser.add_argument("--query",         help="Single ad-hoc query (skips benchmark)")
    parser.add_argument("--top-k",         type=int, default=10, help="Top-K per query")
    parser.add_argument("--output",        help="Write JSON report to file")
    parser.add_argument("--verbose",       action="store_true")
    args = parser.parse_args()

    url  = os.getenv("SUPABASE_URL", os.getenv("SUPABASE_SERVICE_URL", "")).strip()
    key  = (os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    oai  = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    if not url or not key:
        LOGGER.error("SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY) required")
        return 1
    if not oai:
        LOGGER.error("OPENAI_API_KEY required")
        return 1

    try:
        from supabase import create_client  # noqa: PLC0415
    except ImportError:
        LOGGER.error("supabase-py not installed: pip install supabase")
        return 1

    sb = create_client(url, key)
    evaluator = RAGQualityEvaluator(
        supabase_client=sb, openai_api_key=oai,
        embedding_model=model, client=args.client, top_k=args.top_k,
    )

    # Ad-hoc single query
    if args.query:
        evaluator.adhoc(args.query, args.index_version, verbose=args.verbose)
        return 0

    if args.compare:
        r1 = evaluator.benchmark(BENCHMARK_QUERIES, "v1", verbose=args.verbose)
        r2 = evaluator.benchmark(BENCHMARK_QUERIES, "v2", verbose=args.verbose)
        r1.print_table()
        r2.print_table()
        print(f"\n  DELTA (v1 → v2):")
        print(f"    Recall@{args.top_k}: {r1.recall_at_k:.1%} → {r2.recall_at_k:.1%}  ({r2.recall_at_k - r1.recall_at_k:+.1%})")
        print(f"    SOP rate:   {r1.sop_hit_rate:.1%} → {r2.sop_hit_rate:.1%}  ({r2.sop_hit_rate - r1.sop_hit_rate:+.1%})")
        print(f"    MRR:        {r1.mrr:.4f} → {r2.mrr:.4f}  ({r2.mrr - r1.mrr:+.4f})")
        print(f"    Avg sim:    {r1.avg_top_similarity:.4f} → {r2.avg_top_similarity:.4f}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump({"v1": asdict(r1), "v2": asdict(r2)}, f, indent=2, default=str)
        return 0

    report = evaluator.benchmark(BENCHMARK_QUERIES, args.index_version, verbose=args.verbose)
    report.print_table()
    report.print_failures()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        LOGGER.info("Report written to %s", args.output)

    # CI gate: fail if recall < 60%
    if report.recall_at_k < 0.60:
        LOGGER.error(
            "Recall@%d = %.1f%% — below 60%% gate. v2 NOT ready to promote.",
            args.top_k, report.recall_at_k * 100,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
