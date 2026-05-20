"""
rag_engine/feedback/review_queue.py

Phase B3: Human review queue manager.

The review queue captures AI-generated drafts that require human approval before
potentially being ingested into the knowledge base as VERIFIED_REPLY articles.

Flow:
  1. Freshdesk webhook generates a draft via B2 pipeline
  2. Agent reviews the draft (APPROVED / EDITED / REJECTED)
  3. Approved/edited drafts are enqueued via ReviewQueueManager.enqueue()
  4. ReviewQueueManager.process_reviewed() ingests APPROVED/EDITED items
     into rag_knowledge_chunks via KnowledgePipeline.ingest_verified_reply()

Security invariants:
  - REJECTED responses are NEVER ingested as knowledge
  - Only final_response (human-provided or human-approved) enters the knowledge base
  - AI draft alone NEVER becomes a VERIFIED_REPLY without human gate

Asana integration:
  - Optional: only active when ASANA_API_KEY + ASANA_PROJECT_ID are configured
  - Graceful degradation if Asana is not configured (queues work without it)
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

# ── Valid review states ────────────────────────────────────────────────────────
REVIEW_STATUS_PENDING   = "PENDING"
REVIEW_STATUS_IN_REVIEW = "IN_REVIEW"
REVIEW_STATUS_APPROVED  = "APPROVED"
REVIEW_STATUS_REJECTED  = "REJECTED"

# ── Valid human actions ────────────────────────────────────────────────────────
HUMAN_ACTION_APPROVED  = "APPROVED"
HUMAN_ACTION_EDITED    = "EDITED"
HUMAN_ACTION_REJECTED  = "REJECTED"
HUMAN_ACTION_ESCALATED = "ESCALATED"

# Actions that may produce knowledge (both still require human gate)
_KNOWLEDGE_ELIGIBLE_ACTIONS = frozenset({HUMAN_ACTION_APPROVED, HUMAN_ACTION_EDITED})


class ReviewQueueManager:
    """
    Manages the rag_review_queue table and Asana task integration.

    Usage:
        manager = ReviewQueueManager(supabase_client)
        queue_id = manager.enqueue(client="cbi", ticket_id="12345", ...)
        manager.process_reviewed(queue_id, human_action="APPROVED", final_response="...")
    """

    def __init__(
        self,
        supabase_client: Any,
        review_queue_table: str = "rag_review_queue",
        knowledge_pipeline: Optional[Any] = None,   # KnowledgePipeline, injected at runtime
    ) -> None:
        self._client  = supabase_client
        self._table   = review_queue_table
        self._pipeline = knowledge_pipeline
        self._asana   = _AsanaClient.from_env()

    # ── Public API ─────────────────────────────────────────────────────────────

    def enqueue(
        self,
        *,
        client: str,
        ticket_id: str,
        query_text: str,
        ai_draft: str,
        feedback_log_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Add a draft to the human review queue.

        Returns the new queue item ID, or None if the insert fails.
        Never raises.
        """
        row_id = str(uuid.uuid4())
        row = {
            "id":              row_id,
            "feedback_log_id": feedback_log_id,
            "client":          client,
            "ticket_id":       ticket_id,
            "query_text":      query_text[:4000],
            "ai_draft":        ai_draft[:8000],
            "review_status":   REVIEW_STATUS_PENDING,
            "created_at":      datetime.now(timezone.utc).isoformat(),
            "ingested_as_knowledge": False,
        }

        try:
            self._client.table(self._table).insert(row).execute()
            LOGGER.info(
                "ReviewQueue: enqueued id=%s client=%s ticket_id=%s",
                row_id, client, ticket_id,
            )

            # Optionally create Asana task for the reviewer
            if self._asana.is_configured:
                task_id, task_url = self._asana.create_task(
                    title   = f"[Review] {client} ticket {ticket_id}",
                    body    = f"Query: {query_text[:500]}\n\nAI Draft:\n{ai_draft[:1000]}",
                    queue_id= row_id,
                )
                if task_id:
                    self._client.table(self._table).update(
                        {"asana_task_id": task_id, "asana_task_url": task_url}
                    ).eq("id", row_id).execute()

            return row_id

        except Exception as exc:  # noqa: BLE001
            LOGGER.error("ReviewQueue: enqueue failed for ticket=%s: %s", ticket_id, exc)
            return None

    def process_reviewed(
        self,
        queue_id: str,
        *,
        human_action: str,
        final_response: str,
        reviewer_id: Optional[str] = None,
    ) -> bool:
        """
        Record the human review decision and optionally ingest as knowledge.

        Args:
            queue_id:       UUID of the review queue item
            human_action:   APPROVED | EDITED | REJECTED
            final_response: The human-approved/edited response text
            reviewer_id:    Optional identifier of the reviewing agent

        Returns:
            True if processed successfully, False on error.

        Security:
            REJECTED responses are NOT ingested. The pipeline check is explicit.
        """
        if human_action not in {HUMAN_ACTION_APPROVED, HUMAN_ACTION_EDITED, HUMAN_ACTION_REJECTED}:
            LOGGER.warning(
                "ReviewQueue: unknown human_action=%r for queue_id=%s",
                human_action, queue_id,
            )
            return False

        try:
            # Fetch the queue item to get context for knowledge ingestion
            resp = (
                self._client.table(self._table)
                .select("id, client, ticket_id, query_text, ai_draft, review_status")
                .eq("id", queue_id)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            if not rows:
                LOGGER.warning("ReviewQueue: queue_id=%s not found", queue_id)
                return False

            item = rows[0]

            # Determine final review status
            review_status = REVIEW_STATUS_APPROVED if human_action in _KNOWLEDGE_ELIGIBLE_ACTIONS else REVIEW_STATUS_REJECTED

            # Update queue item
            update = {
                "review_status":  review_status,
                "final_response": final_response[:8000] if final_response else None,
                "reviewer_id":    reviewer_id,
                "reviewed_at":    datetime.now(timezone.utc).isoformat(),
            }
            self._client.table(self._table).update(update).eq("id", queue_id).execute()

            LOGGER.info(
                "ReviewQueue: processed id=%s action=%s client=%s",
                queue_id, human_action, item.get("client"),
            )

            # Ingest as VERIFIED_REPLY if eligible and pipeline is available
            if (
                human_action in _KNOWLEDGE_ELIGIBLE_ACTIONS
                and final_response
                and self._pipeline is not None
            ):
                self._ingest_as_knowledge(item, final_response, queue_id)

            return True

        except Exception as exc:  # noqa: BLE001
            LOGGER.error("ReviewQueue: process_reviewed failed for queue_id=%s: %s", queue_id, exc)
            return False

    def get_pending(self, client: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Return pending review queue items (optionally filtered by client)."""
        try:
            query = (
                self._client.table(self._table)
                .select("id, client, ticket_id, query_text, ai_draft, created_at, asana_task_url")
                .eq("review_status", REVIEW_STATUS_PENDING)
                .order("created_at", desc=False)
                .limit(limit)
            )
            if client:
                query = query.eq("client", client)
            resp = query.execute()
            return resp.data or []
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("ReviewQueue: get_pending failed: %s", exc)
            return []

    # ── Private ────────────────────────────────────────────────────────────────

    def _ingest_as_knowledge(
        self,
        item: dict,
        final_response: str,
        queue_id: str,
    ) -> None:
        """Ingest a reviewed response as a VERIFIED_REPLY knowledge article."""
        client    = item.get("client", "")
        title     = f"Support Q&A: {item.get('ticket_id', 'unknown')}"
        query_text= item.get("query_text", "")

        if not client or not query_text or not final_response:
            LOGGER.warning(
                "ReviewQueue: skipping knowledge ingestion — missing fields for queue_id=%s",
                queue_id,
            )
            return

        try:
            result = self._pipeline.ingest_verified_reply(
                title           = title,
                query_text      = query_text,
                final_response  = final_response,
                client          = client,
                source_ticket_id= item.get("ticket_id"),
            )
            if result.articles_inserted:
                self._client.table(self._table).update(
                    {"ingested_as_knowledge": True}
                ).eq("id", queue_id).execute()
                LOGGER.info(
                    "ReviewQueue: ingested as VERIFIED_REPLY for client=%s queue_id=%s",
                    client, queue_id,
                )
            else:
                LOGGER.info(
                    "ReviewQueue: knowledge ingestion returned 0 inserts (may be duplicate) queue_id=%s",
                    queue_id,
                )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "ReviewQueue: knowledge ingestion failed for queue_id=%s: %s",
                queue_id, exc,
            )


# ── Asana integration (optional, graceful degradation) ────────────────────────

class _AsanaClient:
    """
    Lightweight Asana task creator.

    Requires environment variables:
        ASANA_API_KEY      — personal access token
        ASANA_PROJECT_ID   — GID of the review project
        ASANA_WORKSPACE_ID — GID of the workspace (optional if project is enough)

    If any of these are missing, is_configured = False and all calls are no-ops.
    """

    def __init__(
        self,
        api_key: str,
        project_id: str,
    ) -> None:
        self._api_key    = api_key
        self._project_id = project_id

    @classmethod
    def from_env(cls) -> "_AsanaClient":
        api_key    = os.getenv("ASANA_API_KEY", "").strip()
        project_id = os.getenv("ASANA_PROJECT_ID", "").strip()
        return cls(api_key=api_key, project_id=project_id)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._project_id)

    def create_task(
        self,
        *,
        title: str,
        body: str,
        queue_id: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Create an Asana task for human review.

        Returns (task_gid, task_url) or (None, None) on failure.
        Fails gracefully — never raises.
        """
        if not self.is_configured:
            return None, None

        try:
            import urllib.request
            import json as _json

            payload = {
                "data": {
                    "name":      title[:255],
                    "notes":     body[:2000] + f"\n\nQueue ID: {queue_id}",
                    "projects":  [self._project_id],
                }
            }
            data = _json.dumps(payload).encode("utf-8")
            req  = urllib.request.Request(
                "https://app.asana.com/api/1.0/tasks",
                data    = data,
                headers = {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type":  "application/json",
                    "Accept":        "application/json",
                },
                method  = "POST",
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                result  = _json.loads(response.read().decode("utf-8"))
                task    = result.get("data", {})
                task_id = task.get("gid")
                task_url = f"https://app.asana.com/0/{self._project_id}/{task_id}" if task_id else None
                LOGGER.info("Asana task created: gid=%s for queue_id=%s", task_id, queue_id)
                return task_id, task_url
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Asana task creation failed for queue_id=%s: %s", queue_id, exc)
            return None, None
