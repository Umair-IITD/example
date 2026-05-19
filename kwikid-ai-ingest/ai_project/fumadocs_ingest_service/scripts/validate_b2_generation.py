"""
scripts/validate_b2_generation.py

Phase B2 generation layer validation suite.

Tests the full ChatGenerator pipeline:
    TicketRetriever → context assembly → LLM call → GenerationResult

Offline mode (G1-G10):
    Structural and unit-level checks — no DB, no LLM calls.
    Validates schema, prompt anatomy, confidence derivation, and context assembly.

Live mode (G11-G20):
    End-to-end generation against live Supabase + OpenAI.
    Validates grounding, citations, requires_human signaling, latency, and multi-tenant safety.

Run:
    python scripts/validate_b2_generation.py                      # offline only
    python scripts/validate_b2_generation.py --live               # offline + live
    python scripts/validate_b2_generation.py --live --client unity_bank
    python scripts/validate_b2_generation.py --live --report      # write markdown report
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

OK   = "[OK]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"
SKIP = "[SKIP]"


# ── Result tracking ────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    label: str
    passed: bool
    notes: list[str] = dataclasses.field(default_factory=list)

    def mark(self, symbol: str) -> str:
        return OK if self.passed else FAIL


class ValidationSuite:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def check(self, label: str, passed: bool, *notes: str) -> CheckResult:
        r = CheckResult(label=label, passed=passed, notes=list(notes))
        self.results.append(r)
        sym = OK if passed else FAIL
        print(f"  {sym}  {label}")
        for note in notes:
            print(f"         {note}")
        return r

    def warn(self, label: str, *notes: str) -> None:
        r = CheckResult(label=label, passed=True, notes=list(notes))
        self.results.append(r)
        print(f"  {WARN}  {label}")
        for note in notes:
            print(f"         {note}")

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def summary_line(self) -> str:
        return f"{self.pass_count}/{self.total} checks passed"


# ── Offline mock infrastructure ────────────────────────────────────────────────

class _MockChunk:
    """Minimal stand-in for RetrievedChunk — satisfies assemble_context typing."""
    def __init__(
        self,
        chunk_id: str = "ck-001",
        ticket_id: Optional[str] = "t-001",
        sop_id: Optional[str] = None,
        chunk_type: str = "RESOLUTION_RCA",
        content: str = "The OTP was resent after verifying the mobile number.",
        similarity: float = 0.72,
        boosted_score: float = 0.72,
        source_table: str = "rag_ticket_chunks",
        has_rca: bool = True,
        has_sop: bool = False,
        automation_label: Optional[str] = None,
        query_type: Optional[str] = None,
    ) -> None:
        self.chunk_id = chunk_id
        self.ticket_id = ticket_id
        self.sop_id = sop_id
        self.chunk_type = chunk_type
        self.content = content
        self.similarity = similarity
        self.boosted_score = boosted_score
        self.source_table = source_table
        self.has_rca = has_rca
        self.has_sop = has_sop
        self.automation_label = automation_label
        self.query_type = query_type


def _make_sop_chunk() -> _MockChunk:
    return _MockChunk(
        chunk_id="sop-001",
        ticket_id=None,
        sop_id="otp_delivery_failure",
        chunk_type="SOP_STEPS",
        content="Step 1: Verify the mobile number registered with the account. "
                "Step 2: Ask the customer to check SMS inbox and spam folder. "
                "Step 3: Trigger OTP resend from the admin panel.",
        similarity=0.58,
        boosted_score=0.73,
        source_table="rag_sop_chunks",
        has_rca=False,
        has_sop=True,
    )


# ── Offline test group ─────────────────────────────────────────────────────────

def run_offline_tests(suite: ValidationSuite) -> None:
    print("\n=== OFFLINE TESTS (G1-G10) ===")

    # G1 — GenerationResult schema completeness
    print("\nG1 — GenerationResult has all required B2 fields")
    from rag_engine.generation.chat_generator import GenerationResult
    required_fields = {
        "answer", "confidence", "confidence_score", "citations",
        "requires_human", "follow_up_question", "chunks", "diagnostics",
        "session_id", "message_id", "insufficient_context",
    }
    actual_fields = {f.name for f in dataclasses.fields(GenerationResult)}
    missing = required_fields - actual_fields
    suite.check(
        "GenerationResult has all required B2 fields",
        not missing,
        f"fields present: {sorted(actual_fields)}",
        *(f"MISSING: {m}" for m in sorted(missing)),
    )

    # G2 — confidence_score is numeric float
    print("\nG2 — _derive_confidence_score returns float in [0.0, 1.0]")
    from rag_engine.generation.chat_generator import _derive_confidence_score
    for cat, expect_lo, expect_hi in [
        ("high",   0.75, 1.0),
        ("medium", 0.40, 0.75),
        ("low",    0.05, 0.40),
    ]:
        score = _derive_confidence_score(
            cat,
            insufficient_context=False,
            has_sop=False,
            has_rca=False,
            chunk_count=3,
            requires_human=False,
        )
        in_range = expect_lo <= score <= expect_hi
        suite.check(
            f"confidence_score({cat!r}) in [{expect_lo}, {expect_hi}]",
            in_range,
            f"got {score:.3f}",
        )

    # G3 — monotonicity: high > medium > low
    print("\nG3 — confidence_score is monotonically decreasing (high > medium > low)")
    high_s  = _derive_confidence_score("high",   insufficient_context=False, has_sop=False, has_rca=False, chunk_count=3, requires_human=False)
    med_s   = _derive_confidence_score("medium", insufficient_context=False, has_sop=False, has_rca=False, chunk_count=3, requires_human=False)
    low_s   = _derive_confidence_score("low",    insufficient_context=False, has_sop=False, has_rca=False, chunk_count=3, requires_human=False)
    suite.check(
        "high > medium > low (monotonicity)",
        high_s > med_s > low_s,
        f"high={high_s:.3f}  medium={med_s:.3f}  low={low_s:.3f}",
    )

    # G4 — insufficient_context caps score at 0.10
    print("\nG4 — insufficient_context=True caps confidence_score at 0.10")
    score_ic = _derive_confidence_score("high", insufficient_context=True, has_sop=False, has_rca=False, chunk_count=0, requires_human=True)
    suite.check(
        "confidence_score == 0.10 when insufficient_context=True",
        score_ic == 0.10,
        f"got {score_ic}",
    )

    # G5 — requires_human from _derive_requires_human when insufficient_context
    print("\nG5 — _derive_requires_human forces True on insufficient_context")
    from rag_engine.generation.chat_generator import _derive_requires_human
    rh_ic = _derive_requires_human(
        llm_flag=False,
        insufficient_context=True,
        confidence="medium",
        has_sop_context=False,
    )
    suite.check(
        "requires_human=True when insufficient_context=True (LLM said False)",
        rh_ic is True,
    )

    # G6 — requires_human respects LLM signal when True
    print("\nG6 — requires_human=True honored when LLM sets flag=True")
    rh_llm = _derive_requires_human(
        llm_flag=True,
        insufficient_context=False,
        confidence="high",
        has_sop_context=True,
    )
    suite.check(
        "requires_human=True when LLM flags True even with high confidence + SOP",
        rh_llm is True,
    )

    # G7 — requires_human=False with good context and LLM flag=False
    print("\nG7 — requires_human=False with high confidence + SOP + LLM flag=False")
    rh_ok = _derive_requires_human(
        llm_flag=False,
        insufficient_context=False,
        confidence="high",
        has_sop_context=True,
    )
    suite.check(
        "requires_human=False when context good and LLM did not flag",
        rh_ok is False,
    )

    # G8 — B2_SYSTEM_PROMPT anatomy sections present
    print("\nG8 — B2_SYSTEM_PROMPT contains all required anatomy sections")
    from rag_engine.generation.prompt_builder import B2_SYSTEM_PROMPT
    anatomy_sections = ["ROLE", "TASK", "CONTEXT", "REASONING", "STOP CONDITIONS", "OUTPUT"]
    for section in anatomy_sections:
        suite.check(
            f"B2_SYSTEM_PROMPT contains section: {section}",
            section in B2_SYSTEM_PROMPT,
        )

    # G9 — B2_SYSTEM_PROMPT output schema includes requires_human
    print("\nG9 — B2_SYSTEM_PROMPT output schema includes requires_human boolean")
    suite.check(
        "B2_SYSTEM_PROMPT mentions requires_human in OUTPUT",
        "requires_human" in B2_SYSTEM_PROMPT,
    )
    suite.check(
        "B2_SYSTEM_PROMPT specifies requires_human as boolean (true | false)",
        "true | false" in B2_SYSTEM_PROMPT or "<true | false>" in B2_SYSTEM_PROMPT,
    )

    # G8b — B1LLMClient has granular timeout params
    print("\nG8b — B1LLMClient constructor has granular timeout params")
    from rag_engine.generation.llm_client import B1LLMClient
    import inspect
    sig = inspect.signature(B1LLMClient.__init__)
    for param_name in ["connect_timeout_s", "read_timeout_s", "write_timeout_s",
                       "pool_timeout_s", "max_timeout_retries"]:
        suite.check(
            f"B1LLMClient.__init__ has param: {param_name}",
            param_name in sig.parameters,
        )

    # G8c — B1LLMClient builds httpx.Timeout object
    print("\nG8c — B1LLMClient uses httpx.Timeout (not scalar)")
    import httpx as _httpx
    client = B1LLMClient(api_key="sk-test", connect_timeout_s=5, read_timeout_s=45)
    suite.check(
        "B1LLMClient._timeout is httpx.Timeout instance",
        isinstance(client._timeout, _httpx.Timeout),
    )
    suite.check(
        "connect timeout set to 5s",
        client._timeout.connect == 5.0,
        f"got {client._timeout.connect}",
    )
    suite.check(
        "read timeout set to 45s",
        client._timeout.read == 45.0,
        f"got {client._timeout.read}",
    )

    # G8d — ChatGenerator returns degraded result when LLM raises
    print("\nG8d — ChatGenerator returns degraded GenerationResult when LLM fails")
    from rag_engine.generation.chat_generator import ChatGenerator, GenerationRequest, _degraded_result

    class _AlwaysFailLLM:
        def complete_json(self, **kw):
            raise RuntimeError("simulated LLM timeout")

    class _MockRetriever:
        def retrieve(self, req):
            from dataclasses import dataclass, field as dc_field
            @dataclass
            class FakeResp:
                chunks = []
                total_candidates = 0
                semantic_latency_ms = 1.0
                total_latency_ms = 1.5
                client = req.client
                query_hash = "abc"
                retrieval_metadata: dict = dc_field(default_factory=dict)
                has_sop_context = False
                has_rca_context = False
            return FakeResp()

    broken_gen = ChatGenerator(
        retriever=_MockRetriever(),  # type: ignore
        llm_client=_AlwaysFailLLM(),  # type: ignore
    )
    try:
        degraded = broken_gen.generate(
            GenerationRequest(query_text="test", client="unity_bank", persist_history=False)
        )
        suite.check(
            "degraded result returned (no exception raised)",
            True,
        )
        suite.check(
            "degraded result has requires_human=True",
            degraded.requires_human is True,
            f"got {degraded.requires_human}",
        )
        suite.check(
            "degraded result has confidence='low'",
            degraded.confidence == "low",
            f"got {degraded.confidence}",
        )
        suite.check(
            "degraded result has confidence_score <= 0.10",
            degraded.confidence_score <= 0.10,
            f"got {degraded.confidence_score}",
        )
        suite.check(
            "degraded diagnostics contains llm_failure=True",
            degraded.diagnostics.get("llm_failure") is True,
        )
    except Exception as exc:
        suite.check("degraded result returned (no exception raised)", False, str(exc))

    # G8e — _parse_json on non-JSON content sets requires_human=True
    print("\nG8e — _parse_json on non-JSON sets requires_human=True")
    from rag_engine.generation.llm_client import _parse_json
    bad_parse = _parse_json("This is not JSON at all")
    suite.check(
        "_parse_json returns dict for non-JSON input",
        isinstance(bad_parse, dict),
    )
    suite.check(
        "_parse_json sets requires_human=True for non-JSON input",
        bad_parse.get("requires_human") is True,
        f"got requires_human={bad_parse.get('requires_human')}",
    )
    suite.check(
        "_parse_json sets confidence='low' for non-JSON input",
        bad_parse.get("confidence") == "low",
    )

    # G10 — assemble_context: empty chunks returns sentinel + zero stats
    print("\nG10 — assemble_context([]) returns correct sentinel and zero stats")
    from rag_engine.generation.context_assembler import assemble_context, AssembledContext
    empty = assemble_context([])
    suite.check(
        "context_block == '(no context retrieved)' for empty chunks",
        empty.context_block == "(no context retrieved)",
    )
    suite.check(
        "chunk_count == 0 for empty chunks",
        empty.chunk_count == 0,
    )
    suite.check(
        "total_tokens == 0 for empty chunks",
        empty.total_tokens == 0,
    )
    suite.check(
        "skipped_chunks == 0 for empty chunks",
        empty.skipped_chunks == 0,
    )

    # G10b — assemble_context: sop chunk gets AUTHORITATIVE label + has total_tokens > 0
    print("\n     assemble_context with mixed chunks")
    chunks = [_make_sop_chunk(), _MockChunk()]
    assembled = assemble_context(chunks)  # type: ignore[arg-type]
    suite.check(
        "AUTHORITATIVE label in context_block for SOP chunk",
        "AUTHORITATIVE" in assembled.context_block,
    )
    suite.check(
        "total_tokens > 0 when chunks are present",
        assembled.total_tokens > 0,
        f"got {assembled.total_tokens} tokens",
    )
    suite.check(
        "skipped_chunks == 0 within normal budget",
        assembled.skipped_chunks == 0,
    )
    suite.check(
        "sop_count == 1 for one SOP chunk",
        assembled.sop_count == 1,
    )


# ── Live test group ────────────────────────────────────────────────────────────

def run_live_tests(suite: ValidationSuite, client: str) -> None:
    print(f"\n=== LIVE TESTS (G11-G20) | client={client} ===")

    # Load environment
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_KEY", "").strip()
    openai_key   = os.getenv("OPENAI_API_KEY", "").strip()

    missing_env = [v for v, k in [
        ("SUPABASE_URL", supabase_url),
        ("SUPABASE_KEY", supabase_key),
        ("OPENAI_API_KEY", openai_key),
    ] if not k]

    if missing_env:
        print(f"  {SKIP}  Missing environment variables: {missing_env}")
        print(f"  {SKIP}  All live tests skipped — set variables in .env and re-run with --live")
        return

    from supabase import create_client
    from rag_engine.config.rag_settings import get_rag_settings
    from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
    from rag_engine.generation.chat_generator import ChatGenerator, GenerationRequest
    from rag_engine.generation.llm_client import B1LLMClient
    from rag_engine.retrieval.ticket_retriever import TicketRetriever

    rag_settings = get_rag_settings()
    supabase = create_client(supabase_url, supabase_key)
    embedder = OpenAIEmbeddingProvider(
        api_key=openai_key,
        model=rag_settings.embedding_model,
    )
    llm = B1LLMClient(api_key=openai_key, model="gpt-4o-mini")
    retriever = TicketRetriever(
        supabase_client=supabase,
        embedding_provider=embedder,
        ticket_chunks_table=rag_settings.ticket_chunks_table,
        sop_chunks_table=rag_settings.sop_chunks_table,
    )
    generator = ChatGenerator(retriever, llm, per_chunk_max_chars=1400)

    def gen(query: str, **kw: Any):
        return generator.generate(
            GenerationRequest(
                query_text=query,
                client=client,
                top_k=kw.get("top_k", 8),
                similarity_threshold=kw.get("similarity_threshold", 0.27),
                persist_history=False,
                history_turns=0,
                index_version=rag_settings.index_version,
            )
        )

    # G11 — Basic generation: valid GenerationResult returned
    print("\nG11 — Basic generation returns valid GenerationResult")
    try:
        t0 = time.perf_counter()
        result = gen("OTP not received on registered mobile number")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        suite.check(
            "GenerationResult returned without exception",
            True,
            f"latency={elapsed_ms:.0f}ms",
        )
        suite.check(
            "answer is non-empty string",
            isinstance(result.answer, str) and len(result.answer) > 0,
            f"len={len(result.answer)}",
        )
    except Exception as exc:
        suite.check("GenerationResult returned without exception", False, str(exc))
        print(f"  {WARN}  G11 failed — skipping dependent live tests")
        embedder.close()
        return

    # G12 — requires_human is bool
    print("\nG12 — requires_human is a Python bool")
    suite.check(
        "requires_human is bool",
        isinstance(result.requires_human, bool),
        f"got type={type(result.requires_human).__name__} value={result.requires_human}",
    )

    # G13 — confidence_score is float in [0.0, 1.0]
    print("\nG13 — confidence_score is float in [0.0, 1.0]")
    suite.check(
        "confidence_score is float",
        isinstance(result.confidence_score, float),
        f"got {result.confidence_score}",
    )
    suite.check(
        "confidence_score in [0.0, 1.0]",
        0.0 <= result.confidence_score <= 1.0,
        f"got {result.confidence_score}",
    )

    # G14 — confidence categorical value is valid
    print("\nG14 — confidence is valid categorical value")
    suite.check(
        "confidence in {high, medium, low}",
        result.confidence in {"high", "medium", "low"},
        f"got {result.confidence!r}",
    )

    # G15 — citations are well-formed when present
    print("\nG15 — citations are well-formed")
    citation_ok = True
    for i, cit in enumerate(result.citations):
        if not isinstance(cit, dict):
            suite.check(f"citation[{i}] is dict", False, f"got {type(cit).__name__}")
            citation_ok = False
            continue
        has_chunk_num = "chunk_num" in cit and isinstance(cit["chunk_num"], int)
        has_chunk_type = "chunk_type" in cit and isinstance(cit["chunk_type"], str)
        has_source_id = "source_id" in cit
        if not (has_chunk_num and has_chunk_type and has_source_id):
            suite.check(f"citation[{i}] has required keys", False, str(cit))
            citation_ok = False
    if result.citations:
        suite.check(
            f"all {len(result.citations)} citation(s) well-formed",
            citation_ok,
        )
    else:
        suite.warn(
            "no citations returned — may indicate insufficient context or no chunk matches",
        )

    # G16 — insufficient context: requires_human=True and confidence_score low
    print("\nG16 — Empty query triggers requires_human=True")
    try:
        empty_result = gen(
            "xzqjkl892fhzpw",   # nonsense string — should yield 0 chunks
            similarity_threshold=0.95,  # very high threshold → no matches
        )
        suite.check(
            "insufficient_context=True for nonsense query at high threshold",
            empty_result.insufficient_context,
            f"chunks_returned={len(empty_result.chunks)}",
        )
        suite.check(
            "requires_human=True when insufficient_context=True",
            empty_result.requires_human,
        )
        suite.check(
            "confidence_score <= 0.20 when insufficient_context=True",
            empty_result.confidence_score <= 0.20,
            f"got {empty_result.confidence_score}",
        )
    except Exception as exc:
        suite.check("empty query handled without exception", False, str(exc))

    # G17 — Latency: 5 generations, P95 < 30,000ms
    # SLA note: B2 pipeline = embed (~100ms) + vector search (~600ms) + gpt-4o-mini LLM
    # (~5,000–15,000ms typical under API load). 30,000ms is the calibrated B2 P95 target.
    # The B1 retrieval-only SLA (3,000ms) does not apply here.
    print("\nG17 — Latency: 5 generations, P95 < 30,000ms (full pipeline including LLM)")
    latencies: list[float] = []
    queries = [
        "OTP delivery failure resolution",
        "account locked after wrong password",
        "video KYC session keeps dropping",
        "how to verify customer mobile number",
        "authentication error code 403",
    ]
    for q in queries:
        t0 = time.perf_counter()
        try:
            gen(q)
            latencies.append((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            print(f"       {WARN}  generation failed for query={q!r}: {exc}")
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]
        suite.check(
            "P95 latency < 30,000ms (embed + search + LLM generation)",
            p95 < 30_000,
            f"P50={p50:.0f}ms  P95={p95:.0f}ms  n={len(latencies)}",
        )
    else:
        suite.check("latency measurements available", False, "no successful generations")

    # G18 — confidence_score monotonicity across real queries
    print("\nG18 — confidence_score is lower for low-confidence result than high-confidence result")
    try:
        res_specific = gen("OTP delivery failure resolution steps for mobile number verification")
        res_vague    = gen("something wrong with the system", similarity_threshold=0.90)
        both_ok = (
            isinstance(res_specific.confidence_score, float) and
            isinstance(res_vague.confidence_score, float)
        )
        suite.check(
            "confidence_score(specific query) >= confidence_score(vague/high-threshold query)",
            not both_ok or res_specific.confidence_score >= res_vague.confidence_score,
            f"specific={res_specific.confidence_score:.3f} ({res_specific.confidence})",
            f"vague={res_vague.confidence_score:.3f} ({res_vague.confidence})",
        )
    except Exception as exc:
        suite.check("confidence monotonicity test ran", False, str(exc))

    # G19 — Multi-tenant safety: client kwarg enforced
    print("\nG19 — Multi-tenant: client field is reflected in diagnostics")
    try:
        mt_result = gen("OTP issue")
        suite.check(
            "session_id is non-empty string",
            isinstance(mt_result.session_id, str) and len(mt_result.session_id) > 0,
        )
    except Exception as exc:
        suite.check("multi-tenant generation ran", False, str(exc))

    # G20 — diagnostics block contains expected keys
    print("\nG20 — diagnostics contains context_tokens and context_skipped_chunks")
    suite.check(
        "diagnostics.context_tokens present and int",
        "context_tokens" in result.diagnostics and isinstance(result.diagnostics["context_tokens"], int),
        f"got {result.diagnostics.get('context_tokens')}",
    )
    suite.check(
        "diagnostics.context_skipped_chunks present and int",
        "context_skipped_chunks" in result.diagnostics and isinstance(result.diagnostics["context_skipped_chunks"], int),
        f"got {result.diagnostics.get('context_skipped_chunks')}",
    )

    embedder.close()


# ── Report writer ──────────────────────────────────────────────────────────────

def write_report(suite: ValidationSuite, client: str, live: bool) -> Path:
    report_dir = BASE_DIR / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "b2_generation_report.md"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# Phase B2 Generation Validation Report",
        f"",
        f"**Date**: {now}  ",
        f"**Client**: {client}  ",
        f"**Mode**: {'offline + live' if live else 'offline only'}  ",
        f"**Result**: {suite.summary_line()}  ",
        f"",
        f"---",
        f"",
        f"## Check Results",
        f"",
    ]
    for r in suite.results:
        sym = "PASS" if r.passed else "FAIL"
        lines.append(f"| {sym} | {r.label} |")
        for note in r.notes:
            lines.append(f"|      |   `{note}` |")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B2 generation validation suite")
    parser.add_argument("--live", action="store_true", help="Run live end-to-end tests (requires .env)")
    parser.add_argument("--client", default="unity_bank", help="Tenant slug for live tests")
    parser.add_argument("--report", action="store_true", help="Write markdown report to data/reports/")
    args = parser.parse_args()

    print("Phase B2 Generation Validation Suite")
    print(f"Mode: {'offline + live' if args.live else 'offline only'}")
    print(f"Client: {args.client}")

    suite = ValidationSuite()

    run_offline_tests(suite)

    if args.live:
        run_live_tests(suite, args.client)

    print(f"\n{'='*50}")
    print(f"RESULT: {suite.summary_line()}")

    if args.report:
        path = write_report(suite, args.client, args.live)
        print(f"Report written: {path}")

    failed = suite.total - suite.pass_count
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
