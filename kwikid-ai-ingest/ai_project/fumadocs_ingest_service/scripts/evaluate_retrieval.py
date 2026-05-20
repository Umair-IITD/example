#!/usr/bin/env python3
"""
scripts/evaluate_retrieval.py

Offline retrieval evaluation framework for the B1/B3 RAG pipeline.

Compares two retrieval modes head-to-head on a set of test queries:
  - semantic_only: TicketRetriever (pgvector only)
  - hybrid: HybridTicketRetriever (pgvector + FTS + RRF)

Metrics computed per query and aggregated:
  - Latency (ms): semantic, keyword, fusion, total
  - Candidate counts: semantic, keyword, fused, returned
  - Recall proxy: overlap between semantic-only and hybrid results
  - Source distribution: ticket vs SOP vs knowledge chunks
  - Confidence: retrieval_confidence from adaptive RRF
  - Top-1 chunk type and similarity

Test queries are loaded from:
  1. --queries-file JSON (list of {query_text, client, expected_chunk_types?})
  2. Inline defaults (hardcoded sample queries for quick validation)

Usage:
  # Run with defaults (uses sample queries)
  python scripts/evaluate_retrieval.py --client unity_bank

  # Run with custom query file
  python scripts/evaluate_retrieval.py --client unity_bank --queries-file queries.json

  # Compare semantic vs hybrid explicitly
  python scripts/evaluate_retrieval.py --client unity_bank --mode both

  # Write report to file
  python scripts/evaluate_retrieval.py --client unity_bank --output eval_report.json

Environment:
  SUPABASE_URL       — required
  SUPABASE_KEY       — required (service role key)
  OPENAI_API_KEY     — required for embedding
  EMBEDDING_MODEL    — default: text-embedding-3-small
  B1_INDEX_VERSION   — default: v1

Exit codes:
  0 — evaluation completed (reports written)
  1 — evaluation failed (missing env or import error)
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
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("evaluate_retrieval")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()


# ── Default sample queries ────────────────────────────────────────────────────

_DEFAULT_QUERIES: list[dict] = [
    {"query_text": "Customer cannot complete KYC verification process", "expected_types": ["SOP_STEPS", "QUERY_BODY"]},
    {"query_text": "OTP not received for mobile banking login", "expected_types": ["QUERY_BODY", "SOP_STEPS"]},
    {"query_text": "UPI payment failed but amount debited from account", "expected_types": ["QUERY_BODY"]},
    {"query_text": "How to reset internet banking password step by step", "expected_types": ["SOP_STEPS"]},
    {"query_text": "Account blocked due to multiple wrong PIN attempts", "expected_types": ["QUERY_BODY", "SOP_STEPS"]},
    {"query_text": "VKYC video call keeps disconnecting", "expected_types": ["QUERY_BODY", "SOP_STEPS"]},
    {"query_text": "What are the compliance requirements for KYC documents", "expected_types": ["KNOWLEDGE", "SOP_STEPS"]},
    {"query_text": "NEFT transfer showing pending for 24 hours", "expected_types": ["QUERY_BODY"]},
]


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class QueryResult:
    query_text: str
    mode: str
    total_latency_ms: float
    semantic_latency_ms: float
    keyword_latency_ms: float
    fusion_latency_ms: float
    returned_count: int
    semantic_candidates: int
    keyword_candidates: int
    fused_candidates: int
    chunk_types: list[str]
    top1_chunk_type: Optional[str]
    top1_similarity: float
    retrieval_mode: str
    retrieval_confidence: str
    overlap_ratio: float
    selected_rrf_k: int
    error: Optional[str] = None


@dataclass
class ComparisonResult:
    query_text: str
    semantic_result: Optional[QueryResult]
    hybrid_result: Optional[QueryResult]
    recall_overlap: float      # |semantic ∩ hybrid| / |semantic ∪ hybrid|
    hybrid_gain: int           # hybrid_returned - semantic_returned
    latency_delta_ms: float    # hybrid_total - semantic_total


@dataclass
class EvaluationReport:
    client: str
    index_version: str
    evaluated_at: str
    total_queries: int
    mode: str
    query_results: list[QueryResult] = field(default_factory=list)
    comparisons: list[ComparisonResult] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)


# ── Retrieval runner ──────────────────────────────────────────────────────────

def _run_retrieval(
    retriever: Any,
    query_text: str,
    client: str,
    top_k: int,
    similarity_threshold: float,
    index_version: str,
    mode: str,
) -> QueryResult:
    """Run one retrieval call and extract metrics."""
    from rag_engine.retrieval.ticket_retriever import RetrievalRequest  # noqa: PLC0415

    req = RetrievalRequest(
        query_text=query_text,
        client=client,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        index_version=index_version,
    )
    t0 = time.perf_counter()
    try:
        resp = retriever.retrieve(req)
    except Exception as exc:
        return QueryResult(
            query_text=query_text,
            mode=mode,
            total_latency_ms=(time.perf_counter() - t0) * 1000,
            semantic_latency_ms=0.0,
            keyword_latency_ms=0.0,
            fusion_latency_ms=0.0,
            returned_count=0,
            semantic_candidates=0,
            keyword_candidates=0,
            fused_candidates=0,
            chunk_types=[],
            top1_chunk_type=None,
            top1_similarity=0.0,
            retrieval_mode="error",
            retrieval_confidence="low",
            overlap_ratio=0.0,
            selected_rrf_k=60,
            error=str(exc),
        )

    meta = resp.retrieval_metadata
    chunks = resp.chunks
    chunk_types = [c.chunk_type for c in chunks]

    return QueryResult(
        query_text=query_text,
        mode=mode,
        total_latency_ms=round(resp.total_latency_ms, 1),
        semantic_latency_ms=round(resp.semantic_latency_ms, 1),
        keyword_latency_ms=round(meta.get("keyword_latency_ms", 0.0), 1),
        fusion_latency_ms=round(meta.get("fusion_latency_ms", 0.0), 1),
        returned_count=len(chunks),
        semantic_candidates=meta.get("semantic_candidates", 0),
        keyword_candidates=meta.get("keyword_candidates", 0),
        fused_candidates=meta.get("fused_candidates", 0),
        chunk_types=chunk_types,
        top1_chunk_type=chunk_types[0] if chunk_types else None,
        top1_similarity=round(chunks[0].similarity, 4) if chunks else 0.0,
        retrieval_mode=meta.get("retrieval_mode", "unknown"),
        retrieval_confidence=meta.get("retrieval_confidence", "unknown"),
        overlap_ratio=meta.get("overlap_ratio", 0.0),
        selected_rrf_k=meta.get("selected_rrf_k", 60),
    )


def _compare(
    sem: Optional[QueryResult],
    hyb: Optional[QueryResult],
    sem_chunk_ids: set[str],
    hyb_chunk_ids: set[str],
) -> ComparisonResult:
    union = sem_chunk_ids | hyb_chunk_ids
    intersection = sem_chunk_ids & hyb_chunk_ids
    recall_overlap = round(len(intersection) / max(1, len(union)), 4)

    hybrid_returned = hyb.returned_count if hyb else 0
    semantic_returned = sem.returned_count if sem else 0

    hybrid_total_ms = hyb.total_latency_ms if hyb else 0.0
    semantic_total_ms = sem.total_latency_ms if sem else 0.0

    return ComparisonResult(
        query_text=(sem or hyb).query_text if (sem or hyb) else "",
        semantic_result=sem,
        hybrid_result=hyb,
        recall_overlap=recall_overlap,
        hybrid_gain=hybrid_returned - semantic_returned,
        latency_delta_ms=round(hybrid_total_ms - semantic_total_ms, 1),
    )


def _aggregate(results: list[QueryResult]) -> dict:
    if not results:
        return {}
    valid = [r for r in results if not r.error]
    if not valid:
        return {"errors": len(results)}

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {
        "query_count": len(valid),
        "error_count": len(results) - len(valid),
        "avg_total_latency_ms": _avg([r.total_latency_ms for r in valid]),
        "avg_semantic_latency_ms": _avg([r.semantic_latency_ms for r in valid]),
        "avg_keyword_latency_ms": _avg([r.keyword_latency_ms for r in valid]),
        "avg_fusion_latency_ms": _avg([r.fusion_latency_ms for r in valid]),
        "avg_returned_count": _avg([float(r.returned_count) for r in valid]),
        "avg_semantic_candidates": _avg([float(r.semantic_candidates) for r in valid]),
        "avg_keyword_candidates": _avg([float(r.keyword_candidates) for r in valid]),
        "avg_overlap_ratio": _avg([r.overlap_ratio for r in valid]),
        "chunk_type_distribution": {
            ct: sum(r.chunk_types.count(ct) for r in valid)
            for ct in {"QUERY_BODY", "SOP_STEPS", "KNOWLEDGE", "RCA_SUMMARY"}
        },
        "retrieval_confidence_distribution": {
            conf: sum(1 for r in valid if r.retrieval_confidence == conf)
            for conf in {"high", "medium", "low", "unknown"}
        },
    }


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Offline retrieval evaluation — semantic vs hybrid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--client", required=True, metavar="SLUG",
                   help="Tenant slug to evaluate (e.g. unity_bank)")
    p.add_argument("--queries-file", metavar="PATH",
                   help="JSON file with list of {query_text, expected_types?} objects. "
                        "Defaults to built-in sample queries.")
    p.add_argument("--mode", choices=["semantic", "hybrid", "both"], default="both",
                   help="Which retriever(s) to run (default: both)")
    p.add_argument("--top-k", type=int, default=8,
                   help="Top-k chunks to retrieve per query (default: 8)")
    p.add_argument("--similarity-threshold", type=float, default=0.27,
                   help="Similarity threshold (default: 0.27)")
    p.add_argument("--index-version", default="v1",
                   help="index_version to retrieve from (default: v1)")
    p.add_argument("--output", metavar="PATH",
                   help="Write JSON report to this file (also printed to stdout)")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-query details to stderr")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not supabase_url or not supabase_key:
        LOGGER.error("SUPABASE_URL and SUPABASE_KEY must be set")
        return 1
    if not openai_key:
        LOGGER.error("OPENAI_API_KEY must be set")
        return 1

    try:
        from supabase import create_client  # noqa: PLC0415
        from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider  # noqa: PLC0415
        from rag_engine.retrieval.ticket_retriever import TicketRetriever  # noqa: PLC0415
        from rag_engine.config.rag_settings import get_rag_settings  # noqa: PLC0415
    except ImportError as exc:
        LOGGER.error("Import error: %s", exc)
        return 1

    # Load test queries
    if args.queries_file:
        try:
            with open(args.queries_file, encoding="utf-8") as f:
                queries = json.load(f)
            LOGGER.info("Loaded %d queries from %s", len(queries), args.queries_file)
        except Exception as exc:
            LOGGER.error("Failed to load queries file: %s", exc)
            return 1
    else:
        queries = _DEFAULT_QUERIES
        LOGGER.info("Using %d built-in sample queries", len(queries))

    rag_settings = get_rag_settings()
    supabase = create_client(supabase_url, supabase_key)

    embedder = OpenAIEmbeddingProvider(
        api_key=openai_key,
        model=rag_settings.embedding_model,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )

    retrievers: dict[str, Any] = {}

    if args.mode in ("semantic", "both"):
        retrievers["semantic"] = TicketRetriever(
            supabase_client=supabase,
            embedding_provider=embedder,
            ticket_chunks_table=rag_settings.ticket_chunks_table,
            sop_chunks_table=rag_settings.sop_chunks_table,
        )

    if args.mode in ("hybrid", "both"):
        try:
            from rag_engine.retrieval.hybrid_ticket_retriever import HybridTicketRetriever  # noqa: PLC0415
            retrievers["hybrid"] = HybridTicketRetriever(
                supabase_client=supabase,
                embedding_provider=embedder,
                ticket_chunks_table=rag_settings.ticket_chunks_table,
                sop_chunks_table=rag_settings.sop_chunks_table,
            )
        except ImportError as exc:
            LOGGER.warning("HybridTicketRetriever unavailable (%s) — skipping hybrid mode", exc)

    report = EvaluationReport(
        client=args.client,
        index_version=args.index_version,
        evaluated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        total_queries=len(queries),
        mode=args.mode,
    )

    # Per-query results by mode
    results_by_mode: dict[str, list[QueryResult]] = {m: [] for m in retrievers}
    chunk_ids_by_mode: dict[str, list[set[str]]] = {m: [] for m in retrievers}

    for i, q in enumerate(queries, start=1):
        query_text = q.get("query_text", "")
        if not query_text:
            continue

        LOGGER.info("[%d/%d] %s", i, len(queries), query_text[:80])

        for mode_name, retriever in retrievers.items():
            result = _run_retrieval(
                retriever,
                query_text=query_text,
                client=args.client,
                top_k=args.top_k,
                similarity_threshold=args.similarity_threshold,
                index_version=args.index_version,
                mode=mode_name,
            )
            results_by_mode[mode_name].append(result)
            report.query_results.append(result)

            if args.verbose:
                LOGGER.info(
                    "  [%s] returned=%d latency=%.0fms mode=%s confidence=%s",
                    mode_name, result.returned_count, result.total_latency_ms,
                    result.retrieval_mode, result.retrieval_confidence,
                )

    # Build comparisons if both modes were run
    if "semantic" in results_by_mode and "hybrid" in results_by_mode:
        sem_results = results_by_mode["semantic"]
        hyb_results = results_by_mode["hybrid"]

        for sem_r, hyb_r in zip(sem_results, hyb_results):
            # We don't have chunk IDs in QueryResult — use chunk_types as a proxy for overlap
            sem_types = set(sem_r.chunk_types) if sem_r else set()
            hyb_types = set(hyb_r.chunk_types) if hyb_r else set()
            comp = _compare(sem_r, hyb_r, sem_types, hyb_types)
            report.comparisons.append(comp)

    # Aggregate per mode
    for mode_name, results in results_by_mode.items():
        report.aggregate[mode_name] = _aggregate(results)

    # Comparison aggregate
    if report.comparisons:
        comps = report.comparisons
        report.aggregate["comparison"] = {
            "avg_recall_overlap": round(
                sum(c.recall_overlap for c in comps) / len(comps), 4
            ),
            "avg_hybrid_gain": round(
                sum(c.hybrid_gain for c in comps) / len(comps), 2
            ),
            "avg_latency_delta_ms": round(
                sum(c.latency_delta_ms for c in comps) / len(comps), 1
            ),
            "queries_where_hybrid_gained": sum(1 for c in comps if c.hybrid_gain > 0),
            "queries_where_hybrid_slower": sum(1 for c in comps if c.latency_delta_ms > 0),
        }

    # Serialise — convert dataclasses to dicts
    def _to_dict(obj: Any) -> Any:
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _to_dict(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [_to_dict(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _to_dict(v) for k, v in obj.items()}
        return obj

    report_dict = _to_dict(report)
    report_json = json.dumps(report_dict, indent=2)
    print(report_json)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report_json)
            LOGGER.info("Evaluation report written to %s", args.output)
        except Exception as exc:
            LOGGER.warning("Could not write report: %s", exc)

    # Print summary
    LOGGER.info("=" * 60)
    LOGGER.info("EVALUATION SUMMARY  client=%s  queries=%d", args.client, len(queries))
    for mode_name, agg in report.aggregate.items():
        if mode_name == "comparison":
            continue
        LOGGER.info(
            "  [%s] avg_returned=%.1f  avg_total_ms=%.0f  avg_overlap_ratio=%.3f",
            mode_name,
            agg.get("avg_returned_count", 0),
            agg.get("avg_total_latency_ms", 0),
            agg.get("avg_overlap_ratio", 0),
        )
    if "comparison" in report.aggregate:
        comp_agg = report.aggregate["comparison"]
        LOGGER.info(
            "  [comparison] recall_overlap=%.3f  hybrid_gain=%.1f  latency_delta=%.0fms",
            comp_agg.get("avg_recall_overlap", 0),
            comp_agg.get("avg_hybrid_gain", 0),
            comp_agg.get("avg_latency_delta_ms", 0),
        )

    try:
        embedder.close()
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
