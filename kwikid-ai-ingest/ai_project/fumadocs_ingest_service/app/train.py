"""Teach-the-AI / chat-to-train knowledge capture.

Users converse with the LLM to author a reusable "knowledge card". On commit,
the card is chunked, embedded, and upserted into `public.documents` with
source_type='manual' so it joins the rest of the retrieval corpus. Card-level
metadata (summary, suggested_questions, session_id, chunk count, etc.) is stored
on each chunk's JSON `metadata`; chunk_index == 0 rows are the canonical "card
head" for listing and detail APIs. The legacy `public.knowledge_cards` table is
no longer written by this service.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from app.chat import ChatGPTClient, ChatHistoryStore
from app.chunker import Chunk, SourceDocument, chunk_document
from app.config import Settings
from app.embeddings import EmbeddingClient
from app.vector_store import VectorStore

LOGGER = logging.getLogger(__name__)

MANUAL_CARD_AUTHOR = "chat_train"
MANUAL_REPO_LABEL = "manual:chat_train"
MANUAL_COMMIT_SHA = "manual"
MIN_CONTENT_WORDS = 40
WORD_PATTERN = re.compile(r"\S+")


TRAIN_SYSTEM_PROMPT = (
    "You are a knowledge-capture assistant for the KwikID product.\n"
    "Your job is to help a subject-matter expert teach you reusable knowledge\n"
    "(policies, SOPs, product rules, ticket resolutions, pricing logic, etc.)\n"
    "so it can be stored in a retrieval-augmented knowledge base.\n\n"
    "Rules:\n"
    "1) Never invent facts. Only capture what the user explicitly states.\n"
    "2) Keep the \"content\" field canonical, self-contained, and free of chat\n"
    "   pleasantries. Write it like an internal help-center article.\n"
    "3) Progressively refine a single \"draft\" across turns. Keep prior fields\n"
    "   unless the user explicitly changes them.\n"
    "4) On every turn, ask EXACTLY ONE focused follow-up question that fills the\n"
    "   most important missing slot in this priority order:\n"
    "   title -> tenant -> access_scope -> tags -> validity/scope caveats ->\n"
    "   source links -> examples.\n"
    "5) Produce 3-6 \"suggested_questions\" an end-user might ask that this\n"
    "   content would answer. Phrase them in natural language.\n"
    "6) Set confidence = \"high\" only when title, content, tags, tenant, and\n"
    "   access_scope are all populated and the content is >= 40 words.\n"
    "7) Respond with ONLY a JSON object matching this exact schema:\n"
    "   {\n"
    "     \"assistant_reply\": string,\n"
    "     \"draft\": {\n"
    "       \"title\": string|null,\n"
    "       \"summary\": string|null,\n"
    "       \"content\": string,\n"
    "       \"tags\": string[],\n"
    "       \"tenant\": string|null,\n"
    "       \"access_scope\": string|null,\n"
    "       \"suggested_questions\": string[]\n"
    "     },\n"
    "     \"follow_up_question\": string|null,\n"
    "     \"confidence\": \"high\"|\"medium\"|\"low\"\n"
    "   }\n"
    "   No prose outside JSON."
)


@dataclass
class DraftKnowledgeCard:
    title: str | None = None
    summary: str | None = None
    content: str = ""
    tags: list[str] = field(default_factory=list)
    tenant: str | None = None
    access_scope: str | None = None
    suggested_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "tags": list(self.tags),
            "tenant": self.tenant,
            "access_scope": self.access_scope,
            "suggested_questions": list(self.suggested_questions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DraftKnowledgeCard":
        if not isinstance(data, dict):
            return cls()
        return cls(
            title=_coerce_str_or_none(data.get("title")),
            summary=_coerce_str_or_none(data.get("summary")),
            content=_coerce_str(data.get("content")),
            tags=_coerce_str_list(data.get("tags")),
            tenant=_coerce_str_or_none(data.get("tenant")),
            access_scope=_coerce_str_or_none(data.get("access_scope")),
            suggested_questions=_coerce_str_list(data.get("suggested_questions")),
        )


@dataclass(frozen=True)
class TrainChatResult:
    session_id: str
    message_id: str
    assistant_reply: str
    draft: DraftKnowledgeCard
    follow_up_question: str | None
    confidence: str
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class CommitResult:
    card_id: str
    chunk_ids: list[str]
    chunks_created: int
    chunks_updated: int
    chunks_skipped: int


@dataclass(frozen=True)
class KnowledgeCardSummary:
    id: str
    session_id: str
    title: str | None
    summary: str | None
    tags: list[str]
    tenant: str | None
    access_scope: str | None
    status: str
    suggested_questions: list[str]
    chunk_ids: list[str]
    content: str | None
    created_at: str | None
    updated_at: str | None


def _coerce_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _coerce_str_or_none(value: Any) -> str | None:
    text = _coerce_str(value)
    return text or None


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _word_count(text: str) -> int:
    if not text:
        return 0
    return len(WORD_PATTERN.findall(text))


def _merge_drafts(prior: DraftKnowledgeCard, update: DraftKnowledgeCard) -> DraftKnowledgeCard:
    """Cumulative merge: new non-null / non-empty values overwrite prior ones; otherwise keep prior."""
    return DraftKnowledgeCard(
        title=update.title or prior.title,
        summary=update.summary or prior.summary,
        content=update.content.strip() or prior.content,
        tags=update.tags if update.tags else prior.tags,
        tenant=update.tenant or prior.tenant,
        access_scope=update.access_scope or prior.access_scope,
        suggested_questions=update.suggested_questions if update.suggested_questions else prior.suggested_questions,
    )


def _parse_llm_json(raw: dict[str, Any]) -> tuple[str, DraftKnowledgeCard, str | None, str]:
    assistant_reply = _coerce_str(raw.get("assistant_reply"))
    if not assistant_reply:
        # When the LLM ignored the schema, fall back to the answer/content fields.
        assistant_reply = _coerce_str(raw.get("answer")) or "(no reply)"
    draft = DraftKnowledgeCard.from_dict(raw.get("draft"))
    follow_up = _coerce_str_or_none(raw.get("follow_up_question"))
    confidence_raw = _coerce_str(raw.get("confidence")).lower()
    confidence = confidence_raw if confidence_raw in {"high", "medium", "low"} else "low"
    return assistant_reply, draft, follow_up, confidence


def _build_train_user_prompt(
    *,
    user_message: str,
    previous_draft: DraftKnowledgeCard,
) -> str:
    draft_json = json.dumps(previous_draft.to_dict(), ensure_ascii=False, indent=2)
    return (
        "Previous draft (JSON):\n"
        f"{draft_json}\n\n"
        "User said:\n"
        f"{user_message.strip()}\n\n"
        "Update the draft with any new knowledge the user provided. Preserve\n"
        "prior values when the user did not change them. Return the required\n"
        "JSON object only."
    )


def _train_supabase_client(settings: Settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_key)


def _card_head_metadata_filter() -> dict[str, Any]:
    """Subset match for the representative documents row per train card (chunk 0)."""
    return {"author": MANUAL_CARD_AUTHOR, "chunk_index": 0}


def _deterministic_chunk_id(*, repo: str, source_type: str, source_id: str, chunk_index: int) -> str:
    # Mirrors the id computation in VectorStore.upsert_chunks so we can delete later.
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{repo}:{source_type}:{source_id}:{chunk_index}",
        )
    )


def _chunk_ids_for_card(card_id: str, chunk_count: int) -> list[str]:
    return [
        _deterministic_chunk_id(
            repo=MANUAL_REPO_LABEL,
            source_type="manual",
            source_id=card_id,
            chunk_index=idx,
        )
        for idx in range(chunk_count)
    ]


def _document_row_to_summary(
    row: dict[str, Any],
    *,
    chunk_ids: list[str],
) -> KnowledgeCardSummary:
    md = row.get("metadata") or {}
    card_id = str(md.get("card_id") or "")
    session_raw = md.get("session_id")
    title_val = md.get("title")
    title = title_val.strip() if isinstance(title_val, str) and title_val.strip() else None
    summary_val = md.get("card_summary")
    summary = summary_val if isinstance(summary_val, str) and summary_val.strip() else None
    tags_raw = md.get("tags")
    if isinstance(tags_raw, list):
        tags = [t for t in tags_raw if isinstance(t, str)]
    else:
        tags = []
    sq_raw = md.get("suggested_questions") or []
    suggested: list[str] = []
    if isinstance(sq_raw, list):
        suggested = [q for q in sq_raw if isinstance(q, str) and q.strip()]

    return KnowledgeCardSummary(
        id=card_id or str(row.get("id")),
        session_id=str(session_raw or ""),
        title=title,
        summary=summary,
        tags=tags,
        tenant=md.get("tenant") if isinstance(md.get("tenant"), str) else None,
        access_scope=md.get("access_scope") if isinstance(md.get("access_scope"), str) else None,
        status="committed",
        suggested_questions=suggested,
        chunk_ids=chunk_ids,
        content=md.get("manual_card_full_content")
        if isinstance(md.get("manual_card_full_content"), str)
        else None,
        created_at=row.get("created_at"),
        updated_at=md.get("updated_at") if isinstance(md.get("updated_at"), str) else row.get("created_at"),
    )


def delete_document_chunks_for_card(settings: Settings, card_id: str) -> int:
    """Delete `public.documents` rows whose metadata.card_id == card_id."""
    client = _train_supabase_client(settings)
    response = client.table(settings.supabase_table).select("id").eq("metadata->>card_id", str(card_id)).execute()
    deleted = 0
    for row in response.data or []:
        rid = row.get("id")
        if rid is None:
            continue
        client.table(settings.supabase_table).delete().eq("id", rid).execute()
        deleted += 1
    return deleted


def fetch_card_head_document(settings: Settings, card_id: str) -> dict[str, Any] | None:
    client = _train_supabase_client(settings)
    response = (
        client.table(settings.supabase_table)
        .select("*")
        .eq("metadata->>card_id", str(card_id))
        .contains("metadata", _card_head_metadata_filter())
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if rows:
        return rows[0]
    # Legacy rows without chunk_index on head: any chunk with this card_id.
    response2 = (
        client.table(settings.supabase_table)
        .select("*")
        .eq("metadata->>card_id", str(card_id))
        .eq("metadata->>author", MANUAL_CARD_AUTHOR)
        .limit(1)
        .execute()
    )
    rows2 = response2.data or []
    return rows2[0] if rows2 else None


def list_train_card_heads_from_documents(
    settings: Settings,
    *,
    limit: int,
    offset: int,
    tenant: str | None = None,
    access_scope: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
) -> tuple[list[KnowledgeCardSummary], int]:
    if status == "draft":
        return [], 0
    client = _train_supabase_client(settings)
    query = (
        client.table(settings.supabase_table)
        .select("*", count="exact")
        .contains("metadata", _card_head_metadata_filter())
    )
    if tenant:
        query = query.eq("metadata->>tenant", tenant)
    if access_scope:
        query = query.eq("metadata->>access_scope", access_scope)
    if session_id:
        query = query.eq("metadata->>session_id", session_id)
    if status == "archived":
        return [], 0

    response = query.order("created_at", desc=True).range(offset, offset + max(limit - 1, 0)).execute()
    rows = response.data or []
    total = int(getattr(response, "count", None) or len(rows))

    summaries: list[KnowledgeCardSummary] = []
    for row in rows:
        md = row.get("metadata") or {}
        cid = str(md.get("card_id") or "")
        if not cid:
            continue
        chunk_count = 1
        raw_count = md.get("manual_chunk_count")
        if isinstance(raw_count, int) and raw_count >= 1:
            chunk_count = raw_count
        elif isinstance(raw_count, float):
            chunk_count = max(1, int(raw_count))
        chunk_ids = _chunk_ids_for_card(cid, chunk_count)
        summaries.append(_document_row_to_summary(row, chunk_ids=chunk_ids))
    return summaries, total


def _build_chat_client(settings: Settings) -> ChatGPTClient:
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


def run_train_chat(
    settings: Settings,
    *,
    query_text: str,
    session_id: str | None = None,
    previous_draft: DraftKnowledgeCard | None = None,
    tenant: str | None = None,
    access_scope: str | None = None,
    history_turns: int | None = None,
    persist_history: bool = True,
) -> TrainChatResult:
    if not query_text or not query_text.strip():
        raise ValueError("query_text must be non-empty")

    effective_session_id = session_id or str(uuid.uuid4())
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())

    prior_draft = previous_draft or DraftKnowledgeCard()
    # Seed tenant/access_scope from request if the draft is missing them.
    if not prior_draft.tenant and tenant:
        prior_draft = DraftKnowledgeCard(
            title=prior_draft.title,
            summary=prior_draft.summary,
            content=prior_draft.content,
            tags=prior_draft.tags,
            tenant=tenant,
            access_scope=prior_draft.access_scope,
            suggested_questions=prior_draft.suggested_questions,
        )
    if not prior_draft.access_scope and access_scope:
        prior_draft = DraftKnowledgeCard(
            title=prior_draft.title,
            summary=prior_draft.summary,
            content=prior_draft.content,
            tags=prior_draft.tags,
            tenant=prior_draft.tenant,
            access_scope=access_scope,
            suggested_questions=prior_draft.suggested_questions,
        )

    history_store = ChatHistoryStore(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
        table_name=settings.chat_history_table,
    )

    history: list[dict[str, str]] = []
    if session_id:
        history = history_store.fetch_recent_turns(
            session_id,
            limit=history_turns if history_turns is not None else settings.chat_history_turns,
        )

    user_prompt = _build_train_user_prompt(user_message=query_text, previous_draft=prior_draft)

    # RuntimeError bubbles up to the endpoint layer for 502 mapping.
    raw = _build_chat_client(settings).complete_json(
        system_prompt=TRAIN_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        history=history,
    )

    assistant_reply, update_draft, follow_up, confidence = _parse_llm_json(raw)
    merged = _merge_drafts(prior_draft, update_draft)

    # Confidence guardrail: never promote to 'high' unless structural prereqs met.
    if confidence == "high" and (
        not merged.title
        or not merged.tenant
        or not merged.access_scope
        or not merged.tags
        or _word_count(merged.content) < MIN_CONTENT_WORDS
    ):
        confidence = "medium"

    diagnostics = {
        "word_count": _word_count(merged.content),
        "has_title": bool(merged.title),
        "has_tenant": bool(merged.tenant),
        "has_access_scope": bool(merged.access_scope),
        "tag_count": len(merged.tags),
        "suggested_question_count": len(merged.suggested_questions),
    }

    if persist_history:
        history_store.append(
            session_id=effective_session_id,
            role="user",
            content=query_text,
            message_id=user_message_id,
            metadata={"mode": "training"},
        )
        history_store.append(
            session_id=effective_session_id,
            role="assistant",
            content=assistant_reply,
            message_id=assistant_message_id,
            metadata={
                "mode": "training",
                "confidence": confidence,
                "follow_up_question": follow_up,
                "diagnostics": diagnostics,
                "draft": merged.to_dict(),
            },
        )

    return TrainChatResult(
        session_id=effective_session_id,
        message_id=assistant_message_id,
        assistant_reply=assistant_reply,
        draft=merged,
        follow_up_question=follow_up,
        confidence=confidence,
        diagnostics=diagnostics,
    )


def commit_knowledge_card(
    settings: Settings,
    *,
    session_id: str,
    draft: DraftKnowledgeCard,
) -> CommitResult:
    if not session_id or not session_id.strip():
        raise ValueError("session_id is required")
    if not draft.title or not draft.title.strip():
        raise ValueError("draft.title is required to commit a knowledge card")
    content = draft.content.strip() if draft.content else ""
    if _word_count(content) < MIN_CONTENT_WORDS:
        raise ValueError(
            f"draft.content must have at least {MIN_CONTENT_WORDS} words before committing"
        )

    # TODO(pii): Redact obvious PII (emails, phone numbers) from committed content
    # once a shared helper exists. Not blocking initial rollout.

    card_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    doc_metadata: dict[str, Any] = {
        "tenant": draft.tenant,
        "access_scope": draft.access_scope,
        "author": MANUAL_CARD_AUTHOR,
        "captured_at": now_iso,
        "session_id": session_id,
        "card_id": card_id,
        "updated_at": now_iso,
    }
    source_document = SourceDocument(
        source_type="manual",
        source_id=card_id,
        content=content,
        title=draft.title.strip(),
        heading=None,
        tags=list(draft.tags),
        creation_date=now_iso,
        metadata=doc_metadata,
    )

    chunks = chunk_document(
        source_document,
        min_words=settings.min_chunk_words,
        target_words=settings.target_chunk_words,
        max_words=settings.max_chunk_words,
        overlap_words=settings.chunk_overlap_words,
        strategy="paragraph",
        max_chars=settings.chunk_max_chars,
        min_orphan_words=settings.chunk_min_orphan_words,
    )
    if not chunks:
        raise ValueError("chunker produced zero chunks; content too short or empty after normalization")

    enriched_chunks: list[Chunk] = []
    for chunk in chunks:
        meta = dict(chunk.metadata)
        if chunk.chunk_index == 0:
            meta.update(
                {
                    "card_summary": draft.summary,
                    "suggested_questions": list(draft.suggested_questions),
                    "manual_card_full_content": content,
                    "manual_chunk_count": len(chunks),
                }
            )
        enriched_chunks.append(replace(chunk, metadata=meta))

    embeddings = EmbeddingClient(
        provider=settings.embedding_provider,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        timeout_s=settings.embedding_timeout_s,
        max_retries=settings.embedding_max_retries,
        retry_base_delay_s=settings.embedding_retry_base_delay_s,
    )
    vectors = embeddings.embed_texts([c.content for c in enriched_chunks])
    if len(vectors) != len(enriched_chunks):
        raise RuntimeError("embedding client returned a vector count mismatched with chunk count")

    store = VectorStore(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
        table_name=settings.supabase_table,
        local_fallback_max_rows=settings.local_match_fallback_max_rows,
    )
    try:
        result = store.upsert_chunks(
            repo=MANUAL_REPO_LABEL,
            commit_sha=MANUAL_COMMIT_SHA,
            index_version=settings.write_index_version,
            chunk_vectors=zip(enriched_chunks, vectors, strict=False),
        )
    except Exception:
        delete_document_chunks_for_card(settings, card_id)
        raise

    chunk_ids = [
        _deterministic_chunk_id(
            repo=MANUAL_REPO_LABEL,
            source_type="manual",
            source_id=card_id,
            chunk_index=chunk.chunk_index,
        )
        for chunk in enriched_chunks
    ]

    LOGGER.info(
        "train_commit card_id=%s session_id=%s chunks_created=%s chunks_updated=%s chunks_skipped=%s",
        card_id,
        session_id,
        result.created,
        result.updated,
        result.skipped,
    )

    return CommitResult(
        card_id=card_id,
        chunk_ids=chunk_ids,
        chunks_created=result.created,
        chunks_updated=result.updated,
        chunks_skipped=result.skipped,
    )


def list_knowledge_cards(
    settings: Settings,
    *,
    limit: int = 20,
    offset: int = 0,
    tenant: str | None = None,
    access_scope: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
) -> tuple[list[KnowledgeCardSummary], int]:
    return list_train_card_heads_from_documents(
        settings,
        limit=limit,
        offset=offset,
        tenant=tenant,
        access_scope=access_scope,
        status=status,
        session_id=session_id,
    )


def get_knowledge_card(settings: Settings, card_id: str) -> KnowledgeCardSummary | None:
    row = fetch_card_head_document(settings, card_id)
    if not row:
        return None
    md = row.get("metadata") or {}
    cid = str(md.get("card_id") or card_id)
    chunk_count = 1
    raw_count = md.get("manual_chunk_count")
    if isinstance(raw_count, int) and raw_count >= 1:
        chunk_count = raw_count
    elif isinstance(raw_count, float):
        chunk_count = max(1, int(raw_count))
    chunk_ids = _chunk_ids_for_card(cid, chunk_count)
    return _document_row_to_summary(row, chunk_ids=chunk_ids)


def delete_knowledge_card(settings: Settings, card_id: str) -> dict[str, Any]:
    deleted = delete_document_chunks_for_card(settings, card_id)
    if deleted == 0:
        return {"card_id": card_id, "deleted_chunks": 0, "status": "missing"}
    LOGGER.info("train_delete card_id=%s deleted_chunks=%s", card_id, deleted)
    return {"card_id": card_id, "deleted_chunks": deleted, "status": "archived"}
