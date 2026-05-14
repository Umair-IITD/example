"""
rag_engine/generation/context_assembler.py

Converts RetrievedChunk objects from B1 retrieval into an LLM-ready context block.

Chunk type semantics:
  ISSUE_HEADER   — ticket subject, category, tenant metadata
  QUERY_BODY     — customer complaint / query text
  RESOLUTION_RCA — agent resolution (often sparse in dataset: avg 12 words)
  SOP chunks     — authoritative procedures from rag_sop_chunks, boosted +0.15
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag_engine.retrieval.ticket_retriever import RetrievedChunk

_CHUNK_TYPE_LABEL = {
    "ISSUE_HEADER": "Ticket Header",
    "QUERY_BODY": "Customer Query",
    "RESOLUTION_RCA": "Resolution / RCA",
}
_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class AssembledContext:
    context_block: str
    chunk_count: int
    sop_count: int
    has_resolution: bool


def assemble_context(
    chunks: list["RetrievedChunk"],
    per_chunk_max_chars: int = 1400,
) -> AssembledContext:
    if not chunks:
        return AssembledContext(
            context_block="(no context retrieved)",
            chunk_count=0,
            sop_count=0,
            has_resolution=False,
        )

    sop_chunks = [c for c in chunks if c.source_table == "rag_sop_chunks"]
    ticket_chunks = [c for c in chunks if c.source_table != "rag_sop_chunks"]

    blocks: list[str] = []
    idx = 1

    # SOPs first — highest authority, already boosted by +0.15 in retrieval
    for chunk in sop_chunks:
        content = _truncate(chunk.content, per_chunk_max_chars)
        header = (
            f"##{idx} [SOP | sop_id={chunk.sop_id or 'unknown'} | "
            f"score={chunk.boosted_score:.3f} | AUTHORITATIVE]"
        )
        blocks.append(f"{header}\n{content}")
        idx += 1

    for chunk in ticket_chunks:
        label = _CHUNK_TYPE_LABEL.get(chunk.chunk_type, chunk.chunk_type)
        rca_note = " | has_rca=True" if chunk.has_rca else ""
        content = _truncate(chunk.content, per_chunk_max_chars)
        header = (
            f"##{idx} [{label} | ticket_id={chunk.ticket_id or 'unknown'} | "
            f"chunk_type={chunk.chunk_type} | similarity={chunk.similarity:.3f}{rca_note}]"
        )
        blocks.append(f"{header}\n{content}")
        idx += 1

    has_resolution = any(
        c.chunk_type == "RESOLUTION_RCA" and c.has_rca for c in ticket_chunks
    )

    return AssembledContext(
        context_block=_SEPARATOR.join(blocks),
        chunk_count=len(chunks),
        sop_count=len(sop_chunks),
        has_resolution=has_resolution,
    )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."
