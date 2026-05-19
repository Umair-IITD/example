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

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from rag_engine.generation.context_assembler import assemble_context
from rag_engine.generation.llm_client import B1LLMClient
from rag_engine.generation.prompt_builder import B2_SYSTEM_PROMPT, build_user_prompt
from rag_engine.retrieval.ticket_retriever import (
    RetrievalRequest,
    RetrievedChunk,
    TicketRetriever,
)

LOGGER = logging.getLogger(__name__)

_VALID_CONFIDENCE = {"high", "medium", "low"}

# Deterministic base scores for each categorical confidence level.
# These are calibrated so that: high > medium > low, and no single level
# occupies more than 0.45 of the [0,1] range, preserving separation.
_CONFIDENCE_BASE: dict[str, float] = {
    "high": 0.82,
    "medium": 0.50,
    "low": 0.18,
}


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
        per_chunk_max_chars: int = 1400,
    ) -> None:
        self._retriever = retriever
        self._llm = llm_client
        self._history_store = history_store
        self._per_chunk_max_chars = per_chunk_max_chars

    def generate(self, request: GenerationRequest) -> GenerationResult:
        effective_session_id = request.session_id or str(uuid.uuid4())
        user_msg_id = str(uuid.uuid4())
        assistant_msg_id = str(uuid.uuid4())

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

        diagnostics: dict[str, Any] = {
            "returned_count": len(chunks),
            "total_candidates": retrieval.total_candidates,
            "semantic_latency_ms": round(retrieval.semantic_latency_ms, 1),
            "total_latency_ms": round(retrieval.total_latency_ms, 1),
            "has_sop_context": retrieval.has_sop_context,
            "has_rca_context": retrieval.has_rca_context,
            "best_similarity": round(max((c.similarity for c in chunks), default=0.0), 4),
            **retrieval.retrieval_metadata,
        }

        # ── Context assembly ──────────────────────────────────────────────────
        assembled = assemble_context(chunks, per_chunk_max_chars=self._per_chunk_max_chars)
        diagnostics["context_tokens"] = assembled.total_tokens
        diagnostics["context_skipped_chunks"] = assembled.skipped_chunks

        # ── History ───────────────────────────────────────────────────────────
        history: list[dict[str, str]] = []
        if self._history_store and request.session_id:
            history = self._history_store.fetch_recent(request.session_id, request.history_turns)

        # ── LLM call ──────────────────────────────────────────────────────────
        user_prompt = build_user_prompt(
            request.query_text,
            context_block=assembled.context_block,
            diagnostics=diagnostics,
            client=request.client,
        )
        try:
            parsed = self._llm.complete_json(
                system_prompt=B2_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                history=history or None,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "B2 LLM call failed — returning degraded result: client=%s error=%s",
                request.client,
                type(exc).__name__,
            )
            return _degraded_result(
                request=request,
                session_id=effective_session_id,
                message_id=assistant_msg_id,
                chunks=chunks,
                diagnostics=diagnostics,
                reason=str(exc),
            )

        # ── Normalize output ──────────────────────────────────────────────────
        answer = str(parsed.get("answer") or "").strip()
        confidence_raw = str(parsed.get("confidence") or "").strip().lower()
        confidence = confidence_raw if confidence_raw in _VALID_CONFIDENCE else "low"

        raw_citations = parsed.get("citations")
        citations = [c for c in (raw_citations or []) if isinstance(c, dict)]

        follow_up_raw = parsed.get("follow_up_question")
        follow_up: Optional[str] = (
            follow_up_raw.strip() if isinstance(follow_up_raw, str) else None
        ) or None

        # Never claim "high" when no context was retrieved
        if insufficient_context and confidence == "high":
            confidence = "medium"

        # Derive requires_human: LLM signal + Python-side safety overrides
        llm_requires_human = parsed.get("requires_human")
        requires_human = _derive_requires_human(
            llm_flag=llm_requires_human,
            insufficient_context=insufficient_context,
            confidence=confidence,
            has_sop_context=retrieval.has_sop_context,
        )

        # Derive numeric confidence score deterministically from categorical + context signals
        confidence_score = _derive_confidence_score(
            confidence,
            insufficient_context=insufficient_context,
            has_sop=retrieval.has_sop_context,
            has_rca=retrieval.has_rca_context,
            chunk_count=len(chunks),
            requires_human=requires_human,
        )

        # ── Persist history ───────────────────────────────────────────────────
        if request.persist_history and self._history_store:
            self._history_store.append(
                session_id=effective_session_id,
                role="user",
                content=request.query_text,
                message_id=user_msg_id,
                metadata={"match_count": len(chunks), "client": request.client},
            )
            self._history_store.append(
                session_id=effective_session_id,
                role="assistant",
                content=answer,
                message_id=assistant_msg_id,
                metadata={
                    "confidence": confidence,
                    "confidence_score": confidence_score,
                    "requires_human": requires_human,
                    "citations": citations,
                    "follow_up_question": follow_up,
                    "insufficient_context": insufficient_context,
                    "diagnostics": diagnostics,
                    "source": "b2_rag",
                },
            )

        LOGGER.info(
            "B2 generate: client=%s chunks=%d sop=%d rca=%s confidence=%s "
            "confidence_score=%.3f requires_human=%s",
            request.client,
            len(chunks),
            assembled.sop_count,
            retrieval.has_rca_context,
            confidence,
            confidence_score,
            requires_human,
        )

        return GenerationResult(
            answer=answer,
            confidence=confidence,
            confidence_score=confidence_score,
            citations=citations,
            requires_human=requires_human,
            follow_up_question=follow_up,
            chunks=[_chunk_to_dict(c) for c in chunks],
            diagnostics=diagnostics,
            session_id=effective_session_id,
            message_id=assistant_msg_id,
            insufficient_context=insufficient_context,
        )


# ── Private helpers ────────────────────────────────────────────────────────────


def _derive_requires_human(
    *,
    llm_flag: Any,
    insufficient_context: bool,
    confidence: str,
    has_sop_context: bool,
) -> bool:
    """Determine whether human review is mandatory.

    The LLM's requires_human signal is respected (trust escalation intent),
    then overridden by Python-side safety conditions that the LLM cannot judge:
    e.g. zero retrieval, or low confidence with no SOP anchor.
    """
    if llm_flag is True:
        return True
    if insufficient_context:
        return True
    if confidence == "low" and not has_sop_context:
        return True
    return False


def _derive_confidence_score(
    confidence: str,
    *,
    insufficient_context: bool,
    has_sop: bool,
    has_rca: bool,
    chunk_count: int,
    requires_human: bool,
) -> float:
    """Derive a numeric [0.0, 1.0] confidence score from categorical + context signals.

    Deterministic derivation is more reliable than asking the LLM to produce a float.
    Adjustments are additive and small so the categorical bucket is always dominant.
    """
    if insufficient_context:
        return 0.10

    base = _CONFIDENCE_BASE.get(confidence, 0.18)
    adj = 0.0
    if has_sop:
        adj += 0.05   # SOP is authoritative evidence
    if has_rca:
        adj += 0.03   # proven resolution pattern exists
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
    degraded_diagnostics = {**diagnostics, "llm_failure": True, "llm_failure_reason": reason}
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
        chunks=[_chunk_to_dict(c) for c in chunks],
        diagnostics=degraded_diagnostics,
        session_id=session_id,
        message_id=message_id,
        insufficient_context=len(chunks) == 0,
    )


def _chunk_to_dict(chunk: RetrievedChunk) -> dict[str, Any]:
    return {
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
