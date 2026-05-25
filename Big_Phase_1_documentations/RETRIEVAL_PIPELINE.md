# Retrieval Pipeline

## Overview

Phase B1 implements a **hybrid retrieval pipeline** combining semantic vector search (pgvector) with keyword full-text search (PostgreSQL FTS), fused via Reciprocal Rank Fusion (RRF), followed by reranking.

```
Query Text
    │
    ├──▶ [Semantic] pgvector cosine similarity (HNSW index)
    │    Supabase RPC: match_all_b1_sources()
    │
    ├──▶ [Keyword] PostgreSQL full-text search (tsvector)
    │    Supabase RPC: match_all_b1_sources_fts()
    │
    └──▶ [Fusion] Reciprocal Rank Fusion (RRF)
              │
              └──▶ [Reranking] BM25 or CrossEncoder
                        │
                        └──▶ Final Top-K Results
```

## Semantic Search

### Embedding Model

- **Provider**: OpenAI `text-embedding-3-small`
- **Dimensions**: 1536
- **Index type**: HNSW (Hierarchical Navigable Small World) in pgvector
- **Distance metric**: Cosine similarity

### `match_all_b1_sources` RPC

The SQL RPC queries across multiple tables simultaneously:
- `documents` (legacy ticket chunks, fumadocs, JSON, Excel)
- `rag_sop_chunks` — +0.15 boosted similarity score applied here in SQL
- `rag_knowledge_chunks` (B3 knowledge base)

The SOP boost (`B1_SOP_SIMILARITY_BOOST = 0.15`) is applied in SQL, not Python. This means:
- SOP chunks with raw cosine 0.43 appear with `boosted_score = 0.58`
- Classification (`workflow_match_type`) uses the boosted score
- `WORKFLOW_EXACT_SIMILARITY = 0.55` is calibrated against boosted scores

### Token-Aware Chunking (Phase B1.5)

Before embedding, each chunk is validated for token length:
- Hard cap: `EMBEDDING_MAX_INPUT_TOKENS = 7000` (OpenAI limit is 8192; ~15% safety margin)
- Target: `CHUNK_TARGET_TOKENS = 1200` tokens (~900 words of English prose)
- Overlap: `CHUNK_OVERLAP_TOKENS = 150` tokens (prevents context loss at split boundaries)

Chunks exceeding the hard cap are truncated with a warning. This prevents `invalid_request_error` from the OpenAI embeddings API.

## Keyword Search (FTS)

### PostgreSQL Full-Text Search

Uses PostgreSQL's built-in `tsvector` type and `@@` operator for full-text search:
- `ts_rank_cd` for scoring
- GIN index for fast lookup
- Multi-language support via `to_tsvector('english', content)`

### Required Migrations

Before enabling keyword search:
```sql
-- Run in Supabase:
sql/b1_migrations/B1_007_fts_setup.sql  -- Creates tsvector columns and GIN indexes
sql/b1_migrations/B1_008_fts_rpc.sql   -- Creates match_all_b1_sources_fts() RPC
```

### Parameters

```
RETRIEVAL_KEYWORD_ENABLED=true
RETRIEVAL_KEYWORD_TOP_K=20
RETRIEVAL_KEYWORD_MIN_TS_RANK=0.0
```

## RRF Fusion

Reciprocal Rank Fusion merges ranked lists from semantic and keyword search:

```
RRF_score(chunk) = Σ 1 / (k + rank_in_list_i)
```

Where:
- `k = B1_RRF_K = 60` (smoothing factor — higher k reduces advantage of top-ranked items)
- Summed across all ranked lists (semantic + keyword)

**Why RRF?**
- Score-agnostic: works regardless of the scale or distribution of similarity scores
- Rank-based: penalizes items that appear low in either list
- Additive: rewards items that appear in BOTH lists (natural de-duplication)
- Simple: no learned parameters to tune

### Adaptive RRF (`RETRIEVAL_ADAPTIVE_RRF_ENABLED`)

When enabled, k is adjusted based on query complexity:
- Short/simple queries → lower k → more aggressive top-rank reward
- Complex/multi-part queries → higher k → more uniform distribution

## Reranking

### BM25 (Default)

BM25 (Best Match 25) is a bag-of-words retrieval function that scores chunks based on term frequency in the query context:
- Implemented in Python (no external model required)
- Fast: microseconds per chunk
- Query-dependent: reranked against the actual query terms

### CrossEncoder (Optional)

When `RERANK_CROSS_ENCODER_ENABLED=true`:
- Model: `cross-encoder/ms-marco-MiniLM-L-6-v2` (sentence-transformers)
- Jointly encodes the (query, chunk) pair — more accurate but slower
- Timeout: `RERANK_CROSS_ENCODER_TIMEOUT_S = 5.0` seconds
- Fallback: if model load or inference fails, BM25 is used automatically
- Thread-safe: model loaded once at startup, inference in a ThreadPoolExecutor

## Multi-Source Retrieval

The hybrid retriever queries multiple source types simultaneously:

| Source | Table | Boost |
|--------|-------|-------|
| SOPs | `rag_sop_chunks` | +0.15 cosine boost |
| Fumadocs (MD/MDX) | `documents` WHERE source_type='md' | None |
| JSON knowledge | `documents` WHERE source_type='json' | None |
| Excel data | `documents` WHERE source_type='excel' | None |
| Freshdesk tickets | `documents` WHERE source_type='freshdesk' | None |
| Knowledge articles | `rag_knowledge_chunks` | +`B3_KNOWLEDGE_SIMILARITY_BOOST` (0.08) |

Per-source thresholds (`QUERY_SOURCE_THRESHOLD_*`) filter out low-quality matches from each source before fusion.

## Three-Part Ticket Chunking

Historical Freshdesk tickets are ingested as three chunks per ticket:

```
Ticket
├── ISSUE_HEADER chunk    — subject, category, priority, created_at
├── QUERY_BODY chunk      — conversation thread (customer descriptions)
└── RESOLUTION_RCA chunk  — resolution, root cause, agent notes
```

This separation allows retrieval to match issues (ticket subject) or resolutions (what worked) independently, rather than diluting them in a single long document.

## Retrieval Confidence

After retrieval, a `retrieval_confidence` level is computed:

```python
if best_similarity >= 0.65 and len(high_quality_chunks) >= 2:
    retrieval_confidence = "high"
elif best_similarity >= 0.40:
    retrieval_confidence = "medium"
elif best_similarity >= 0.20:
    retrieval_confidence = "low"
else:
    retrieval_confidence = "none"
```

This feeds into the automation safety gate — `retrieval_confidence == "high"` is required for automation.

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `B1_HYBRID_RETRIEVAL_ENABLED` | `true` | Master switch for hybrid mode |
| `HYBRID_RETRIEVAL_ENABLED` | `true` | Legacy switch (must also be true) |
| `RETRIEVAL_SEMANTIC_TOP_K` | 20 | Semantic search candidate count |
| `RETRIEVAL_KEYWORD_TOP_K` | 20 | FTS candidate count |
| `RETRIEVAL_RRF_K` | 60 | RRF smoothing factor |
| `RETRIEVAL_RERANK_TOP_K` | 5 | Chunks passed to reranker |
| `RETRIEVAL_FINAL_TOP_K` | 5 | Final result count |
| `RETRIEVAL_MIN_SIMILARITY` | 0.20 | Post-rerank minimum score |
| `B1_DEFAULT_SIMILARITY_THRESHOLD` | 0.27 | /rag/chat default threshold |
| `B1_SOP_SIMILARITY_BOOST` | 0.15 | SOP score bonus (applied in SQL) |
