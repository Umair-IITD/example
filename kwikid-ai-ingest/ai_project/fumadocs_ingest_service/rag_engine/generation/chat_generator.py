"""
rag_engine/generation/chat_generator.py

Phase B2 orchestrator: TicketRetriever → context assembly → LLM → GenerationResult.

GenerationResult carries the full structured response:
  answer            — grounded draft text for the support agent
  confidence        — categorical: "high" | "medium" | "low"
  confidence_score  — numeric [0.0, 1.0], derived deterministically from categorical + context signals
  citations         — list of chunk attributions (chunk_num, chunk_type, source_id)
  requires_human    — True when the LLM or Python-side safety rules mandate human review
  follow_up_question — optional clarifying question for the agent
  insufficient_context — True when no chunks were retrieved
"""
from __future__ import annotations

import hashlib as _hashlib
import logging
import os
import re as _re
import threading as _threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from rag_engine.generation.context_assembler import assemble_context, count_tokens
from rag_engine.generation.llm_client import B1LLMClient
from rag_engine.generation.prompt_builder import B2_SYSTEM_PROMPT, build_user_prompt
from rag_engine.retrieval.ticket_retriever import (
    RetrievalRequest,
    RetrievedChunk,
    TicketRetriever,
)
from rag_engine.sop.sop_parser import SopDocumentFlags, parse_sop_content

LOGGER = logging.getLogger(__name__)

# System prompt token count — computed once on first request to avoid cold-import overhead.
# Used by Phase 3 diagnostics to surface overhead_tokens = llm_prompt_tokens - context_tokens.
_SYSTEM_PROMPT_TOKENS: int | None = None
_SYSTEM_PROMPT_TOKENS_LOCK = _threading.Lock()


def _get_system_prompt_tokens() -> int:
    global _SYSTEM_PROMPT_TOKENS
    if _SYSTEM_PROMPT_TOKENS is None:
        with _SYSTEM_PROMPT_TOKENS_LOCK:
            if _SYSTEM_PROMPT_TOKENS is None:
                _SYSTEM_PROMPT_TOKENS = count_tokens(B2_SYSTEM_PROMPT)
    return _SYSTEM_PROMPT_TOKENS


# When DEBUG_RAG=true, chunk dicts include content_preview, retrieval_rank, and
# rerank_score. Used to verify whether generated answers are grounded in retrieved
# chunks or whether the LLM is hallucinating unsupported details. Never enabled
# in production — keep false by default.
_DEBUG_RAG: bool = os.getenv("DEBUG_RAG", "false").strip().lower() in {"1", "true", "yes", "on"}

# Per-chunk character cap applied by the context assembler before the LLM call.
# SOP chunks ingested at CHUNK_TARGET_TOKENS=1200 (~5,400 chars) were systematically
# truncated to 1,400 chars (~26% of content), silently dropping Steps 4-7 of large
# SOPs (hard lock resolution, security freeze, escalation criteria, post-unlock checklist).
# 3,500 chars (~500 words) matches the ingestion target without exceeding the 6k token budget.
_PER_CHUNK_MAX_CHARS: int = int(os.getenv("CHAT_CONTEXT_CHUNK_MAX_CHARS", "3500"))

# Deterministic base scores for each categorical confidence level.
# These are calibrated so that: high > medium > low, and no single level
# occupies more than 0.45 of the [0,1] range, preserving separation.
_CONFIDENCE_BASE: dict[str, float] = {
    "high": 0.82,
    "medium": 0.50,
    "low": 0.18,
}

# ── Stage 1 optimization: adaptive context + fast path ────────────────────────
# Adaptive context budgeting (Priority 2): when exact SOP match is high-confidence,
# SOP chunks are always included and non-SOP chunks share a tight token budget.
# This reduces context from ~841 tokens to ~300-500 on qualifying queries.
_ADAPTIVE_CONTEXT_ENABLED: bool = os.getenv("ADAPTIVE_CONTEXT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
_ADAPTIVE_CONTEXT_BUDGET: int = int(os.getenv("ADAPTIVE_CONTEXT_BUDGET", "650"))
_ADAPTIVE_SOP_THRESHOLD: float = float(os.getenv("ADAPTIVE_SOP_THRESHOLD", "0.60"))

# Early exit fast path (Priority 4): when SOP confidence is very high and no RCA/knowledge
# context is needed, ticket chunks are filtered from context post-retrieval.
# Reduces LLM input tokens; retrieval still runs (shared query), savings are context-side.
_FAST_PATH_ENABLED: bool = os.getenv("FAST_PATH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
_FAST_PATH_SOP_THRESHOLD: float = float(os.getenv("FAST_PATH_SOP_THRESHOLD", "0.75"))

# Stage 0 system prompt token baseline — pre-optimization reference for compression_ratio.
_SYSTEM_PROMPT_TOKENS_BASELINE: int = 903


@dataclass
class GenerationRequest:
    query_text: str
    client: str                             # REQUIRED: tenant slug (unity_bank, rbl_bank, …)
    session_id: Optional[str] = None
    top_k: int = 8
    similarity_threshold: float = 0.27
    persist_history: bool = True
    history_turns: int = 6
    index_version: str = "v1"


@dataclass
class GenerationResult:
    answer: str
    confidence: str                         # "high" | "medium" | "low"
    confidence_score: float                 # numeric [0.0, 1.0] — deterministic, not LLM-derived
    citations: list[dict[str, Any]]
    requires_human: bool                    # True → agent must review before any customer action
    follow_up_question: Optional[str]
    chunks: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    session_id: str
    message_id: str
    insufficient_context: bool


class B1HistoryStore:
    """Persists B2 chat turns in the shared chat_messages table."""

    def __init__(self, supabase_client: Any, table_name: str = "chat_messages") -> None:
        self._client = supabase_client
        self._table = table_name

    def fetch_recent(self, session_id: str, limit: int) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        try:
            response = (
                self._client.table(self._table)
                .select("role,content,created_at")
                .eq("session_id", session_id)
                .order("created_at", desc=False)
                .limit(max(limit * 2, 2))
                .execute()
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning("B1 history fetch failed for session_id=%s", session_id)
            return []
        rows = response.data or []
        turns: list[dict[str, str]] = []
        for row in rows[-(limit * 2):]:
            role, content = row.get("role"), row.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                turns.append({"role": role, "content": content})
        return turns

    def append(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        message_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._client.table(self._table).insert(payload).execute()
        except Exception:  # noqa: BLE001
            LOGGER.warning("B1 history append failed session_id=%s role=%s", session_id, role)


class ChatGenerator:
    """
    Phase B2 chat generator.

    Usage:
        retriever = TicketRetriever(supabase_client, embedder)
        llm = B1LLMClient(api_key=..., model=...)
        history = B1HistoryStore(supabase_client)
        gen = ChatGenerator(retriever, llm, history)
        result = gen.generate(GenerationRequest(query_text="...", client="unity_bank"))
    """

    def __init__(
        self,
        retriever: TicketRetriever,
        llm_client: B1LLMClient,
        history_store: Optional[B1HistoryStore] = None,
        per_chunk_max_chars: int = _PER_CHUNK_MAX_CHARS,
    ) -> None:
        self._retriever = retriever
        self._llm = llm_client
        self._history_store = history_store
        self._per_chunk_max_chars = per_chunk_max_chars

    # ── Shared retrieval + context preparation ────────────────────────────────

    def _compute_retrieval_context(self, request: GenerationRequest) -> dict[str, Any]:
        """Retrieval + classification + context assembly (sync, blocking).

        Starts a history fetch thread before retrieval so both run concurrently.
        Returns all pre-LLM state needed by generate() and stream_generate().
        """
        effective_session_id = request.session_id or str(uuid.uuid4())
        user_msg_id = str(uuid.uuid4())
        assistant_msg_id = str(uuid.uuid4())

        # Start history fetch concurrently with retrieval
        _hist_state: dict[str, Any] = {"history": []}

        def _fetch_history() -> None:
            if self._history_store and request.session_id:
                try:
                    _hist_state["history"] = self._history_store.fetch_recent(
                        request.session_id, request.history_turns
                    )
                except Exception:  # noqa: BLE001
                    pass

        _hist_thread: _threading.Thread | None = None
        if self._history_store and request.session_id:
            _hist_thread = _threading.Thread(target=_fetch_history, daemon=True, name="b2_history")
            _hist_thread.start()

        # ── Retrieval ─────────────────────────────────────────────────────────
        retrieval = self._retriever.retrieve(
            RetrievalRequest(
                query_text=request.query_text,
                client=request.client,
                top_k=request.top_k,
                similarity_threshold=request.similarity_threshold,
                index_version=request.index_version,
            )
        )
        chunks = retrieval.chunks
        insufficient_context = len(chunks) == 0

        # Phase B3: True when any retrieved chunk came from the knowledge table.
        has_knowledge_context = any(c.source_table == "rag_knowledge_chunks" for c in chunks)

        diagnostics: dict[str, Any] = {
            "index_version": request.index_version,
            "returned_count": len(chunks),
            "total_candidates": retrieval.total_candidates,
            "semantic_latency_ms": round(retrieval.semantic_latency_ms, 1),
            "total_latency_ms": round(retrieval.total_latency_ms, 1),
            "has_sop_context": retrieval.has_sop_context,
            "has_rca_context": retrieval.has_rca_context,
            "has_knowledge_context": has_knowledge_context,
            "best_similarity": round(max((c.similarity for c in chunks), default=0.0), 4),
            "retrieval_mode": retrieval.retrieval_metadata.get("retrieval_mode", "semantic_rpc"),
            "used_fallback": retrieval.retrieval_metadata.get("used_fallback", False),
            **retrieval.retrieval_metadata,
        }

        # ── Workflow coverage classification ──────────────────────────────────
        sop_chunks_in_result = [c for c in chunks if c.source_table == "rag_sop_chunks"]
        best_sop_boosted = round(
            max((c.boosted_score for c in sop_chunks_in_result), default=0.0), 4
        )
        classification_sim = (
            max(best_sop_boosted, diagnostics["best_similarity"])
            if retrieval.has_sop_context
            else diagnostics["best_similarity"]
        )

        retrieval_confidence = retrieval.retrieval_metadata.get("retrieval_confidence", "unknown")
        workflow_match_type = _classify_workflow_match(
            has_sop_context=retrieval.has_sop_context,
            has_knowledge_context=has_knowledge_context,
            best_similarity=classification_sim,
            chunk_count=len(chunks),
        )
        diagnostics["workflow_match_type"]    = workflow_match_type
        diagnostics["best_sop_score"]         = best_sop_boosted
        diagnostics["grounding_confidence"]   = retrieval_confidence
        diagnostics["exact_sop_match"]        = workflow_match_type == "exact_match"
        diagnostics["partial_match_detected"] = workflow_match_type == "related_match"

        # ── SOP branch detection (pre-LLM) ────────────────────────────────────
        sop_flags: SopDocumentFlags | None = None
        if sop_chunks_in_result and workflow_match_type in ("exact_match", "related_match"):
            combined_sop_text = "\n\n".join(c.content for c in sop_chunks_in_result)
            sop_flags = parse_sop_content(combined_sop_text)
            if sop_flags.any_set():
                diagnostics["sop_branch_flags"] = sop_flags.to_dict()
        branch_flags: dict[str, Any] = diagnostics.get("sop_branch_flags", {})

        # ── Priority 4: Fast path — filter ticket chunks on very-high-confidence SOP match ──
        # Post-retrieval filter: when SOP confidence is >= 0.75, ticket chunks add noise rather
        # than grounding value. SOP already fully covers the procedure; tickets only add context
        # that the LLM doesn't need for exact SOP answers. Saves ~100-200 input tokens.
        _fast_path = (
            _FAST_PATH_ENABLED
            and workflow_match_type == "exact_match"
            and best_sop_boosted >= _FAST_PATH_SOP_THRESHOLD
            and not retrieval.has_rca_context
            and not has_knowledge_context
        )
        if _fast_path:
            _pre_filter_count = len(chunks)
            chunks = [c for c in chunks if c.source_table == "rag_sop_chunks"]
            _filtered_count = _pre_filter_count - len(chunks)
            diagnostics["fast_path_triggered"] = True
            diagnostics["fast_path_ticket_chunks_filtered"] = _filtered_count
            LOGGER.debug(
                "fast_path: filtered %d ticket chunks (sop_score=%.3f)",
                _filtered_count, best_sop_boosted,
            )
        else:
            diagnostics["fast_path_triggered"] = False
            diagnostics["fast_path_ticket_chunks_filtered"] = 0

        # ── Context assembly ──────────────────────────────────────────────────
        # Phase C2: suppress ISSUE_HEADER chunks from LLM context on exact_match — they
        # carry no grounding value when a matching SOP is present, and cost ~200-400 tokens.
        _suppress_headers = workflow_match_type == "exact_match" and len(sop_chunks_in_result) > 0

        # Priority 2: Adaptive context budgeting — on high-confidence exact SOP match,
        # constrain non-SOP context to a tight budget. SOP chunks are ALWAYS included.
        _use_adaptive = (
            _ADAPTIVE_CONTEXT_ENABLED
            and workflow_match_type == "exact_match"
            and best_sop_boosted >= _ADAPTIVE_SOP_THRESHOLD
            and retrieval_confidence != "low"
        )
        _adaptive_budget_val = _ADAPTIVE_CONTEXT_BUDGET if _use_adaptive else None

        assembled = assemble_context(
            chunks,
            per_chunk_max_chars=self._per_chunk_max_chars,
            suppress_issue_headers=_suppress_headers,
            adaptive_budget=_adaptive_budget_val,
        )
        diagnostics["context_tokens"] = assembled.total_tokens
        diagnostics["context_skipped_chunks"] = assembled.skipped_chunks
        diagnostics["context_chunk_types"] = assembled.context_chunk_types
        diagnostics["context_suppress_issue_headers"] = _suppress_headers
        diagnostics["adaptive_context_enabled"] = _use_adaptive
        diagnostics["adaptive_context_budget"] = _adaptive_budget_val
        diagnostics["suppressed_chunks_count"] = assembled.skipped_chunks

        # ── Join history fetch (it's had the entire retrieval time to complete) ─
        if _hist_thread is not None:
            _hist_thread.join(timeout=2.0)
        history = list(_hist_state["history"])

        return {
            "effective_session_id": effective_session_id,
            "user_msg_id": user_msg_id,
            "assistant_msg_id": assistant_msg_id,
            "chunks": chunks,
            "assembled": assembled,
            "diagnostics": diagnostics,
            "history": history,
            "retrieval": retrieval,
            "insufficient_context": insufficient_context,
            "has_knowledge_context": has_knowledge_context,
            "workflow_match_type": workflow_match_type,
            "retrieval_confidence": retrieval_confidence,
            "branch_flags": branch_flags,
            "sop_chunks_in_result": sop_chunks_in_result,
            "best_sop_boosted": best_sop_boosted,
            "sop_flags": sop_flags,
        }

    # ── Blocking (non-streaming) generation ───────────────────────────────────

    def generate(self, request: GenerationRequest) -> GenerationResult:
        t_gen_start = time.perf_counter()
        ctx = self._compute_retrieval_context(request)
        context_prep_ms = round((time.perf_counter() - t_gen_start) * 1000, 1)

        chunks             = ctx["chunks"]
        assembled          = ctx["assembled"]
        diagnostics        = ctx["diagnostics"]
        history            = ctx["history"]
        retrieval          = ctx["retrieval"]
        insufficient_context  = ctx["insufficient_context"]
        has_knowledge_context = ctx["has_knowledge_context"]
        workflow_match_type   = ctx["workflow_match_type"]
        retrieval_confidence  = ctx["retrieval_confidence"]
        branch_flags          = ctx["branch_flags"]
        effective_session_id  = ctx["effective_session_id"]
        user_msg_id           = ctx["user_msg_id"]
        assistant_msg_id      = ctx["assistant_msg_id"]

        # ── LLM call ──────────────────────────────────────────────────────────
        t_prompt = time.perf_counter()
        user_prompt = build_user_prompt(
            request.query_text,
            context_block=assembled.context_block,
            diagnostics=diagnostics,
            client=request.client,
        )
        prompt_build_ms = round((time.perf_counter() - t_prompt) * 1000, 1)

        t_llm = time.perf_counter()
        _llm_timing: list[dict] = []
        try:
            parsed = self._llm.complete_json(
                system_prompt=B2_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                history=history or None,
                _timing_capture=_llm_timing,
            )
        except Exception as exc:  # noqa: BLE001
            llm_ms = round((time.perf_counter() - t_llm) * 1000, 1)
            total_ms = round((time.perf_counter() - t_gen_start) * 1000, 1)
            LOGGER.error(
                "B2 LLM call failed — returning degraded result: client=%s error=%s",
                request.client,
                type(exc).__name__,
            )
            diagnostics["context_prep_ms"]     = context_prep_ms
            diagnostics["prompt_build_ms"]     = prompt_build_ms
            diagnostics["llm_request_ms"]      = llm_ms
            diagnostics["total_generation_ms"] = total_ms
            return _degraded_result(
                request=request,
                session_id=effective_session_id,
                message_id=assistant_msg_id,
                chunks=chunks,
                diagnostics=diagnostics,
                reason=str(exc),
            )
        llm_request_ms = round((time.perf_counter() - t_llm) * 1000, 1)
        if _llm_timing:
            _u = _llm_timing[0]
            diagnostics["llm_prompt_tokens"]            = _u.get("prompt_tokens")
            diagnostics["llm_completion_tokens"]        = _u.get("completion_tokens")
            diagnostics["llm_total_tokens"]             = _u.get("total_tokens")
            diagnostics["llm_model_name"]               = _u.get("model")
            diagnostics["llm_parse_attempts"]           = _u.get("parse_attempts")
            diagnostics["llm_parse_failures"]           = _u.get("parse_failures")
            diagnostics["llm_schema_retried"]           = _u.get("schema_retried", False)
            diagnostics["llm_schema_validation_failed"] = _u.get("schema_validation_failed", False)

        # Phase A: token breakdown — accounts for every token in the round-trip.
        # overhead_tokens = llm_prompt_tokens - context_tokens (system prompt + user prompt framing).
        # All values are None when the LLM call skipped (schema fallback path).
        _pt = diagnostics.get("llm_prompt_tokens")
        _ct = diagnostics.get("llm_completion_tokens")
        _ctx = assembled.total_tokens
        diagnostics["token_breakdown"] = {
            "context_tokens":      _ctx,
            "llm_prompt_tokens":   _pt,
            "llm_completion_tokens": _ct,
            "overhead_tokens":     (max(0, _pt - _ctx)) if _pt is not None else None,
            "total_tokens":        ((_pt or 0) + (_ct or 0)) if (_pt is not None or _ct is not None) else None,
        }

        # Phase 3: deterministic rendering diagnostics — stable hash for regression detection
        _norm_ans = _re.sub(r'\s+', ' ', str(parsed.get("answer") or "")).strip().lower()
        diagnostics["answer_structure_hash"] = _hashlib.sha256(_norm_ans.encode()).hexdigest()[:12]
        diagnostics["deterministic_rendering_enabled"] = True
        # Priority 1 diagnostics: track system prompt compression vs Stage 0 baseline
        _spt = _get_system_prompt_tokens()
        diagnostics["system_prompt_tokens"] = _spt
        diagnostics["compacted_system_prompt_tokens"] = _spt
        diagnostics["original_system_prompt_tokens"] = _SYSTEM_PROMPT_TOKENS_BASELINE
        diagnostics["compression_ratio"] = round(_spt / _SYSTEM_PROMPT_TOKENS_BASELINE, 3)

        # Schema fallback path: both validation attempts failed
        if parsed.get("_schema_failure"):
            diagnostics["llm_schema_failure"]        = True
            diagnostics["llm_schema_failure_reason"] = parsed.get("_schema_failure_reason")

        # ── Normalize output (Phase 3: LLM provides only answer + citations) ─
        answer = _sanitize_answer(str(parsed.get("answer") or ""))
        raw_citations = parsed.get("citations")
        citations = [c for c in (raw_citations or []) if isinstance(c, dict)]

        # Derive confidence programmatically from retrieval signals
        confidence = _derive_confidence_programmatic(
            insufficient_context=insufficient_context,
            has_sop_context=retrieval.has_sop_context,
            has_knowledge_context=has_knowledge_context,
            workflow_match_type=workflow_match_type,
            chunk_count=len(chunks),
        )

        follow_up: Optional[str] = _derive_follow_up_question(
            workflow_match_type=workflow_match_type,
            insufficient_context=insufficient_context,
        )

        # ── Branch completeness check (post-LLM) ─────────────────────────────
        if branch_flags and workflow_match_type == "exact_match":
            completeness_warnings = _check_answer_completeness(answer, branch_flags)
            if completeness_warnings:
                LOGGER.warning(
                    "B2 branch_completeness: client=%s missing_branches=%d warnings=%s",
                    request.client,
                    len(completeness_warnings),
                    completeness_warnings,
                )
                diagnostics["branch_completeness_warnings"] = completeness_warnings
                if confidence == "high" and len(completeness_warnings) >= 2:
                    confidence = "medium"
                    LOGGER.info(
                        "B2 confidence high->medium: client=%s branch_warnings=%d",
                        request.client,
                        len(completeness_warnings),
                    )

        # Never claim "high" when no context was retrieved
        if insufficient_context and confidence == "high":
            confidence = "medium"

        # Derive requires_human programmatically (Phase 3: LLM no longer provides this)
        requires_human = _derive_requires_human(
            llm_flag=None,
            insufficient_context=insufficient_context,
            confidence=confidence,
            has_sop_context=retrieval.has_sop_context,
            workflow_match_type=workflow_match_type,
            retrieval_confidence=retrieval_confidence,
        )

        # Derive numeric confidence score deterministically
        confidence_score = _derive_confidence_score(
            confidence,
            insufficient_context=insufficient_context,
            has_sop=retrieval.has_sop_context,
            has_rca=retrieval.has_rca_context,
            has_knowledge=has_knowledge_context,
            chunk_count=len(chunks),
            requires_human=requires_human,
        )

        # ── Automation safety gate + governance diagnostics ───────────────────
        automation_safe = _is_automation_safe(
            workflow_match_type=workflow_match_type,
            confidence=confidence,
            requires_human=requires_human,
            retrieval_confidence=retrieval_confidence,
        )
        escalation_reason = _escalation_trigger_reason(
            llm_flag=False,  # Phase 3: LLM no longer provides escalation flag
            insufficient_context=insufficient_context,
            confidence=confidence,
            has_sop_context=retrieval.has_sop_context,
            workflow_match_type=workflow_match_type,
            retrieval_confidence=retrieval_confidence,
        )
        block_reason = (
            None if automation_safe else
            _automation_block_reason(
                workflow_match_type=workflow_match_type,
                confidence=confidence,
                requires_human=requires_human,
                llm_flag=False,  # Phase 3: LLM no longer provides this
                retrieval_confidence=retrieval_confidence,
            )
        )
        diagnostics["escalation_trigger_reason"] = escalation_reason
        diagnostics["automation_safe"]            = automation_safe
        diagnostics["automation_block_reason"]    = block_reason

        # ── Generation timing ─────────────────────────────────────────────────
        total_generation_ms = round((time.perf_counter() - t_gen_start) * 1000, 1)
        diagnostics["context_prep_ms"]     = context_prep_ms
        diagnostics["prompt_build_ms"]     = prompt_build_ms
        diagnostics["llm_request_ms"]      = llm_request_ms
        diagnostics["total_generation_ms"] = total_generation_ms

        # ── Fire-and-forget history persistence ───────────────────────────────
        if request.persist_history and self._history_store:
            _eff_sid = effective_session_id
            _uid, _aid = user_msg_id, assistant_msg_id
            _q, _cl, _nc = request.query_text, request.client, len(chunks)
            _ans, _conf, _cscore = answer, confidence, confidence_score
            _rh, _cit = requires_human, citations
            _fup, _ic = follow_up, insufficient_context
            _diag = dict(diagnostics)

            def _persist() -> None:
                try:
                    self._history_store.append(
                        session_id=_eff_sid,
                        role="user",
                        content=_q,
                        message_id=_uid,
                        metadata={"match_count": _nc, "client": _cl},
                    )
                    self._history_store.append(
                        session_id=_eff_sid,
                        role="assistant",
                        content=_ans,
                        message_id=_aid,
                        metadata={
                            "confidence": _conf,
                            "confidence_score": _cscore,
                            "requires_human": _rh,
                            "citations": _cit,
                            "follow_up_question": _fup,
                            "insufficient_context": _ic,
                            "diagnostics": _diag,
                            "source": "b2_rag",
                        },
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.warning("b2_history_persist_failed session_id=%s", _eff_sid)

            _threading.Thread(target=_persist, daemon=True, name="b2_persist").start()

        LOGGER.info(
            "B2 generate: client=%s chunks=%d sop=%d rca=%s knowledge=%s "
            "confidence=%s confidence_score=%.3f requires_human=%s "
            "context_tokens=%d skipped=%d retrieval_mode=%s used_fallback=%s "
            "embedding_ms=%.0f context_prep_ms=%.0f llm_ms=%.0f total_ms=%.0f",
            request.client,
            len(chunks),
            assembled.sop_count,
            retrieval.has_rca_context,
            has_knowledge_context,
            confidence,
            confidence_score,
            requires_human,
            assembled.total_tokens,
            assembled.skipped_chunks,
            retrieval.retrieval_metadata.get("retrieval_mode", "semantic_rpc"),
            retrieval.retrieval_metadata.get("used_fallback", False),
            retrieval.retrieval_metadata.get("embedding_latency_ms", 0),
            context_prep_ms,
            llm_request_ms,
            total_generation_ms,
        )

        return GenerationResult(
            answer=answer,
            confidence=confidence,
            confidence_score=confidence_score,
            citations=citations,
            requires_human=requires_human,
            follow_up_question=follow_up,
            chunks=[_chunk_to_dict(c, rank=i) for i, c in enumerate(chunks)],
            diagnostics=diagnostics,
            session_id=effective_session_id,
            message_id=assistant_msg_id,
            insufficient_context=insufficient_context,
        )

    # ── True streaming generation ─────────────────────────────────────────────

    async def stream_generate(self, request: GenerationRequest) -> AsyncGenerator[str, None]:
        """True streaming generation — yields SSE 'data: ...' lines.

        Retrieval runs synchronously in a thread pool (~3 s), then LLM tokens
        stream back immediately. TTFB is bounded by retrieval latency, not total
        LLM generation time.

        Event types emitted:
          {"type": "metadata", "session_id": ..., "confidence": ..., ...}
          {"type": "token", "content": "<delta>"}
          {"type": "done", "answer": "<full sanitized text>", ...}
          {"type": "error", "message": "..."}
        """
        import asyncio
        import json as _json

        from rag_engine.generation.prompt_builder import (
            B2_STREAM_SYSTEM_PROMPT,
            build_stream_user_prompt,
        )

        ctx = await asyncio.to_thread(self._compute_retrieval_context, request)

        chunks                = ctx["chunks"]
        assembled             = ctx["assembled"]
        diagnostics           = ctx["diagnostics"]
        retrieval             = ctx["retrieval"]
        insufficient_context  = ctx["insufficient_context"]
        has_knowledge_context = ctx["has_knowledge_context"]
        workflow_match_type   = ctx["workflow_match_type"]
        retrieval_confidence  = ctx["retrieval_confidence"]
        effective_session_id  = ctx["effective_session_id"]
        user_msg_id           = ctx["user_msg_id"]
        assistant_msg_id      = ctx["assistant_msg_id"]

        # Pre-compute confidence programmatically (same function as non-streaming path)
        confidence = _derive_confidence_programmatic(
            insufficient_context=insufficient_context,
            has_sop_context=retrieval.has_sop_context,
            has_knowledge_context=has_knowledge_context,
            workflow_match_type=workflow_match_type,
            chunk_count=len(chunks),
        )

        requires_human = _derive_requires_human(
            llm_flag=None,
            insufficient_context=insufficient_context,
            confidence=confidence,
            has_sop_context=retrieval.has_sop_context,
            workflow_match_type=workflow_match_type,
            retrieval_confidence=retrieval_confidence,
        )

        # Metadata event — client can update UI before LLM starts
        yield (
            "data: " + _json.dumps({
                "type": "metadata",
                "session_id": effective_session_id,
                "message_id": assistant_msg_id,
                "confidence": confidence,
                "requires_human": requires_human,
                "workflow_match_type": workflow_match_type,
                "chunk_count": len(chunks),
                "context_tokens": assembled.total_tokens,
            }) + "\n\n"
        )

        user_prompt = build_stream_user_prompt(
            request.query_text,
            context_block=assembled.context_block,
            diagnostics=diagnostics,
            client=request.client,
        )

        token_parts: list[str] = []
        t_llm_stream_start = time.perf_counter()
        first_token_ms: float | None = None
        try:
            async for token in self._llm.stream_complete_async(
                system_prompt=B2_STREAM_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            ):
                if first_token_ms is None:
                    first_token_ms = round((time.perf_counter() - t_llm_stream_start) * 1000, 1)
                token_parts.append(token)
                yield "data: " + _json.dumps({"type": "token", "content": token}) + "\n\n"
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("B2 stream_generate LLM error client=%s: %s", request.client, exc)
            yield "data: " + _json.dumps({"type": "error", "message": "LLM streaming failed"}) + "\n\n"
            return
        stream_completion_ms = round((time.perf_counter() - t_llm_stream_start) * 1000, 1)

        answer = _sanitize_answer("".join(token_parts))

        if request.persist_history and self._history_store:
            _eff_sid, _uid, _aid = effective_session_id, user_msg_id, assistant_msg_id
            _q, _cl, _nc = request.query_text, request.client, len(chunks)
            _ans, _conf, _rh = answer, confidence, requires_human

            def _persist() -> None:
                try:
                    self._history_store.append(
                        session_id=_eff_sid, role="user", content=_q,
                        message_id=_uid, metadata={"match_count": _nc, "client": _cl},
                    )
                    self._history_store.append(
                        session_id=_eff_sid, role="assistant", content=_ans,
                        message_id=_aid, metadata={
                            "confidence": _conf, "requires_human": _rh, "source": "b2_stream",
                        },
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.warning("b2_stream_persist_failed session_id=%s", _eff_sid)

            _threading.Thread(target=_persist, daemon=True, name="b2_stream_persist").start()

        yield (
            "data: " + _json.dumps({
                "type": "done",
                "answer": answer,
                "session_id": effective_session_id,
                "message_id": assistant_msg_id,
                "first_token_ms": first_token_ms,
                "stream_completion_ms": stream_completion_ms,
            }) + "\n\n"
        )


# ── Private helpers ────────────────────────────────────────────────────────────


# ── Workflow match classification thresholds ──────────────────────────────────
# "exact_match"  : SOP present + best_similarity >= this → direct SOP coverage
# "related_match": SOP/knowledge present + similarity >= lower bound → adjacent coverage
# These are conservative starting values; tune via env if retrieval patterns shift.
# exact_match threshold is evaluated against the EFFECTIVE classification similarity,
# which uses the boosted SOP score (raw cosine + 0.15 SOP boost) rather than raw cosine.
# This means a SOP chunk with raw cosine ≥ 0.40 (boosted to ≥ 0.55) triggers exact_match.
# Previously 0.62 — calibrated against test values that already represented boosted scores,
# causing production SOP hits to be misclassified as related_match.
_WORKFLOW_EXACT_MATCH_SIMILARITY: float  = float(os.getenv("WORKFLOW_EXACT_SIMILARITY",  "0.55"))
_WORKFLOW_RELATED_MATCH_SIMILARITY: float = float(os.getenv("WORKFLOW_RELATED_SIMILARITY", "0.35"))


def _classify_workflow_match(
    *,
    has_sop_context: bool,
    has_knowledge_context: bool,
    best_similarity: float,
    chunk_count: int,
) -> str:
    """Classify how well retrieved context covers the query's workflow.

    Returns one of: "exact_match" | "related_match" | "weak_match" | "no_match"

    Used to gate automation safety and to signal the LLM about coverage gaps.
    Does NOT use retrieval_confidence (RRF overlap) — that's a separate signal
    used in the governance rule, not the coverage classification.
    """
    if chunk_count == 0:
        return "no_match"
    if has_sop_context and best_similarity >= _WORKFLOW_EXACT_MATCH_SIMILARITY:
        return "exact_match"
    if (has_sop_context or has_knowledge_context) and best_similarity >= _WORKFLOW_RELATED_MATCH_SIMILARITY:
        return "related_match"
    return "weak_match"


def _derive_confidence_programmatic(
    *,
    insufficient_context: bool,
    has_sop_context: bool,
    has_knowledge_context: bool,
    workflow_match_type: str,
    chunk_count: int,
) -> str:
    """Derive categorical confidence from retrieval signals (Phase 3 — no LLM input).

    Matches the logic in stream_generate() so both paths produce identical confidence.
    """
    if insufficient_context:
        return "low"
    if has_sop_context and workflow_match_type == "exact_match":
        return "high"
    if has_sop_context or has_knowledge_context or workflow_match_type == "related_match":
        return "medium"
    return "low"


def _derive_follow_up_question(
    *,
    workflow_match_type: str,
    insufficient_context: bool,
) -> Optional[str]:
    """Derive a follow-up question programmatically based on retrieval coverage (Phase 3)."""
    if insufficient_context or workflow_match_type == "no_match":
        return "Could you provide more details about the issue so we can locate the relevant procedure?"
    if workflow_match_type == "related_match":
        return "Is there a specific step or condition in this workflow that needs clarification?"
    return None


def _derive_requires_human(
    *,
    llm_flag: Any,
    insufficient_context: bool,
    confidence: str,
    has_sop_context: bool,
    workflow_match_type: str = "no_match",
    retrieval_confidence: str = "unknown",
) -> bool:
    """Determine whether human review is mandatory.

    Rules applied in priority order:
    1. LLM flag — always respected (trust escalation intent)
    2. insufficient_context — zero chunks cannot auto-resolve
    3. low confidence with no SOP anchor
    4. Governance rule: non-exact workflow coverage with explicit low/medium retrieval
       confidence. Skipped when retrieval_confidence is "unknown" (semantic-only path
       or missing signal) to avoid false escalations on deployments without hybrid RRF.
    """
    if llm_flag is True:
        return True
    if insufficient_context:
        return True
    if confidence == "low" and not has_sop_context:
        return True
    # Governance rule: weak workflow coverage + non-high retrieval confidence → human review.
    # "unknown" means the hybrid RRF confidence is not available (semantic-only mode) —
    # skip this rule in that case to avoid false escalations.
    if retrieval_confidence not in ("unknown", "high") and workflow_match_type != "exact_match":
        return True
    return False


def _escalation_trigger_reason(
    *,
    llm_flag: bool,
    insufficient_context: bool,
    confidence: str,
    has_sop_context: bool,
    workflow_match_type: str,
    retrieval_confidence: str,
) -> Optional[str]:
    """Return the primary reason human escalation was triggered, or None."""
    if llm_flag:
        return "llm_flag"
    if insufficient_context:
        return "insufficient_context"
    if confidence == "low" and not has_sop_context:
        return "low_confidence_no_sop"
    if retrieval_confidence not in ("unknown", "high") and workflow_match_type != "exact_match":
        return "weak_workflow_coverage"
    return None


def _is_automation_safe(
    *,
    workflow_match_type: str,
    confidence: str,
    requires_human: bool,
    retrieval_confidence: str,
) -> bool:
    """True only when ALL four conditions for safe automated sending are met.

    Conservative by design — all four gates must pass. Future-proofs the system
    for an eventual auto-send path without relaxing any individual gate.
    """
    return (
        not requires_human
        and confidence == "high"
        and workflow_match_type == "exact_match"
        and retrieval_confidence == "high"
    )


def _automation_block_reason(
    *,
    workflow_match_type: str,
    confidence: str,
    requires_human: bool,
    llm_flag: bool,
    retrieval_confidence: str,
) -> Optional[str]:
    """Return the primary reason automation was blocked, or None if automation is safe."""
    if llm_flag:
        return "llm_requested_human_review"
    if requires_human and workflow_match_type == "no_match":
        return "no_workflow_coverage"
    if requires_human and workflow_match_type in ("weak_match", "related_match"):
        return "partial_workflow_coverage"
    if requires_human:
        return "requires_human"
    if workflow_match_type != "exact_match":
        return f"workflow_match_is_{workflow_match_type}"
    if confidence != "high":
        return f"confidence_is_{confidence}"
    if retrieval_confidence != "high":
        return f"retrieval_confidence_is_{retrieval_confidence}"
    return None


def _derive_confidence_score(
    confidence: str,
    *,
    insufficient_context: bool,
    has_sop: bool,
    has_rca: bool,
    has_knowledge: bool = False,
    chunk_count: int,
    requires_human: bool,
) -> float:
    """Derive a numeric [0.0, 1.0] confidence score from categorical + context signals.

    Deterministic derivation is more reliable than asking the LLM to produce a float.
    Adjustments are additive and small so the categorical bucket is always dominant.

    Phase B3: has_knowledge adds +0.02 when knowledge chunks are present.
    """
    if insufficient_context:
        return 0.10

    base = _CONFIDENCE_BASE.get(confidence, 0.18)
    adj = 0.0
    if has_sop:
        adj += 0.05   # SOP is authoritative evidence
    if has_rca:
        adj += 0.03   # proven resolution pattern exists
    if has_knowledge:
        adj += 0.02   # Phase B3: institutional knowledge present
    if chunk_count >= 5:
        adj += 0.02   # rich context pool

    score = base + adj
    if requires_human:
        score = min(score, 0.75)   # human gate → cap at 0.75

    return round(min(0.95, max(0.05, score)), 3)


def _degraded_result(
    *,
    request: GenerationRequest,
    session_id: str,
    message_id: str,
    chunks: list[RetrievedChunk],
    diagnostics: dict[str, Any],
    reason: str,
) -> GenerationResult:
    """Return a safe requires_human=True result when the LLM call fails entirely.

    The retrieved chunks are preserved so the agent can still see what the retrieval
    layer found. The answer explicitly states the service is unavailable rather than
    silently returning an empty or fabricated response.
    """
    degraded_diagnostics = {
        **diagnostics,
        "llm_failure":              True,
        "llm_failure_reason":       reason,
        # Governance fields — always set on the degraded path
        "escalation_trigger_reason": "llm_failure",
        "automation_safe":           False,
        "automation_block_reason":   "llm_failure",
    }
    LOGGER.warning(
        "Returning degraded GenerationResult: client=%s reason=%s",
        request.client,
        reason,
    )
    return GenerationResult(
        answer=(
            "The AI generation service is temporarily unavailable. "
            "Please handle this support ticket manually."
        ),
        confidence="low",
        confidence_score=0.05,
        citations=[],
        requires_human=True,
        follow_up_question=None,
        chunks=[_chunk_to_dict(c, rank=i) for i, c in enumerate(chunks)],
        diagnostics=degraded_diagnostics,
        session_id=session_id,
        message_id=message_id,
        insufficient_context=len(chunks) == 0,
    )


def _check_answer_completeness(
    answer: str,
    branch_flags: dict[str, Any],
) -> list[str]:
    """Check whether the generated answer preserves critical SOP branches (post-LLM).

    Returns a list of warning strings for branches detected in retrieved SOP chunks
    but absent from the generated answer. Used for confidence downgrade and monitoring.
    This is a heuristic keyword check — occasional false positives are possible.
    """
    if not branch_flags or not answer:
        return []

    a = answer.lower()
    warnings: list[str] = []

    if branch_flags.get("has_escalation_branches"):
        if not any(kw in a for kw in (
            "escalat", "security team", "fraud", "human review",
            "supervisor", "account takeover", "security team",
        )):
            warnings.append("SOP has escalation conditions — answer missing escalation language")

    if branch_flags.get("has_denial_branches"):
        if not any(kw in a for kw in (
            "do not", "cannot", "one factor", "zero factor",
            "if only", "visit the nearest", "refused",
        )):
            warnings.append("SOP has denial/restriction branches — answer missing restriction language")

    if branch_flags.get("has_security_freeze"):
        if not any(kw in a for kw in (
            "security freeze", "security team", "cannot be resolved",
            "pending review", "clearance", "not resolvable",
        )):
            warnings.append("SOP has Security Freeze path — answer does not address it")

    if branch_flags.get("has_post_resolution"):
        if not any(kw in a for kw in (
            "log", "confirm", "resolve the ticket", "checklist",
            "audit", "before closing", "successful login",
        )):
            warnings.append("SOP has post-resolution steps — answer omits them")

    return warnings


# Defense-in-depth: compiled patterns for leaked internal references.
# These should never appear in answer prose per system prompt rules, but this
# post-processor catches the rare cases where the LLM ignores those instructions.
_CHUNK_REF_RE = _re.compile(r"\s*##\s*\d+", _re.IGNORECASE)
_SOP_ID_FIELD_RE = _re.compile(r"\bsop_id\s*=\s*\S+", _re.IGNORECASE)
_DIAG_FIELD_RE = _re.compile(
    r"\b(workflow_match_type|exact_sop_match|grounding_confidence|"
    r"retrieval_confidence|partial_match_detected|automation_safe|"
    r"automation_block_reason|escalation_trigger_reason|best_sop_score|"
    r"sop_branch_flags|branch_completeness_warnings|index_version|"
    r"has_escalation_branches|has_denial_branches|has_security_freeze|"
    r"has_post_resolution|has_mandatory_warnings)\s*[=:]\s*\S+",
    _re.IGNORECASE,
)


def _sanitize_answer(answer: str) -> str:
    """Strip leaked internal metadata from the LLM answer text (defense-in-depth).

    The system prompt prohibits chunk ##N refs, sop_id= field strings, and diagnostic
    field assignments in answer prose. This catches the rare cases where the LLM produces
    them anyway. Only strips metadata markers — never strips SOP procedural content.
    """
    if not answer:
        return answer
    answer = _CHUNK_REF_RE.sub("", answer)
    answer = _SOP_ID_FIELD_RE.sub("", answer)
    answer = _DIAG_FIELD_RE.sub("", answer)
    # Collapse any double-spaces left by the substitutions
    answer = _re.sub(r"  +", " ", answer)
    return answer.strip()


def _chunk_to_dict(chunk: RetrievedChunk, *, rank: int = 0) -> dict[str, Any]:
    d: dict[str, Any] = {
        "chunk_id": chunk.chunk_id,
        "ticket_id": chunk.ticket_id,
        "sop_id": chunk.sop_id,
        "chunk_type": chunk.chunk_type,
        "similarity": round(chunk.similarity, 4),
        "boosted_score": round(chunk.boosted_score, 4),
        "source_table": chunk.source_table,
        "has_rca": chunk.has_rca,
        "has_sop": chunk.has_sop,
    }
    # Phase B3: include knowledge metadata when present
    if chunk.knowledge_class is not None:
        d["knowledge_class"] = chunk.knowledge_class
    if chunk.quality_score is not None:
        d["quality_score"] = round(chunk.quality_score, 3)

    # Debug-only fields: expose chunk text and ranking signals for grounding
    # evaluation and hallucination debugging. Never enabled in production.
    # Set DEBUG_RAG=true in env to activate. Embeddings are never exposed.
    if _DEBUG_RAG:
        preview = chunk.content[:800]
        if len(chunk.content) > 800:
            preview += "…"
        d["content_preview"] = preview
        d["retrieval_rank"] = rank + 1   # 1-based rank in the returned chunk list
        d["rerank_score"] = round(chunk.boosted_score, 4)  # effective post-boost score

    return d
