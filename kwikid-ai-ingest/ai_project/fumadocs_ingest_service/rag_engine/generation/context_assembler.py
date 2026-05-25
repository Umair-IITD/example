"""
rag_engine/generation/context_assembler.py

Converts RetrievedChunk objects from B1/B3 retrieval into an LLM-ready context block,
with tiktoken-based token budgeting to prevent context overflow.

Chunk type semantics:
  ISSUE_HEADER   — ticket subject, category, tenant metadata
  QUERY_BODY     — customer complaint / query text
  RESOLUTION_RCA — agent resolution (often sparse in dataset: avg 12 words)
  SOP chunks     — authoritative procedures from rag_sop_chunks, boosted +0.15

Phase B3 additions:
  VERIFIED_REPLY / TROUBLESHOOTING / FAQ_ANSWER / POLICY / RCA — knowledge chunks
  These are placed AFTER SOPs but BEFORE ticket chunks in the context block.

Evidence tier order (highest → lowest authority):
  1. SOP chunks              (source_table=rag_sop_chunks)
  2. VERIFIED_REPLY knowledge (source_table=rag_knowledge_chunks, chunk_type=VERIFIED_REPLY)
  3. Other knowledge chunks   (source_table=rag_knowledge_chunks, other types)
  4. RESOLUTION_RCA tickets   (source_table=rag_ticket_chunks, chunk_type=RESOLUTION_RCA)
  5. Other ticket chunks       (source_table=rag_ticket_chunks, other types)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tiktoken

from rag_engine.sop.sop_parser import parse_sop_content

if TYPE_CHECKING:
    from rag_engine.retrieval.ticket_retriever import RetrievedChunk

LOGGER = logging.getLogger(__name__)

_MAX_CONTEXT_TOKENS = 6_000   # leave headroom for system prompt + output within 16k window

_CHUNK_TYPE_LABEL = {
    "ISSUE_HEADER": "Ticket Header",
    "QUERY_BODY": "Customer Query",
    "RESOLUTION_RCA": "Resolution / RCA",
}
_SEPARATOR = "\n\n---\n\n"

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text))


@dataclass(frozen=True)
class AssembledContext:
    context_block: str
    chunk_count: int
    sop_count: int
    has_resolution: bool
    total_tokens: int = 0         # token count of the assembled context_block
    skipped_chunks: int = 0       # chunks dropped due to token budget exhaustion


def assemble_context(
    chunks: list["RetrievedChunk"],
    per_chunk_max_chars: int = 1400,
    max_context_tokens: int = _MAX_CONTEXT_TOKENS,
) -> AssembledContext:
    if not chunks:
        return AssembledContext(
            context_block="(no context retrieved)",
            chunk_count=0,
            sop_count=0,
            has_resolution=False,
            total_tokens=0,
            skipped_chunks=0,
        )

    # ── Phase B3: separate into four tiers ───────────────────────────────────
    sop_chunks = [c for c in chunks if c.source_table == "rag_sop_chunks"]

    knowledge_verified = [
        c for c in chunks
        if c.source_table == "rag_knowledge_chunks"
        and c.chunk_type in {"VERIFIED_REPLY", "TROUBLESHOOTING"}
    ]
    knowledge_other = [
        c for c in chunks
        if c.source_table == "rag_knowledge_chunks"
        and c.chunk_type not in {"VERIFIED_REPLY", "TROUBLESHOOTING"}
    ]

    ticket_rca = [
        c for c in chunks
        if c.source_table == "rag_ticket_chunks"
        and c.chunk_type == "RESOLUTION_RCA"
    ]
    ticket_other = [
        c for c in chunks
        if c.source_table == "rag_ticket_chunks"
        and c.chunk_type != "RESOLUTION_RCA"
    ]

    # Ordered by authority: SOP → VERIFIED knowledge → other knowledge → RCA → other tickets
    ordered_chunks = sop_chunks + knowledge_verified + knowledge_other + ticket_rca + ticket_other

    candidate_blocks: list[str] = []
    idx = 1

    for chunk in ordered_chunks:
        content = _truncate(chunk.content, per_chunk_max_chars)

        if chunk.source_table == "rag_sop_chunks":
            flags = parse_sop_content(chunk.content)
            branch_tags: list[str] = []
            if flags.has_escalation_branches:
                branch_tags.append("escalation:YES")
            if flags.has_denial_branches:
                branch_tags.append("denial:YES")
            if flags.has_security_freeze:
                branch_tags.append("freeze:YES")
            if flags.has_post_resolution:
                branch_tags.append("post-res:YES")
            if flags.has_mandatory_warnings:
                branch_tags.append("mandatory:YES")
            branch_str = (" | " + " | ".join(branch_tags)) if branch_tags else ""
            header = (
                f"##{idx} [SOP | sop_id={chunk.sop_id or 'unknown'} | "
                f"score={chunk.boosted_score:.3f} | AUTHORITATIVE{branch_str}]"
            )
        elif chunk.source_table == "rag_knowledge_chunks":
            klass = chunk.knowledge_class or chunk.chunk_type
            qs = f" | quality={chunk.quality_score:.2f}" if chunk.quality_score is not None else ""
            header = (
                f"##{idx} [INSTITUTIONAL KNOWLEDGE | {klass} | "
                f"score={chunk.boosted_score:.3f}{qs}]"
            )
        else:
            # Ticket chunks (B1 behavior preserved exactly)
            label = _CHUNK_TYPE_LABEL.get(chunk.chunk_type, chunk.chunk_type)
            rca_note = " | has_rca=True" if chunk.has_rca else ""
            header = (
                f"##{idx} [{label} | ticket_id={chunk.ticket_id or 'unknown'} | "
                f"chunk_type={chunk.chunk_type} | similarity={chunk.similarity:.3f}{rca_note}]"
            )

        candidate_blocks.append(f"{header}\n{content}")
        idx += 1

    # Token budget enforcement — SOPs (listed first) are always preserved under budget pressure
    budget = max_context_tokens
    final_blocks: list[str] = []
    skipped = 0
    for block in candidate_blocks:
        tok = _count_tokens(block)
        if tok > budget:
            skipped += 1
            LOGGER.debug("context_assembler: skipped block (tok=%d, budget=%d)", tok, budget)
        else:
            budget -= tok
            final_blocks.append(block)

    if skipped:
        LOGGER.warning(
            "context_assembler: %d chunk(s) dropped — token budget exhausted (max=%d)",
            skipped,
            max_context_tokens,
        )

    # Compute metadata (ticket chunks only for has_resolution — preserves B1 semantics)
    all_ticket_chunks = ticket_rca + ticket_other
    has_resolution = any(
        c.chunk_type == "RESOLUTION_RCA" and c.has_rca for c in all_ticket_chunks
    )

    context_text = _SEPARATOR.join(final_blocks)
    total_tokens = _count_tokens(context_text) if final_blocks else 0

    return AssembledContext(
        context_block=context_text,
        chunk_count=len(chunks),
        sop_count=len(sop_chunks),
        has_resolution=has_resolution,
        total_tokens=total_tokens,
        skipped_chunks=skipped,
    )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."
