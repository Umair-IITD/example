"""
rag_engine/generation/chat_generator.py

Phase B2 orchestrator: TicketRetriever → context assembly → LLM → GenerationResult.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from rag_engine.generation.context_assembler import assemble_context
from rag_engine.generation.llm_client import B1LLMClient
from rag_engine.generation.prompt_builder import B1_SYSTEM_PROMPT, build_user_prompt
from rag_engine.retrieval.ticket_retriever import (
    RetrievalRequest,
    RetrievedChunk,
    TicketRetriever,
)

LOGGER = logging.getLogger(__name__)

_VALID_CONFIDENCE = {"high", "medium", "low"}


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
    confidence: str
    citations: list[dict[str, Any]]
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
        parsed = self._llm.complete_json(
            system_prompt=B1_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            history=history or None,
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

        # Never claim "high" confidence when no context was retrieved
        if insufficient_context and confidence == "high":
            confidence = "medium"

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
                    "citations": citations,
                    "follow_up_question": follow_up,
                    "insufficient_context": insufficient_context,
                    "diagnostics": diagnostics,
                    "source": "b1_rag",
                },
            )

        LOGGER.info(
            "B2 generate: client=%s, chunks=%d, sop=%d, has_rca=%s, confidence=%s",
            request.client,
            len(chunks),
            assembled.sop_count,
            retrieval.has_rca_context,
            confidence,
        )

        return GenerationResult(
            answer=answer,
            confidence=confidence,
            citations=citations,
            follow_up_question=follow_up,
            chunks=[_chunk_to_dict(c) for c in chunks],
            diagnostics=diagnostics,
            session_id=effective_session_id,
            message_id=assistant_msg_id,
            insufficient_context=insufficient_context,
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
