"""
rag_engine/feedback/feedback_loop.py

Continuous learning feedback loop.

Architecture:
  1. Agent acts on AI draft (APPROVED / EDITED / REJECTED / ESCALATED)
  2. FeedbackIngester records signal in rag_feedback_logs
  3. Nightly scheduler queries rag_feedback_logs WHERE reingestion_eligible = TRUE
  4. Eligible tickets are re-ingested via delta ingestion (IngestionPipeline.run(mode=delta))
  5. For EDITED responses: the edited text becomes the new resolution text in the KB

Eligibility rules for re-ingestion:
  - APPROVED with edit_ratio < 0.1    → high quality confirmation; boost implicitly
  - EDITED with edit_ratio < 0.5      → update resolution text with corrected version
  - REJECTED                          → flag for manual review; do not auto-reingest
  - ESCALATED                         → update automation_label → ESCALATION; exclude from auto
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)


@dataclass
class FeedbackSignal:
    """Represents a single agent feedback event."""
    freshdesk_ticket_id: str
    client: str
    query_text: str
    query_type: Optional[str]
    automation_label: str           # AUTO_REPLY | HUMAN_REVIEW
    ai_response: str
    human_action: str               # APPROVED | EDITED | REJECTED | ESCALATED
    edited_response: Optional[str] = None
    rejection_reason: Optional[str] = None
    quality_score: Optional[float] = None
    agent_id: Optional[str] = None
    ai_model: Optional[str] = "gpt-4o-mini"
    generation_latency_ms: Optional[float] = None
    retrieved_chunks: Optional[list[dict]] = None

    @property
    def edit_distance(self) -> int:
        """Approximate edit distance using len difference (full Levenshtein is expensive)."""
        if not self.edited_response or self.human_action != "EDITED":
            return 0
        return abs(len(self.ai_response) - len(self.edited_response))

    @property
    def edit_ratio(self) -> float:
        if not self.edited_response or len(self.ai_response) == 0:
            return 0.0
        return self.edit_distance / len(self.ai_response)

    @property
    def was_useful(self) -> bool:
        if self.human_action == "APPROVED":
            return True
        if self.human_action == "EDITED" and self.edit_ratio < 0.25:
            return True
        return False

    @property
    def reingestion_eligible(self) -> bool:
        """Determine if this feedback should trigger re-ingestion."""
        if self.human_action in ("APPROVED", "ESCALATED"):
            return True
        if self.human_action == "EDITED" and self.edited_response:
            return True
        return False


class FeedbackIngester:
    """
    Records feedback signals and queues eligible tickets for re-ingestion.
    """

    def __init__(
        self,
        supabase_client: Any,
        feedback_logs_table: str = "rag_feedback_logs",
    ) -> None:
        self._client = supabase_client
        self._feedback_table = feedback_logs_table

    def record(self, signal: FeedbackSignal) -> Optional[str]:
        """
        Record a feedback signal to rag_feedback_logs.
        Returns the new log row ID, or None if write fails.
        """
        row = {
            "freshdesk_ticket_id": signal.freshdesk_ticket_id,
            "client": signal.client,
            "query_text": signal.query_text[:2000],
            "query_type": signal.query_type,
            "automation_label": signal.automation_label,
            "ai_response": signal.ai_response[:5000],
            "ai_model": signal.ai_model,
            "generation_latency_ms": signal.generation_latency_ms,
            "human_action": signal.human_action,
            "edited_response": (signal.edited_response or "")[:5000] or None,
            "rejection_reason": signal.rejection_reason,
            "quality_score": signal.quality_score,
            "agent_id": signal.agent_id,
            "retrieved_chunks": signal.retrieved_chunks or [],
            "retrieval_count": len(signal.retrieved_chunks or []),
            "top_similarity_score": (
                max((c.get("similarity", 0) for c in (signal.retrieved_chunks or [])), default=0.0)
            ),
            "had_sop_context": any(
                c.get("chunk_type") == "SOP_STEPS" for c in (signal.retrieved_chunks or [])
            ),
            "edit_distance": signal.edit_distance,
            "edit_ratio": round(signal.edit_ratio, 4),
            "was_useful": signal.was_useful,
            "feedback_at": datetime.now(timezone.utc).isoformat(),
            "reingestion_eligible": signal.reingestion_eligible,
            "reingestion_done": False,
        }
        try:
            response = self._client.table(self._feedback_table).insert(row).execute()
            rows = response.data or []
            if rows:
                log_id = str(rows[0].get("id", ""))
                LOGGER.info(
                    "Feedback recorded: ticket=%s client=%s action=%s eligible=%s id=%s",
                    signal.freshdesk_ticket_id, signal.client,
                    signal.human_action, signal.reingestion_eligible, log_id
                )
                return log_id
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to record feedback signal: %s", exc)
        return None

    def get_reingestion_queue(
        self,
        client: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Fetch feedback records that are eligible for re-ingestion but not yet done.
        Used by the nightly scheduler.
        """
        try:
            query = (
                self._client.table(self._feedback_table)
                .select("id, freshdesk_ticket_id, client, human_action, edited_response, feedback_at")
                .eq("reingestion_eligible", True)
                .eq("reingestion_done", False)
                .order("feedback_at", desc=False)
                .limit(limit)
            )
            if client:
                query = query.eq("client", client)
            response = query.execute()
            return response.data or []
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to fetch reingestion queue: %s", exc)
            return []

    def mark_reingested(self, log_id: str, run_id: str) -> None:
        """Mark a feedback record as re-ingested after the pipeline completes."""
        try:
            self._client.table(self._feedback_table).update({
                "reingestion_done": True,
                "reingestion_run_id": run_id,
                "re_ingested_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", log_id).execute()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to mark feedback %s as reingested: %s", log_id, exc)
