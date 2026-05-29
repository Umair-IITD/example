"""Starter-question suggestions for the /chat empty state.

Primary source: `public.documents` rows authored via Teach-the-AI (`metadata.author`
== `chat_train`, chunk head `metadata.chunk_index` == 0), using
`metadata.suggested_questions`.

Fallback: distinct `metadata.title` from the most-recent `public.documents` rows,
rendered as "What can you tell me about <title>?" so new installations still show
something useful.
"""

from __future__ import annotations

import logging
from typing import Any

from supabase import Client, create_client

from app.config import Settings

LOGGER = logging.getLogger(__name__)

DOCUMENTS_FALLBACK_LIMIT = 40
MANUAL_CARD_AUTHOR = "chat_train"


def _get_client(settings: Settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_key)


def _collect_from_train_documents(
    client: Client,
    *,
    tenant: str | None,
    access_scope: str | None,
    limit: int,
) -> list[str]:
    try:
        query = (
            client.table("documents")
            .select("metadata,created_at")
            .contains("metadata", {"author": MANUAL_CARD_AUTHOR, "chunk_index": 0})
            .order("created_at", desc=True)
            .limit(max(limit * 4, 20))
        )
        response = query.execute()
    except Exception:  # noqa: BLE001
        LOGGER.exception("suggestions: failed to read train card documents")
        return []

    collected: list[str] = []
    for row in response.data or []:
        metadata = row.get("metadata") or {}
        if tenant and metadata.get("tenant") and metadata.get("tenant") != tenant:
            continue
        if access_scope and metadata.get("access_scope") and metadata.get("access_scope") != access_scope:
            continue
        suggested = metadata.get("suggested_questions") or []
        if not isinstance(suggested, list):
            continue
        for item in suggested:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                collected.append(text)
    return collected


def _collect_from_documents(
    client: Client,
    *,
    tenant: str | None,
    access_scope: str | None,
    limit: int,
) -> list[str]:
    try:
        response = (
            client.table("documents")
            .select("metadata,created_at")
            .order("created_at", desc=True)
            .limit(DOCUMENTS_FALLBACK_LIMIT)
            .execute()
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("suggestions: failed to read documents fallback")
        return []

    collected: list[str] = []
    for row in response.data or []:
        metadata = row.get("metadata") or {}
        if tenant and metadata.get("tenant") and metadata.get("tenant") != tenant:
            continue
        if access_scope and metadata.get("access_scope") and metadata.get("access_scope") != access_scope:
            continue
        title = metadata.get("title")
        if not isinstance(title, str):
            continue
        text = title.strip()
        if not text:
            continue
        collected.append(f"What can you tell me about {text}?")
        if len(collected) >= limit * 2:
            break
    return collected


def _dedupe_case_insensitive(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
        if len(out) >= limit:
            break
    return out


def get_suggestions(
    settings: Settings,
    *,
    tenant: str | None = None,
    access_scope: str | None = None,
    limit: int = 6,
    supabase_client: Any = None,
) -> list[str]:
    if limit <= 0:
        return []
    client = supabase_client if supabase_client is not None else _get_client(settings)

    primary = _collect_from_train_documents(
        client,
        tenant=tenant,
        access_scope=access_scope,
        limit=limit,
    )
    result = _dedupe_case_insensitive(primary, limit)
    if len(result) >= limit:
        return result

    fallback = _collect_from_documents(
        client,
        tenant=tenant,
        access_scope=access_scope,
        limit=limit - len(result),
    )
    combined = _dedupe_case_insensitive(result + fallback, limit)
    return combined
