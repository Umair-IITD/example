# Hybrid Retrieval Architecture

**Version**: v1  
**Date**: 2026-05-12  

---

## Overview

The hybrid retrieval pipeline combines **semantic vector search** (pgvector) and **lexical keyword search** (PostgreSQL FTS) using **Reciprocal Rank Fusion (RRF)**. The result is a more robust retrieval system that handles both semantic similarity and exact technical term matching.

```
User Query
    │
    ├─► [Embedding]           OpenAI/Ollama → query_vector
    │
    ├─► [Semantic Search]     pgvector cosine similarity → top-N semantic candidates
    │
    ├─► [Keyword Search]      PostgreSQL FTS (ts_vector + GIN) → top-N keyword candidates
    │
    ├─► [RRF Fusion]          Combine ranks → fused candidate set
    │
    ├─► [Reranking]           Lexical overlap scoring → reordered top-K
    │
    └─► [HybridResult]        Backward-compatible matches list + diagnostics + trace
```

---

## Module Map

| Module | Responsibility |
|---|---|
| `retrieval/hybrid_search.py` | Pipeline orchestrator — coordinates all stages |
| `retrieval/semantic_search.py` | pgvector cosine similarity via Supabase RPC |
| `retrieval/keyword_search.py` | PostgreSQL FTS + graceful ilike fallback |
| `retrieval/fusion.py` | Reciprocal Rank Fusion combining both result sets |
| `retrieval/reranker.py` | Lexical reranker + BaseReranker interface |
| `retrieval/filters.py` | Centralized metadata filter utilities |
| `retrieval/models.py` | Dataclass schemas for all pipeline stages |
| `retrieval/config.py` | Environment-overridable configuration |
| `retrieval/metrics.py` | Hit Rate, Recall@K, MRR, latency stats |

---

## Stage 1: Semantic Search

**File**: `retrieval/semantic_search.py`

Calls the existing `match_documents` Supabase RPC:
```sql
SELECT id, content, metadata, 1 - (embedding <=> query_embedding) AS similarity
FROM documents
WHERE similarity >= threshold
ORDER BY embedding <=> query_embedding
LIMIT semantic_top_k;
```

- Uses HNSW index (`m=16, ef_construction=64`)
- Returns `SemanticCandidate` objects with `similarity` and `semantic_rank`
- Applies metadata filters post-fetch (same as existing path)

**Configuration**: `RETRIEVAL_SEMANTIC_TOP_K` (default: 20)

---

## Stage 2: Keyword Search (BM25 / Full-Text)

**File**: `retrieval/keyword_search.py`

Uses PostgreSQL's built-in full-text search via PostgREST:
```sql
-- Index setup (one-time, run sql/fts_setup.sql)
ALTER TABLE documents
  ADD COLUMN fts tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;

CREATE INDEX documents_fts_gin_idx ON documents USING gin(fts);
```

**Query building**: Tokenizes query → builds tsquery with prefix matching:
```
"PAN mismatch during VKYC" → "PAN:* | mismatch:* | during:* | VKYC:*"
```

**Why this helps**:
- `VKYC`, `PAN`, `OCR`, `ERR-4021` are exact tokens — semantic search may embed them poorly
- Prefix matching (`PAN:*`) catches plurals and compound forms
- GIN index makes this O(log N) rather than a full scan

**Fallback**: If the `fts` column is not set up, gracefully falls back to `ilike` substring search.

**Configuration**: `RETRIEVAL_KEYWORD_TOP_K` (default: 20), `RETRIEVAL_KEYWORD_ENABLED` (default: true)

---

## Stage 3: Reciprocal Rank Fusion

**File**: `retrieval/fusion.py`

**Formula**:
```
rrf_score(doc) = Σᵢ  1 / (k + rankᵢ)
```

- `k = 60` (default): smoothing constant preventing top-ranked docs from dominating
- A document in both result sets gets contributions from both ranks
- A document in only one set gets only one contribution (the other is 0)

**Example output**:
```json
{
  "doc_id": "abc-123",
  "semantic_rank": 2,
  "keyword_rank": 5,
  "semantic_score": 0.87,
  "keyword_score": 0.62,
  "rrf_score": 0.0307
}
```

**Why RRF**:
- Scale-invariant: semantic scores (0–1) and ts_rank scores don't need normalization
- Robust to outlier scores in either retrieval system
- Proven effective in TREC evaluations

**Configuration**: `RETRIEVAL_RRF_K` (default: 60), `RETRIEVAL_FUSION_TOP_K` (default: 20)

---

## Stage 4: Reranking

**File**: `retrieval/reranker.py`

**Current implementation**: `LexicalReranker` — mirrors existing `_rerank_score()` in `app/query.py`:
```python
score = |query_tokens ∩ candidate_tokens| / max(1, |query_tokens|)
```

**Future extension**: `CrossEncoderReranker` scaffold exists. Swap in by updating `get_reranker()`.

**Interface**:
```python
class BaseReranker:
    def score(query: str, candidate_text: str) -> float: ...
    def rerank(query, candidates, top_k) -> (candidates, latency_ms): ...
```

---

## Stage 5: Output Format

All results are converted to the **existing row dict format** via `fused_to_row()`:
```python
{
    "id": "...",
    "content": "...",
    "metadata": {...},
    "similarity": 0.87,        # backward-compat
    "vector_score": 0.87,      # backward-compat
    "rerank_score": 0.6,       # backward-compat
    "rrf_score": 0.031,        # new: hybrid transparency
    "semantic_rank": 2,        # new: debug info
    "keyword_rank": 5,         # new: debug info
    "final_rank": 1,           # existing field
}
```

Existing `QueryResult` consumers (`chat.py`, `train.py`, n8n) see no changes.

---

## Retrieval Tracing

When `DEBUG_TRACE=true`, each hybrid retrieval call writes a JSON file to `/traces/`:

```json
{
  "query": "PAN mismatch during VKYC",
  "query_hash": "a3f1b2c4...",
  "latencies_ms": {
    "embedding": 45.2,
    "semantic": 120.3,
    "keyword": 85.1,
    "fusion": 2.1,
    "rerank": 1.8,
    "total": 254.5
  },
  "semantic_candidates_count": 20,
  "keyword_candidates_count": 15,
  "fused_candidates_count": 28,
  "final_count": 5,
  "semantic_candidates": [...],
  "keyword_candidates": [...],
  "fused_candidates": [...],
  "final_candidates": [...]
}
```

---

## Configuration Reference

| Env Variable | Default | Description |
|---|---|---|
| `HYBRID_RETRIEVAL_ENABLED` | `false` | Master switch — off by default |
| `RETRIEVAL_SEMANTIC_TOP_K` | `20` | Candidates from vector search |
| `RETRIEVAL_KEYWORD_TOP_K` | `20` | Candidates from keyword search |
| `RETRIEVAL_KEYWORD_ENABLED` | `true` | Toggle keyword search leg |
| `RETRIEVAL_RRF_K` | `60` | RRF smoothing constant |
| `RETRIEVAL_FUSION_TOP_K` | `20` | Max candidates after fusion |
| `RETRIEVAL_RERANK_ENABLED` | `true` | Toggle reranking step |
| `RETRIEVAL_RERANK_TOP_K` | `5` | Candidates after reranking |
| `RETRIEVAL_FINAL_TOP_K` | `5` | Final returned results |
| `RETRIEVAL_MIN_SIMILARITY` | `0.2` | Insufficient context threshold |

---

## Feature Flag Activation

```bash
# .env — activate hybrid mode
HYBRID_RETRIEVAL_ENABLED=true

# Optional tuning
RETRIEVAL_RRF_K=60
RETRIEVAL_KEYWORD_ENABLED=true
RETRIEVAL_SEMANTIC_TOP_K=20
```

When `HYBRID_RETRIEVAL_ENABLED=false` (default), the existing `run_query()` path executes unchanged.

When `HYBRID_RETRIEVAL_ENABLED=true` and the hybrid pipeline raises an exception, it logs a warning and **automatically falls back** to the existing semantic-only path.

---

## DB Prerequisites

Run **once** in the Supabase SQL editor before enabling keyword search:
```sql
-- See sql/fts_setup.sql for the full script
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS fts tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;

CREATE INDEX IF NOT EXISTS documents_fts_gin_idx
  ON documents USING gin(fts);
```

---

## Future Extensibility

| Capability | Path |
|---|---|
| **Cross-encoder reranking** | Implement `CrossEncoderReranker.score()` in `reranker.py` |
| **Query classification** | Integrate `query_router/classifier.py` before Stage 1 |
| **Per-category thresholds** | Extend `RetrievalConfig` using `query_router/thresholds.py` |
| **Evaluation** | Populate `evaluation/gold_dataset.json`, run `evaluate_retrieval.py` |
| **LangGraph orchestration** | `run_hybrid_search()` is a clean callable node — composable directly |
