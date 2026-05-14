"""
rag_engine/schemas/chunk_schema.py

TicketChunk — the unit of ingestion into rag_ticket_chunks.
Carries all metadata that must survive independently for retrieval.
"""
from __future__ import annotations

import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from rag_engine.schemas.ticket_document import AutomationLabel, ChunkType


def _deterministic_chunk_id(ticket_id: str, chunk_index: int, index_version: str) -> str:
    """
    UUID5-based deterministic ID.
    Same ticket + same chunk_index + same index_version → same UUID always.
    This makes Supabase upsert idempotent: re-running ingestion never creates
    duplicate rows, even without pre-checking existing hashes.
    """
    namespace = uuid.NAMESPACE_URL
    name = f"freshdesk:{ticket_id}:{chunk_index}:{index_version}"
    return str(uuid.uuid5(namespace, name))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(text.split())


@dataclass
class TicketChunk:
    """
    A single embedded chunk derived from a RagTicketDocument.
    Maps 1:1 to a row in rag_ticket_chunks.
    """
    # Identity (deterministic)
    id: str                             # UUID5 deterministic chunk ID

    # Parent document link
    document_id: Optional[str]          # UUID of rag_ticket_documents row (set after doc upsert)
    ticket_id: str

    # Chunk position
    chunk_index: int
    chunk_type: ChunkType               # ISSUE_HEADER | QUERY_BODY | RESOLUTION_RCA
    chunk_total: int                    # Total chunks for this ticket

    # Content
    content: str
    word_count: int
    content_hash: str

    # Embedding (None until embedding pipeline runs)
    embedding: Optional[list[float]] = field(default=None)

    # Retrieval metadata (all indexed in DB)
    client: str = ""
    source_type: str = "freshdesk"
    automation_label: AutomationLabel = AutomationLabel.HUMAN_REVIEW
    escalation_flag: bool = False
    query_type: Optional[str] = None
    issue_area: Optional[str] = None
    environment: Optional[str] = None
    has_rca: bool = False
    has_sop: bool = False
    rca_quality_score: int = 0
    ticket_created_at: Optional[datetime] = None

    # Ingestion tracking
    ingestion_run_id: Optional[str] = None
    index_version: str = "v1"

    # Low-cardinality signals stored as JSONB
    extra_metadata: dict = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        ticket_id: str,
        chunk_index: int,
        chunk_type: ChunkType,
        chunk_total: int,
        content: str,
        client: str,
        automation_label: AutomationLabel,
        index_version: str = "v1",
        **kwargs,
    ) -> "TicketChunk":
        """Factory that computes deterministic ID, hash, and word count."""
        chunk_id = _deterministic_chunk_id(ticket_id, chunk_index, index_version)
        content_hash = _sha256(content)
        wc = _word_count(content)
        return cls(
            id=chunk_id,
            ticket_id=ticket_id,
            chunk_index=chunk_index,
            chunk_type=chunk_type,
            chunk_total=chunk_total,
            content=content,
            word_count=wc,
            content_hash=content_hash,
            client=client,
            automation_label=automation_label,
            index_version=index_version,
            document_id=None,
            **kwargs,
        )

    def to_db_row(self) -> dict:
        """Serialize to the exact shape expected by Supabase upsert."""
        return {
            "id": self.id,
            "document_id": self.document_id,
            "ticket_id": self.ticket_id,
            "source_type": self.source_type,
            "client": self.client,
            "chunk_index": self.chunk_index,
            "chunk_type": self.chunk_type.value,
            "chunk_total": self.chunk_total,
            "content": self.content,
            "word_count": self.word_count,
            "content_hash": self.content_hash,
            "embedding": self.embedding,
            "automation_label": self.automation_label.value,
            "escalation_flag": self.escalation_flag,
            "query_type": self.query_type,
            "issue_area": self.issue_area,
            "environment": self.environment,
            "has_rca": self.has_rca,
            "has_sop": self.has_sop,
            "rca_quality_score": self.rca_quality_score,
            "extra_metadata": self.extra_metadata,
            "ticket_created_at": (
                self.ticket_created_at.isoformat() if self.ticket_created_at else None
            ),
            "ingestion_run_id": self.ingestion_run_id,
            "index_version": self.index_version,
        }
