from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from supabase import Client, create_client

from app.config import Settings
from app.query import run_query

LOGGER = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are KwikID Support AI, assisting human support agents.

## Evidence hierarchy (strict)
1) AUTHORITATIVE: The "Retrieved context chunks" section in the CURRENT user message. Every factual claim in `answer` MUST be supported by at least one cited chunk (see Citations).
2) SECONDARY: The "Diagnostics" JSON in the CURRENT user message (retrieval quality, counts, thresholds). Use it to calibrate confidence and wording, not to invent facts.
3) NOT AUTHORITATIVE: Prior conversation turns in this thread. They help interpret follow-ups (e.g. "that", "the steps above") but MUST NOT introduce facts that are not in the current chunks. If the user refers to something not present in chunks, say you need a different query or more sources.

## Grounding rules
- Answer ONLY from the retrieved chunks for factual/product/policy content. If chunks are empty or say "(no context retrieved)", state clearly that you have no retrieved evidence, set confidence to "low", cite an empty array or only what exists, and use `follow_up_question` to ask what to search or which product/tenant/ticket scope applies.
- Do not invent: product behavior, SLAs, policies, IDs, dates, URLs, version numbers, config keys, API fields, or ticket numbers. If a detail is missing in chunks, say it is not in the provided context instead of guessing.
- If chunks conflict on a fact, summarize both positions, name the disagreement, and set confidence to at most "medium". Prefer the chunk with higher similarity/rerank when the conflict is about the same claim (still mention the conflict).
- If the question needs data that is inherently not in static docs (e.g. live ticket status) and chunks do not contain it, say so and suggest what the agent should check operationally.

## Using diagnostics (calibration)
- If `returned_count` is 0 or chunks are empty: keep the answer short, admit lack of evidence, confidence "low", strong clarification in `follow_up_question`.
- If `best_similarity` and `best_rerank_score` are weak (relative to typical strong hits) or diagnostics suggest heavy filtering: prefer "medium" or "low" confidence and hedge language; ask a narrowing question if useful.
- Do not contradict the obvious implication of diagnostics (e.g. no chunks but claiming certainty).

## Answer style (for support agents)
- Clear, concise, actionable. Prefer short paragraphs or numbered steps when explaining procedures.
- When steps are requested or implied, use numbered steps.
- When useful, name evidence with `source_type` and `title` in prose (still cite in `citations`).

## Citations
- `citations` must list the chunks that directly support the main factual claims in `answer`. Prefer entries that match metadata in the chunk headers (`source_type`, `source_id`, `title`, `chunk_index`).
- Use chunk position index from headers (the "#n" marker) consistently with `chunk_index` when present in metadata.
- If you truly cannot tie claims to any chunk, keep `answer` non-factual or explicitly uncertain and minimize citations rather than fabricating support.

## Confidence
- "high": Multiple consistent chunks clearly answer the question; no major gaps.
- "medium": Partial answer, minor ambiguity, single thin source, or mild conflict resolved reasonably.
- "low": Missing/weak retrieval, important gaps, conflict unresolved, or heavy reliance on clarification.

## Output format (mandatory)
Return STRICT JSON only, with exactly these keys:
- `answer` (string)
- `confidence` ("high" | "medium" | "low")
- `citations` (array of objects with keys: `source_type`, `source_id`, `title`, `chunk_index` — use null where unknown)
- `follow_up_question` (string or null)

No markdown fences, no commentary outside the JSON object."""


@dataclass(frozen=True)
class Citation:
    source_type: str
    source_id: str | None
    title: str | None
    chunk_index: int | None


@dataclass(frozen=True)
class ChatResult:
    answer: str
    confidence: str
    citations: list[dict[str, Any]]
    follow_up_question: str | None
    matches: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    session_id: str
    message_id: str
    insufficient_context: bool


def _truncate_text(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _build_context_block(matches: list[dict[str, Any]], per_chunk_chars: int) -> str:
    if not matches:
        return "(no context retrieved)"
    blocks: list[str] = []
    for idx, row in enumerate(matches, start=1):
        metadata = row.get("metadata") or {}
        header_bits = [
            f"#{idx}",
            f"source_type={metadata.get('source_type') or 'unknown'}",
            f"source_id={metadata.get('source_id') or row.get('id') or ''}",
            f"chunk_index={metadata.get('chunk_index')}",
        ]
        title = metadata.get("title")
        if title:
            header_bits.append(f"title={title}")
        similarity = row.get("similarity")
        if isinstance(similarity, (int, float)):
            header_bits.append(f"similarity={float(similarity):.3f}")
        rerank = row.get("rerank_score")
        if isinstance(rerank, (int, float)):
            header_bits.append(f"rerank={float(rerank):.3f}")
        content = _truncate_text(row.get("content"), per_chunk_chars)
        blocks.append("[" + " | ".join(header_bits) + "]\n" + content)
    return "\n\n---\n\n".join(blocks)


def _citation_candidates(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for row in matches:
        metadata = row.get("metadata") or {}
        citations.append(
            {
                "source_type": metadata.get("source_type") or "unknown",
                "source_id": metadata.get("source_id") or row.get("id"),
                "title": metadata.get("title"),
                "chunk_index": metadata.get("chunk_index"),
            }
        )
    return citations


def _parse_completion_content(content: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"answer": str(content).strip(), "confidence": "low", "citations": [], "follow_up_question": None}
    if not isinstance(parsed, dict):
        return {"answer": str(parsed), "confidence": "low", "citations": [], "follow_up_question": None}
    return parsed


def _normalize_chat_output(
    parsed: dict[str, Any],
    *,
    matches: list[dict[str, Any]],
    insufficient_context: bool,
) -> tuple[str, str, list[dict[str, Any]], str | None]:
    answer = str(parsed.get("answer") or "").strip()
    confidence_raw = str(parsed.get("confidence") or "").strip().lower()
    confidence = confidence_raw if confidence_raw in {"high", "medium", "low"} else "low"

    raw_citations = parsed.get("citations")
    if isinstance(raw_citations, list) and raw_citations:
        citations = [c for c in raw_citations if isinstance(c, dict)]
    else:
        citations = _citation_candidates(matches)

    follow_up_question = parsed.get("follow_up_question")
    if isinstance(follow_up_question, str):
        follow_up_question = follow_up_question.strip() or None
    else:
        follow_up_question = None

    if insufficient_context and confidence == "high":
        confidence = "medium"

    return answer, confidence, citations, follow_up_question


def _build_user_prompt(query_text: str, *, context_block: str, diagnostics: dict[str, Any]) -> str:
    diagnostics_block = json.dumps(diagnostics, default=str)
    return (
        "User question:\n"
        f"{query_text.strip()}\n\n"
        "Retrieved context chunks (ordered):\n"
        f"{context_block}\n\n"
        "Diagnostics:\n"
        f"{diagnostics_block}\n\n"
        "Generate JSON only with keys: answer, confidence, citations, follow_up_question."
    )


def _build_chat_client(settings: Settings) -> "ChatGPTClient":
    return ChatGPTClient(
        api_key=settings.chat_api_key,
        model=settings.chat_model,
        base_url=settings.chat_base_url,
        timeout_s=settings.chat_timeout_s,
        max_retries=settings.chat_max_retries,
        retry_base_delay_s=settings.chat_retry_base_delay_s,
        temperature=settings.chat_temperature,
        max_output_tokens=settings.chat_max_output_tokens,
    )


def _fetch_history(
    history_store: "ChatHistoryStore",
    session_id: str | None,
    *,
    chat_history_turns: int | None,
    default_turns: int,
) -> list[dict[str, str]]:
    if not session_id:
        return []
    effective_limit = chat_history_turns if chat_history_turns is not None else default_turns
    return history_store.fetch_recent_turns(session_id, limit=effective_limit)


def _persist_chat_history(
    history_store: "ChatHistoryStore",
    *,
    session_id: str,
    query_text: str,
    answer: str,
    user_message_id: str,
    assistant_message_id: str,
    match_count: int,
    source_types: list[str] | None,
    confidence: str,
    citations: list[dict[str, Any]],
    follow_up_question: str | None,
    insufficient_context: bool,
    diagnostics: dict[str, Any],
) -> None:
    history_store.append(
        session_id=session_id,
        role="user",
        content=query_text,
        message_id=user_message_id,
        metadata={"match_count": match_count, "source_types": source_types},
    )
    history_store.append(
        session_id=session_id,
        role="assistant",
        content=answer,
        message_id=assistant_message_id,
        metadata={
            "confidence": confidence,
            "citations": citations,
            "follow_up_question": follow_up_question,
            "insufficient_context": insufficient_context,
            "diagnostics": diagnostics,
        },
    )


class ChatGPTClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_s: int,
        max_retries: int,
        retry_base_delay_s: float,
        temperature: float,
        max_output_tokens: int,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_CHAT_API_KEY (or OPENAI_API_KEY) is required for chat")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    def _build_messages(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            for turn in history:
                role = turn.get("role")
                content = turn.get("content")
                if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _retry_delay(self, *, attempt: int, retry_after_header: str | None = None) -> float:
        wait_s = self._retry_base_delay_s * (2**attempt)
        if retry_after_header:
            try:
                wait_s = max(wait_s, float(retry_after_header))
            except ValueError:
                pass
        return wait_s + random.uniform(0.0, 0.5)

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in {408, 409, 429, 500, 502, 503, 504}

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages = self._build_messages(system_prompt=system_prompt, user_prompt=user_prompt, history=history)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        attempt = 0
        with httpx.Client(timeout=self._timeout_s) as client:
            while True:
                try:
                    response = client.post(url, headers=headers, json=payload)
                except httpx.RequestError:
                    if attempt >= self._max_retries:
                        raise
                    time.sleep(self._retry_delay(attempt=attempt))
                    attempt += 1
                    continue
                if response.status_code < 400:
                    break
                if self._is_retryable_status(response.status_code) and attempt < self._max_retries:
                    time.sleep(self._retry_delay(attempt=attempt, retry_after_header=response.headers.get("Retry-After")))
                    attempt += 1
                    continue
                raise RuntimeError(
                    f"OpenAI chat completion failed ({response.status_code}): {response.text.strip()}"
                )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI chat completion returned no choices")
        content = (choices[0].get("message") or {}).get("content") or ""
        return _parse_completion_content(content)


class ChatHistoryStore:
    """Persists chat turns in public.chat_messages (separate from vector `documents`)."""

    def __init__(self, supabase_url: str, supabase_key: str, table_name: str = "chat_messages") -> None:
        self._client: Client = create_client(supabase_url, supabase_key)
        self._table_name = table_name

    def fetch_recent_turns(self, session_id: str, limit: int) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        try:
            response = (
                self._client.table(self._table_name)
                .select("role,content,created_at")
                .eq("session_id", session_id)
                .order("created_at", desc=False)
                .limit(max(limit * 2, 2))
                .execute()
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("chat_history fetch failed session_id=%s", session_id)
            return []
        rows = response.data or []
        turns: list[dict[str, str]] = []
        for row in rows[-limit * 2 :]:
            role = row.get("role")
            content = row.get("content")
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
            self._client.table(self._table_name).insert(payload).execute()
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "chat_history insert failed session_id=%s role=%s message_id=%s",
                session_id,
                role,
                message_id,
            )


def run_chat(
    settings: Settings,
    query_text: str,
    *,
    session_id: str | None = None,
    match_count: int = 5,
    match_threshold: float = 0.0,
    source_thresholds: dict[str, float] | None = None,
    source_types: list[str] | None = None,
    tenant: str | None = None,
    access_scope: str | None = None,
    updated_at_from: str | None = None,
    updated_at_to: str | None = None,
    strict_latest_within_top_n: bool | None = None,
    chat_history_turns: int | None = None,
    persist_history: bool = True,
) -> ChatResult:
    effective_session_id = session_id or str(uuid.uuid4())
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())

    retrieval = run_query(
        settings,
        query_text,
        match_count=match_count,
        match_threshold=match_threshold,
        source_thresholds=source_thresholds,
        source_types=source_types,
        tenant=tenant,
        access_scope=access_scope,
        updated_at_from=updated_at_from,
        updated_at_to=updated_at_to,
        strict_latest_within_top_n=strict_latest_within_top_n,
    )
    matches = retrieval.matches or []

    history_store = ChatHistoryStore(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
        table_name=settings.chat_history_table,
    )

    history = _fetch_history(
        history_store,
        session_id,
        chat_history_turns=chat_history_turns,
        default_turns=settings.chat_history_turns,
    )

    context_block = _build_context_block(matches, per_chunk_chars=settings.chat_context_chunk_max_chars)
    user_prompt = _build_user_prompt(
        query_text,
        context_block=context_block,
        diagnostics=retrieval.diagnostics,
    )
    chat_client = _build_chat_client(settings)

    parsed = chat_client.complete_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        history=history,
    )

    answer, confidence, citations, follow_up_question = _normalize_chat_output(
        parsed,
        matches=matches,
        insufficient_context=retrieval.insufficient_context,
    )

    if persist_history:
        _persist_chat_history(
            history_store,
            session_id=effective_session_id,
            query_text=query_text,
            answer=answer,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            match_count=match_count,
            source_types=source_types,
            confidence=confidence,
            citations=citations,
            follow_up_question=follow_up_question,
            insufficient_context=retrieval.insufficient_context,
            diagnostics=retrieval.diagnostics,
        )

    return ChatResult(
        answer=answer,
        confidence=confidence,
        citations=citations,
        follow_up_question=follow_up_question,
        matches=matches,
        diagnostics=retrieval.diagnostics,
        session_id=effective_session_id,
        message_id=assistant_message_id,
        insufficient_context=retrieval.insufficient_context,
    )
