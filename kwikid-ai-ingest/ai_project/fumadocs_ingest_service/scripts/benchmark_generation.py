"""
scripts/benchmark_generation.py

Stage 0.5 Phase I — End-to-end RAG generation benchmark.

Hits the live /rag/chat endpoint and records per-query:
  - latency  : wall_ms, total_generation_ms, context_prep_ms, llm_request_ms
  - tokens   : context_tokens, llm_prompt_tokens, llm_completion_tokens, overhead_tokens
  - reliability: llm_schema_retried, llm_schema_validation_failed, llm_schema_failure
  - quality  : workflow_match_type, confidence, has_sop_context, branch_completeness_warnings
  - verbosity: answer word count, citations count

Aggregates: p50/p90/p99 latency, average tokens, schema retry rate — per category and global.

Usage:
    python scripts/benchmark_generation.py \\
        --client unity_bank \\
        --api-key <your-key> \\
        --iterations 3 \\
        --output benchmark_results.json

Environment fallbacks:
    RAG_API_KEY  — X-API-Key header (overridden by --api-key)
    SERVICE_URL  — base URL (default http://localhost:8000, overridden by --url)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=False)
except ImportError:
    pass

try:
    import httpx
except ImportError:
    print("[ERROR] httpx is required: pip install httpx", file=sys.stderr)
    sys.exit(1)


# ── Test query suite ───────────────────────────────────────────────────────────
# Categories match Stage 0.5 Phase I spec:
#   exact_sop / escalation / denial / related_match / weak_retrieval / no_context / long_sop

_QUERY_SUITE: list[dict[str, Any]] = [
    # ── Exact SOP match ───────────────────────────────────────────────────────
    {
        "category": "exact_sop",
        "label": "vcip_session_failure",
        "query": "Customer's VCIP session failed during liveness check. How do I resolve?",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "exact_sop",
        "label": "otp_not_received",
        "query": "Customer is not receiving OTP on their registered mobile number during login.",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "exact_sop",
        "label": "account_unlock",
        "query": "Customer account is locked and they cannot log in. How do I unlock it?",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "exact_sop",
        "label": "kyc_document_rejected",
        "query": "Customer's KYC document was rejected. What steps should the agent follow?",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    # ── Escalation-heavy ──────────────────────────────────────────────────────
    {
        "category": "escalation",
        "label": "foreign_ip_login",
        "query": "Customer login attempt detected from a foreign IP address. What action do I take?",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "escalation",
        "label": "account_takeover_suspected",
        "query": "We suspect account takeover — customer says they did not initiate the login attempts.",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    # ── Denial/restriction-heavy ──────────────────────────────────────────────
    {
        "category": "denial",
        "label": "hard_lock_resolution",
        "query": "Customer account is Hard Locked. Can I unlock it remotely?",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "denial",
        "label": "security_freeze",
        "query": "Account has a Security Freeze. Customer wants it removed immediately.",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "denial",
        "label": "single_factor_only",
        "query": "Agent could only verify one identity factor. Can they still unlock the account?",
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    # ── Related/partial SOP match ─────────────────────────────────────────────
    {
        "category": "related_match",
        "label": "mfa_reenrollment",
        "query": "Customer lost access to their authenticator app and needs MFA re-enrollment.",
        "expect_workflow": "related_match",
        "expect_confidence": "medium",
        "expect_sop": False,
    },
    {
        "category": "related_match",
        "label": "biometric_failure",
        "query": "Customer's face does not match during biometric verification step.",
        "expect_workflow": "related_match",
        "expect_confidence": "medium",
        "expect_sop": False,
    },
    # ── Weak retrieval / ambiguous ────────────────────────────────────────────
    {
        "category": "weak_retrieval",
        "label": "vague_login_issue",
        "query": "Customer is having trouble logging in.",
        "expect_workflow": "weak_match",
        "expect_confidence": "low",
        "expect_sop": False,
    },
    {
        "category": "weak_retrieval",
        "label": "general_account_issue",
        "query": "Something is wrong with the customer's account.",
        "expect_workflow": "weak_match",
        "expect_confidence": "low",
        "expect_sop": False,
    },
    # ── No context / off-domain ───────────────────────────────────────────────
    {
        "category": "no_context",
        "label": "weather_query",
        "query": "What is the weather like in Mumbai today?",
        "expect_workflow": "no_match",
        "expect_confidence": "low",
        "expect_sop": False,
    },
    {
        "category": "no_context",
        "label": "completely_unrelated",
        "query": "How do I bake sourdough bread?",
        "expect_workflow": "no_match",
        "expect_confidence": "low",
        "expect_sop": False,
    },
    # ── Long multi-step SOP workflow ──────────────────────────────────────────
    {
        "category": "long_sop",
        "label": "full_kyc_onboarding",
        "query": (
            "Walk me through the complete KYC onboarding process: "
            "document collection, biometric check, liveness test, and verification steps."
        ),
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
    {
        "category": "long_sop",
        "label": "otp_threshold_escalation",
        "query": (
            "Customer has failed OTP 5 times. What are all the steps: fallback channel, "
            "lockout type determination, and escalation path?"
        ),
        "expect_workflow": "exact_match",
        "expect_confidence": "high",
        "expect_sop": True,
    },
]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _run_query(
    client: httpx.Client,
    url: str,
    api_key: str,
    tenant: str,
    query: str,
    top_k: int = 8,
) -> dict[str, Any]:
    payload = {
        "query_text": query,
        "client": tenant,
        "top_k": top_k,
        "similarity_threshold": 0.27,
        "history_turns": 0,
        "persist_history": False,
    }
    t0 = time.perf_counter()
    resp = client.post(
        f"{url}/rag/chat",
        json=payload,
        headers={"X-API-Key": api_key},
    )
    wall_ms = round((time.perf_counter() - t0) * 1000, 1)
    resp.raise_for_status()
    return {"response": resp.json(), "wall_ms": wall_ms}


def _extract_metrics(raw: dict[str, Any], wall_ms: float) -> dict[str, Any]:
    diag = raw.get("diagnostics") or {}
    tb = diag.get("token_breakdown") or {}
    answer = raw.get("answer") or ""
    answer_words = len(answer.split()) if answer else 0
    return {
        "wall_ms":                     wall_ms,
        "total_generation_ms":         diag.get("total_generation_ms"),
        "context_prep_ms":             diag.get("context_prep_ms"),
        "llm_request_ms":              diag.get("llm_request_ms"),
        "context_tokens":              diag.get("context_tokens") or tb.get("context_tokens"),
        "llm_prompt_tokens":           diag.get("llm_prompt_tokens") or tb.get("llm_prompt_tokens"),
        "llm_completion_tokens":       diag.get("llm_completion_tokens") or tb.get("llm_completion_tokens"),
        "overhead_tokens":             tb.get("overhead_tokens"),
        "total_tokens":                tb.get("total_tokens"),
        "workflow_match_type":         diag.get("workflow_match_type"),
        "confidence":                  raw.get("confidence"),
        "requires_human":              raw.get("requires_human"),
        "has_sop_context":             diag.get("has_sop_context"),
        "chunk_count":                 diag.get("returned_count"),
        "llm_schema_retried":          diag.get("llm_schema_retried", False),
        "llm_schema_validation_failed": diag.get("llm_schema_validation_failed", False),
        "llm_schema_failure":          diag.get("llm_schema_failure", False),
        "branch_completeness_warnings": diag.get("branch_completeness_warnings"),
        "context_suppress_headers":    diag.get("context_suppress_issue_headers"),
        "answer_word_count":           answer_words,
        "citations_count":             len(raw.get("citations") or []),
    }


# ── Aggregation ────────────────────────────────────────────────────────────────

def _pct(vals: list[float], p: int) -> float:
    if not vals:
        return 0.0
    return sorted(vals)[min(int(len(vals) * p / 100), len(vals) - 1)]


def _stats(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "mean": round(statistics.mean(vals), 1),
        "median": round(statistics.median(vals), 1),
        "p90": round(_pct(vals, 90), 1),
        "p99": round(_pct(vals, 99), 1),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
    }


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    schema_retries = sum(1 for r in records if r.get("llm_schema_retried"))
    schema_failures = sum(1 for r in records if r.get("llm_schema_failure"))
    suppress_records = [r for r in records if r.get("context_suppress_headers") is not None]

    def _floats(key: str) -> list[float]:
        return [f for r in records for f in [_safe_float(r.get(key))] if f is not None]

    global_agg: dict[str, Any] = {
        "total_queries": total,
        "schema_retry_rate": round(schema_retries / total, 4) if total else 0,
        "schema_failure_rate": round(schema_failures / total, 4) if total else 0,
        "schema_retries": schema_retries,
        "schema_failures": schema_failures,
        "latency_wall_ms": _stats(_floats("wall_ms")),
        "latency_generation_ms": _stats(_floats("total_generation_ms")),
        "latency_llm_ms": _stats(_floats("llm_request_ms")),
        "context_tokens": _stats(_floats("context_tokens")),
        "prompt_tokens": _stats(_floats("llm_prompt_tokens")),
        "completion_tokens": _stats(_floats("llm_completion_tokens")),
        "overhead_tokens": _stats(_floats("overhead_tokens")),
        "answer_word_count": _stats(_floats("answer_word_count")),
        "workflow_distribution": {
            wf: sum(1 for r in records if r.get("workflow_match_type") == wf)
            for wf in ("exact_match", "related_match", "weak_match", "no_match")
        },
        "confidence_distribution": {
            c: sum(1 for r in records if r.get("confidence") == c)
            for c in ("high", "medium", "low")
        },
        "workflow_accuracy": round(
            sum(
                1 for r in records
                if r.get("workflow_match_type") == r.get("expect_workflow")
            ) / total, 4
        ) if total else 0,
        "headers_suppressed_pct": (
            round(
                sum(1 for r in suppress_records if r.get("context_suppress_headers")) /
                len(suppress_records) * 100, 1
            ) if suppress_records else None
        ),
    }

    # Per-category
    categories: dict[str, list[dict]] = {}
    for r in records:
        categories.setdefault(r.get("category", "unknown"), []).append(r)

    per_category: dict[str, dict] = {}
    for cat, cat_records in sorted(categories.items()):
        n = len(cat_records)
        wf_ok = sum(
            1 for r in cat_records
            if r.get("workflow_match_type") == r.get("expect_workflow")
        )
        cat_retries = sum(1 for r in cat_records if r.get("llm_schema_retried"))

        def _cat_floats(key: str) -> list[float]:
            return [f for r in cat_records for f in [_safe_float(r.get(key))] if f is not None]

        per_category[cat] = {
            "query_count": n,
            "workflow_accuracy": round(wf_ok / n, 3) if n else 0,
            "schema_retry_rate": round(cat_retries / n, 3) if n else 0,
            "latency_wall_ms": _stats(_cat_floats("wall_ms")),
            "latency_llm_ms": _stats(_cat_floats("llm_request_ms")),
            "context_tokens": _stats(_cat_floats("context_tokens")),
            "completion_tokens": _stats(_cat_floats("llm_completion_tokens")),
            "answer_word_count": _stats(_cat_floats("answer_word_count")),
        }

    return {"global": global_agg, "per_category": per_category}


# ── Benchmark runner ──────────────────────────────────────────────────────────

def _run_benchmark(
    *,
    base_url: str,
    api_key: str,
    tenant: str,
    suite: list[dict[str, Any]],
    iterations: int,
    timeout_s: float,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total = len(suite) * iterations

    with httpx.Client(timeout=timeout_s) as client:
        print("\n[warmup] Running warm-up call ...", flush=True)
        try:
            _run_query(client, base_url, api_key, tenant, "test connectivity warmup", top_k=3)
            print("[warmup] done\n", flush=True)
        except Exception as exc:
            print(f"[warmup] FAILED: {exc} — continuing anyway\n", flush=True)

        done = 0
        for iteration in range(1, iterations + 1):
            for entry in suite:
                cat, label, query = entry["category"], entry["label"], entry["query"]
                print(
                    f"  [{done+1:>3}/{total}] iter={iteration} {cat}/{label} ...",
                    end=" ",
                    flush=True,
                )
                try:
                    raw = _run_query(client, base_url, api_key, tenant, query)
                    m = _extract_metrics(raw["response"], raw["wall_ms"])
                    record = {
                        "iteration": iteration,
                        "category": cat,
                        "label": label,
                        "query": query,
                        "expect_workflow": entry["expect_workflow"],
                        "expect_confidence": entry["expect_confidence"],
                        "expect_sop": entry["expect_sop"],
                        **m,
                    }
                    records.append(record)
                    wf = m.get("workflow_match_type", "?")
                    wf_ok = "[OK]" if wf == entry["expect_workflow"] else f"[FAIL:{wf}]"
                    print(
                        f"wall={m['wall_ms']:.0f}ms "
                        f"ctx={m.get('context_tokens') or '?'}tok "
                        f"comp={m.get('llm_completion_tokens') or '?'}tok "
                        f"words={m['answer_word_count']} "
                        f"workflow={wf_ok} "
                        f"retry={'Y' if m['llm_schema_retried'] else 'N'}",
                        flush=True,
                    )
                except Exception as exc:
                    errors.append({
                        "iteration": iteration,
                        "category": cat,
                        "label": label,
                        "error": str(exc)[:200],
                    })
                    print(f"ERROR: {exc}", flush=True)
                done += 1
                if done < total:
                    time.sleep(0.5)

    return {"records": records, "errors": errors}


# ── Output ─────────────────────────────────────────────────────────────────────

def _print_summary(agg: dict[str, Any], errors: list[dict]) -> None:
    g = agg["global"]
    print("\n" + "═" * 72)
    print("  GENERATION BENCHMARK SUMMARY")
    print("═" * 72)
    print(f"  Total queries : {g['total_queries']}  |  Errors: {len(errors)}")
    print(f"  Workflow acc  : {g['workflow_accuracy']*100:.1f}%  (expect vs actual)")
    print(f"  Schema retries: {g['schema_retries']} ({g['schema_retry_rate']*100:.1f}%)")
    print(f"  Schema failures:{g['schema_failures']} ({g['schema_failure_rate']*100:.1f}%)")
    if g.get("headers_suppressed_pct") is not None:
        print(f"  C2 header suppression: {g['headers_suppressed_pct']}% of calls")

    def _row(label: str, s: dict) -> str:
        if not s.get("count"):
            return f"  {label:<28} (no data)"
        return (
            f"  {label:<28} mean={s.get('mean','?'):>6}  "
            f"p50={s.get('median','?'):>6}  "
            f"p90={s.get('p90','?'):>6}  "
            f"max={s.get('max','?'):>6}"
        )

    print("\n  Latency")
    print(_row("wall_ms (end-to-end)", g["latency_wall_ms"]))
    print(_row("generation_ms", g["latency_generation_ms"]))
    print(_row("llm_request_ms", g["latency_llm_ms"]))

    print("\n  Token usage")
    print(_row("context_tokens", g["context_tokens"]))
    print(_row("prompt_tokens (total)", g["prompt_tokens"]))
    print(_row("completion_tokens", g["completion_tokens"]))
    print(_row("overhead_tokens", g["overhead_tokens"]))
    print(_row("answer_word_count", g["answer_word_count"]))

    print("\n  Workflow distribution")
    for wf, cnt in g["workflow_distribution"].items():
        print(f"    {wf:18s}: {cnt}")

    print("\n  Per-category")
    hdr = f"  {'Category':18s} {'N':>3} {'WfAcc%':>7} {'Retry%':>7} {'WallP90':>8} {'LLMp90':>7} {'AvgComp':>8} {'AvgWords':>9}"
    print(hdr)
    print("  " + "-" * 74)
    for cat, cs in agg["per_category"].items():
        lw = cs["latency_wall_ms"]
        ll = cs["latency_llm_ms"]
        ct = cs["completion_tokens"]
        aw = cs["answer_word_count"]
        print(
            f"  {cat:18s} {cs['query_count']:>3} "
            f"{cs['workflow_accuracy']*100:>6.1f}% "
            f"{cs['schema_retry_rate']*100:>6.1f}% "
            f"{lw.get('p90','?'):>8} "
            f"{ll.get('p90','?'):>7} "
            f"{ct.get('mean','?'):>8} "
            f"{aw.get('mean','?'):>9}"
        )

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for e in errors[:10]:
            print(f"    iter={e['iteration']} {e['category']}/{e['label']}: {e['error'][:80]}")
    print("═" * 72)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end RAG generation benchmark")
    parser.add_argument("--client", required=True, help="Tenant slug (e.g. unity_bank)")
    parser.add_argument(
        "--url",
        default=os.getenv("SERVICE_URL", "http://localhost:8000"),
        help="Service base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("RAG_API_KEY", ""),
        help="X-API-Key header value (env: RAG_API_KEY)",
    )
    parser.add_argument("--iterations", type=int, default=3, help="Runs per query (default: 3)")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout (s)")
    parser.add_argument(
        "--output",
        default="benchmark_results.json",
        help="Output JSON file (default: benchmark_results.json)",
    )
    parser.add_argument(
        "--categories",
        nargs="*",
        help="Run only specific categories (e.g. exact_sop escalation)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("[ERROR] --api-key or RAG_API_KEY env var required", file=sys.stderr)
        sys.exit(1)

    suite = _QUERY_SUITE
    if args.categories:
        suite = [q for q in _QUERY_SUITE if q["category"] in args.categories]
        if not suite:
            print(f"[ERROR] No queries match categories: {args.categories}", file=sys.stderr)
            sys.exit(1)

    print(f"[INFO] {len(suite)} queries × {args.iterations} iterations = {len(suite)*args.iterations} total")
    print(f"[INFO] Target: {args.url}  client: {args.client}")

    t_total = time.perf_counter()
    raw = _run_benchmark(
        base_url=args.url.rstrip("/"),
        api_key=args.api_key,
        tenant=args.client,
        suite=suite,
        iterations=args.iterations,
        timeout_s=args.timeout,
    )
    elapsed_s = round(time.perf_counter() - t_total, 1)

    agg = _aggregate(raw["records"])
    _print_summary(agg, raw["errors"])

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "client": args.client,
            "url": args.url,
            "iterations": args.iterations,
            "suite_size": len(suite),
            "total_queries": len(raw["records"]),
            "elapsed_s": elapsed_s,
        },
        "aggregates": agg,
        "errors": raw["errors"],
        "records": raw["records"],
    }

    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"\n[INFO] Results saved → {out_path.resolve()}")
    print(f"[INFO] Total benchmark time: {elapsed_s}s")


if __name__ == "__main__":
    main()
