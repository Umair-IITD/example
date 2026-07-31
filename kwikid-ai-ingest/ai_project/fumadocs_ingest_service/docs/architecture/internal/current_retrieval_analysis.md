# Current Retrieval Architecture Analysis

**Date**: 2026-05-12  
**Status**: Baseline — Read-only analysis  

---

## 1. Retrieval Entrypoints

### Primary Entrypoint
```
POST /query  →  query_docs()  →  run_query()  [app/query.py]
POST /chat   →  chat_endpoint()  →  run_chat()  →  run_query()  [app/chat.py]
```

Both paths ultimately call `run_query()` in `app/query.py`. There is a single retrieval code path for all query types.

---

## 2. Flow Walkthrough — `run_query()` [app/query.py L135–248]

```
1. Embed query_text → EmbeddingClient.embed_texts([query_text])
2. Construct candidate_count = match_count * rerank_candidate_multiplier
3. VectorStore.match_documents(query_embedding, candidate_count, match_threshold, metadata_filters)
4. If rerank_enabled=True:
     - Compute rerank_score per candidate via _rerank_score()
     - Sort by (rerank_score, vector_score, recency_score)
5. Apply per-source-type similarity thresholds (_resolve_source_thresholds)
6. Group by source_type (_group_by_source)
7. Balanced round-robin selection (_balanced_top_matches)
8. Optional: strict recency sort
9. Assign final_rank indices
10. Compute diagnostics + insufficient_context flag
11. Return QueryResult dataclass
```

---

## 3. Vector Search — `VectorStore.match_documents()` [app/vector_store.py L182–226]

**Primary path**: Supabase RPC `match_documents`
```sql
-- Underlying SQL (match_documents_documents.sql)
SELECT id, content, metadata, 1 - (embedding <=> query_embedding) AS similarity
FROM documents
WHERE similarity >= match_threshold
ORDER BY embedding <=> query_embedding
LIMIT match_count;
```
- Uses **pgvector cosine distance** (`<=>` operator)
- HNSW index with `m=16, ef_construction=64`
- **Similarity = 1 - cosine_distance**

**Fallback path**: Python-side cosine similarity
- Triggered only when RPC returns `kb_chunks does not exist` error (legacy schema)
- Downloads up to `local_fallback_max_rows=5000` rows and computes in Python
- **Risk**: O(N) memory and CPU — should never be hit in production

**Metadata filtering**: Applied in Python post-RPC (not pushed to DB)
- source_types, tenant, access_scope, index_version, updated_at range

---

## 4. Reranking — `_rerank_score()` [app/query.py L29–35]

```python
def _rerank_score(query_text, candidate_text) -> float:
    q = set(WORD_RE.findall(query_text.lower()))   # simple tokenization
    c = set(WORD_RE.findall(candidate_text.lower()))
    overlap = len(q & c)
    return overlap / max(1, len(q))                 # query coverage ratio
```

**Type**: Simple unigram Jaccard-like coverage ratio  
**Strengths**: Fast, no dependencies, handles keyword overlap  
**Weaknesses**:
- No IDF weighting (treats "the" same as "PAN_MISMATCH")
- No phrase matching
- No acronym handling ("VKYC" vs "video kyc")
- No partial term matching ("panmismatch" vs "pan mismatch")
- Pure bag-of-words — no positional awareness

---

## 5. Sort Key — `_sort_key()` [app/query.py L45–50]

```python
(rerank_score, vector_score, recency_score)
```
Lexicographic tuple sort — rerank_score is dominant, then similarity, then timestamp.

**Issue**: recency can override relevance in ambiguous cases.

---

## 6. Threshold Resolution — `_resolve_source_thresholds()` [app/query.py L84–94]

Per-source-type thresholds from settings:
```
QUERY_SOURCE_THRESHOLD_FRESHDESK=0.30
QUERY_SOURCE_THRESHOLD_MD=0.20
QUERY_SOURCE_THRESHOLD_JSON=0.20
QUERY_SOURCE_THRESHOLD_EXCEL=0.20
```
- Freshdesk threshold is higher (noisier source, needs stronger match)
- Fallback threshold = `match_threshold` from request (default 0.0)

---

## 7. Balanced Source Selection — `_balanced_top_matches()` [app/query.py L108–132]

Round-robin across source types ordered by best-score:
- Picks 1 result per source type per pass
- Ensures diversity across md/json/freshdesk/excel/manual
- Can return fewer than `match_count` if sources are exhausted

**Issue**: A source with 1 mediocre result can "steal" a slot from a better result in another source.

---

## 8. Metadata Filtering — `VectorStore._apply_metadata_filters()` [app/vector_store.py L314–343]

Filters applied **in Python after DB fetch**:
- `source_types`: set membership check
- `tenant`: exact match
- `access_scope`: exact match
- `index_version`: exact match
- `updated_at`: datetime range

**Issue**: Filters are not pushed to DB — full candidate set is fetched then filtered in Python, wasting network bandwidth.

---

## 9. Diagnostics Captured

```python
{
  "candidate_count": int,
  "returned_count": int,
  "returned_count_by_source_type": {source_type: count},
  "filtered_out_by_source_type": {source_type: count},
  "applied_source_thresholds": {source_type: float},
  "best_similarity": float,
  "best_rerank_score": float,
  "strict_latest_within_top_n": bool,
}
```

**Missing from diagnostics**: embedding latency, rerank latency, which chunks were filtered by threshold vs. by count cap.

---

## 10. Identified Weaknesses

| # | Weakness | Impact |
|---|---|---|
| W1 | **Pure semantic retrieval** — no lexical/keyword search | Fails on exact error codes, IDs, acronyms (e.g., "ERR-4021", "VKYC", "PAN") |
| W2 | **Simple Jaccard reranking** — no IDF, no phrase matching | Poor discrimination between candidates |
| W3 | **No BM25 / full-text search** | Cannot leverage PostgreSQL GIN indexes for exact matches |
| W4 | **Metadata filters applied in Python** | Unnecessary data transfer from DB |
| W5 | **No retrieval-level telemetry** | Cannot measure Hit Rate, MRR, latency per-stage |
| W6 | **No fusion mechanism** | Cannot combine keyword and semantic signals |
| W7 | **Settings re-instantiated per request** | Minor overhead per call |
| W8 | **Candidate multiplier fixed** | No dynamic adjustment based on result quality |

---

## 11. Key Configuration [app/config.py]

| Setting | Default | Purpose |
|---|---|---|
| `RERANK_ENABLED` | true | Toggle reranking |
| `RERANK_CANDIDATE_MULTIPLIER` | 4 | Fetch 4x candidates for reranking |
| `CONFIDENCE_MIN_SIMILARITY` | 0.2 | Insufficient context threshold |
| `CONFIDENCE_MIN_RERANK` | 0.05 | Insufficient context threshold |
| `QUERY_SOURCE_THRESHOLD_*` | 0.20–0.30 | Per-source minimum similarity |
| `ACTIVE_INDEX_VERSION` | v1 | Which indexed documents to search |
| `LOCAL_MATCH_FALLBACK_MAX_ROWS` | 5000 | Safety cap for local fallback |
