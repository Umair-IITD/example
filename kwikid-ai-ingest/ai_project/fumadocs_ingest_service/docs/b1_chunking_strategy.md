# B1 Chunking Strategy

**Module:** `rag_engine/chunking/ticket_chunker.py`  
**Schema:** `rag_engine/schemas/chunk_schema.py`  
**Date:** 2026-05-14

---

## Overview

Each support ticket is converted into exactly **3 chunks** with distinct semantic roles. This is not a sliding-window strategy — it is a fixed, role-based decomposition where each chunk answers a different retrieval question.

---

## Three-Chunk Strategy

| Chunk Type | Content | Retrieval Purpose |
|------------|---------|-------------------|
| `ISSUE_HEADER` | Ticket metadata: ID, subject, client, query type, issue area, environment, priority, status | "What kind of problem is this?" |
| `QUERY_BODY` | Full customer question / issue description (HTML-cleaned, truncated to token limit) | "What did the customer report?" |
| `RESOLUTION_RCA` | Agent resolution and root-cause analysis | "How was this resolved?" |

All 3 chunks share the same `document_id` (UUID of the parent `RagTicketDocument`). They have separate embedding vectors and are retrieved independently by the retriever.

---

## Token Flow

```
Raw ticket (Excel/Freshdesk row)
    ↓
DatasetSchemaMapper → RagTicketDocument
    ↓
TicketChunker.chunk()
    ├── ISSUE_HEADER: short text (< 100 tokens typically)
    ├── QUERY_BODY: up to EMBEDDING_MAX_INPUT_TOKENS (default 7000)
    │       └── if > max → safe_split_by_tokens() → multiple QUERY_BODY chunks
    └── RESOLUTION_RCA: may be empty or short (avg 12.2 words in dataset)
    ↓
Each chunk → BatchEmbeddingProcessor._validate_chunk() → embed or skip
    ↓
Supabase upsert → rag_ticket_chunks
```

---

## Token Limits

| Setting | Default | Purpose |
|---------|---------|---------|
| `EMBEDDING_MAX_INPUT_TOKENS` | 7000 | Hard cap per chunk (OpenAI limit is 8192; 15% margin) |
| `CHUNK_TARGET_TOKENS` | 1200 | Target for word-based splitting of QUERY_BODY |
| `CHUNK_OVERLAP_TOKENS` | 150 | Overlap between adjacent QUERY_BODY sub-chunks |
| `B1_TICKET_MAX_CHUNK_WORDS` | 400 | Word-based fallback max (used when tiktoken unavailable) |
| `B1_TICKET_OVERLAP_WORDS` | 20 | Word-based fallback overlap |

---

## Splitting Algorithm

`safe_split_by_tokens()` in `rag_engine/utils/tokens.py`:

1. Count tokens with `tiktoken` (`cl100k_base` encoder)
2. If tiktoken unavailable, fall back to `len(text.split()) * 1.3` estimate
3. If total tokens ≤ max_tokens → return `[text]` (no split)
4. Otherwise: sliding window with `overlap_tokens` token overlap
5. Each window: grab words until token count exceeds target, back off by 1 word
6. Ensure no window exceeds `max_tokens`

**Pathological input fallback:** `recursive_split_if_oversized()` handles inputs that cannot be split by word boundaries (e.g., base64 blobs with no whitespace). Recursively halves by character position up to depth 8, then truncates.

---

## Chunk ID Determinism

Chunk IDs are **deterministic UUID5** values derived from:
```python
uuid.uuid5(
    uuid.NAMESPACE_OID,
    f"{ticket_id}:{chunk_index}:{index_version}"
)
```

Properties:
- Same ticket + same chunk_index + same `B1_INDEX_VERSION` → always same ID
- Changing `B1_INDEX_VERSION` (e.g., `v1` → `v2`) generates new IDs for all chunks → clean rebuild without touching old rows
- IDs are stable across runs → deduplication checker can skip already-embedded chunks

---

## Pre-Embed Validation

Before a chunk is sent to the embedding API, `_validate_chunk()` checks:

| Check | Threshold | Action if Fails |
|-------|-----------|----------------|
| Empty content | any | Skip + log |
| Whitespace only | after strip | Skip + log |
| Too short | < `B1_MIN_QUERY_BODY_CHARS` | Skip + log |
| Repetitive content | top word > 30% of words | Skip + log |
| Base64/hex blob | > 50% of long words (≥50 chars) match base64/hex regex | Skip + log |
| Unicode control chars | > 10% control chars (excl. \n \r \t) | Skip + log |
| Token overflow | > `EMBEDDING_MAX_INPUT_TOKENS` | Skip + log (should not happen after splitting) |

Skipped chunks are counted in metrics but do NOT cause the run to fail.

---

## Content Hash Deduplication

Each `RagTicketDocument` has a `content_hash` (SHA256 of normalized content). The `DeduplicationChecker`:

1. On first run: chunk is `NEW` → embed + upsert
2. If same `ticket_id` exists with same hash → `UNCHANGED` → skip
3. If same `ticket_id` exists with different hash → `UPDATED` → re-embed + upsert
4. This makes every run safe to re-run — idempotent

---

## Known Limitations

- **RESOLUTION_RCA quality:** Average 12.2 words per ticket in the dataset. Most tickets have no formal RCA. This chunk type has low retrieval utility until data quality improves.
- **QUERY_BODY truncation:** Very long tickets (rare) may lose tail content. The token limit is intentional — embedding APIs degrade on very long inputs.
- **No semantic chunking:** The 3-chunk strategy ignores section boundaries within the QUERY_BODY. Phase B1.5 plans section-aware chunking for SOP documents.
