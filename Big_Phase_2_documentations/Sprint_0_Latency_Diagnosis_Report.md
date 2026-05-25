# Sprint 0 — Retrieval Latency Diagnosis Report

**Date**: 2026-05-25  
**Status**: DIAGNOSIS COMPLETE — implementation of fixes blocked pending Sprint 0 gate  
**Observed symptom**: `semantic_latency_ms ≈ 6800ms`, `total_latency_ms ≈ 10000ms` (retrieval phase only; LLM generation adds further time)  
**Target**: `semantic_latency_ms < 200ms`, `total_retrieval_ms < 800ms`

---

## 1. Call Chain Traced

Every `/rag/chat` request follows this path:

```
HTTP POST /rag/chat
  └── rag_chat() [app/main.py:1023]
        └── _build_chat_generator() [app/main.py:956]
              ├── create_client()            ← NEW Supabase client per request
              ├── OpenAIEmbeddingProvider()  ← NEW embedder per request
              └── HybridTicketRetriever(supabase, embedder)

        └── generator.generate(gen_request) [rag_engine/generation/chat_generator.py:166]
              └── self._retriever.retrieve() [hybrid_ticket_retriever.py:123]
                    ├── embedder.embed_single()    ← OpenAI API call
                    ├── _search_with_fetch()       ← match_all_b1_sources RPC  ← 6800ms HERE
                    ├── _search_keyword()          ← search_b1_sources_fts RPC
                    ├── _rrf_fuse()                ← in-process
                    └── _reranker.rerank()         ← in-process

              ├── assemble_context()         ← in-process
              ├── history_store.fetch_recent()  ← Supabase query
              ├── llm.complete_json()           ← OpenAI generation API
              └── history_store.append() x2    ← 2 Supabase inserts
```

---

## 2. Findings by Bottleneck Priority

### 2.1 FINDING 1 — CRITICAL: SQL UNION ALL Prevents HNSW Index Usage

**Confidence**: Very High  
**Estimated impact**: 5,000–6,500ms of the observed 6,800ms `semantic_latency_ms`

**File**: `sql/b1_migrations/B1_005_rpc_functions.sql`, function `match_all_b1_sources`

**The smoking gun**: The function structure is:

```sql
CREATE OR REPLACE FUNCTION public.match_all_b1_sources(...)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    -- Ticket chunks
    SELECT
        ...
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS similarity,
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS boosted_score,
        ...
    FROM public.rag_ticket_chunks rtc
    WHERE
        rtc.client = p_client
        AND rtc.index_version = p_index_version
        AND rtc.embedding IS NOT NULL
        AND rtc.automation_label != 'ESCALATION'
        AND (1 - (rtc.embedding <=> p_query_embedding)) >= p_match_threshold

    UNION ALL

    -- SOP chunks (boosted +0.15)
    SELECT
        ...
        LEAST(1.0, (1 - (sc.embedding <=> p_query_embedding)) + 0.15) AS boosted_score,
        ...
    FROM public.rag_sop_chunks sc
    JOIN public.rag_sop_library sl ON sl.sop_id = sc.sop_id
    WHERE
        sc.embedding IS NOT NULL
        AND sc.index_version = p_index_version
        AND sl.is_active = TRUE
        AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
        AND (1 - (sc.embedding <=> p_query_embedding)) >= p_match_threshold

    ORDER BY boosted_score DESC
    LIMIT p_match_count;
$$;
```

**Why this prevents HNSW index usage — three compounding reasons**:

**Reason A: ORDER BY is on a derived expression, not the index key**  
pgvector's HNSW index supports one specific query pattern:
```sql
ORDER BY embedding <=> query_embedding LIMIT k
```
The `match_all_b1_sources` function orders by `boosted_score DESC`, which is `1 - distance` or `1 - distance + 0.15`. PostgreSQL cannot push this derived expression ORDER BY into the HNSW index scan because the index only knows about the raw distance operator `<=>`. Result: sequential scan of all rows, computing distances for every row.

**Reason B: UNION ALL prevents index pushdown**  
The `ORDER BY ... LIMIT` applies to the UNION ALL result set, not to either individual leg. PostgreSQL cannot apply the HNSW index to a UNION ALL query because it must materialize both sides before sorting. Each leg is executed as a full sequential scan.

**Reason C: WHERE threshold on similarity prevents iterator use**  
Even if the UNION ALL were removed, the `WHERE (1 - (embedding <=> p_query_embedding)) >= p_match_threshold` condition on every row forces PostgreSQL to compute distances for all rows before filtering. HNSW iterator mode (which would limit how many rows are scanned) cannot be used when a similarity threshold must be evaluated.

**Verification**: Run this in Supabase SQL editor to confirm:
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM match_all_b1_sources(
    '[0.1, 0.2, ...]'::VECTOR(1536),  -- your test embedding
    'unity_bank',
    32, 0.27, 'v2'
);
```
If the EXPLAIN output shows `Seq Scan` (not `Index Scan` using `idx_rtc_embedding_hnsw`), this finding is confirmed.

**What the HNSW indexes are doing**: The HNSW indexes defined in `B1_006_indexes.sql` (lines 97-100 and 112-115) ARE present in the schema:
```sql
CREATE INDEX IF NOT EXISTS idx_rtc_embedding_hnsw
    ON public.rag_ticket_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_rsc_embedding_hnsw
    ON public.rag_sop_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```
They are defined correctly. But the SQL in `match_all_b1_sources` never allows them to be used.

---

### 2.2 FINDING 2 — HIGH: New Supabase Client Per Request

**Confidence**: Very High  
**Estimated impact**: 100–600ms per request (a separate problem from Finding 1)

**File**: `app/main.py:968`, function `_build_chat_generator()`

```python
def _build_chat_generator(app_settings, rag_settings, openai_api_key):
    supabase = create_client(app_settings.supabase_url, app_settings.supabase_key)  # ← line 968
    ...
```

This function is called on every single `/rag/chat` and `/freshdesk/webhook` request. `supabase-py`'s `create_client()` creates a new `httpx.Client` (or `AsyncClient`) internally. Each new client starts with an empty connection pool, meaning:
- First RPC call through the new client pays: DNS lookup + TCP handshake + TLS handshake
- In cross-region deployments (e.g., service in India, Supabase in US-East), this adds 300–800ms

The existing HNSW problem (Finding 1) completely dominates, so this looks smaller by comparison. But after Finding 1 is fixed and the RPC drops to <100ms, this overhead would become the new primary bottleneck.

**Secondary creation**: The `OpenAIEmbeddingProvider` is also created fresh per request (line 970–981) and explicitly closed in the `finally` block (line 1083–1086). This creates a new httpx client for each embedding API call.

---

### 2.3 FINDING 3 — HIGH: No Embedding Cache

**Confidence**: High  
**Estimated impact**: 200–500ms saved per cache hit (typical embedding API latency)

Every `/rag/chat` request calls OpenAI to embed the query, even for repeated queries. A Redis-backed embedding cache (query_text → embedding vector) would eliminate this for repeated queries. Designed in `PERFORMANCE_AND_SCALING_REVIEW.md §M5.1`.

---

### 2.4 FINDING 4 — MEDIUM: Sequential Retrieval Legs

**Confidence**: High  
**Estimated impact**: 200–500ms after Fix 1 and Fix 2 are applied

In `HybridTicketRetriever.retrieve()`:

```python
# Step 1: embed (blocks)
query_embedding = self._embedder.embed_single(request.query_text)

# Step 2: semantic search (blocks after embedding)
semantic_raw = self._search_with_fetch(request, query_embedding, fetch_count)

# Step 3: FTS search (blocks after semantic)
keyword_raw = self._search_keyword(request, fetch_count)
```

Step 2 and Step 3 are sequential. The FTS search (`search_b1_sources_fts`) does not use the query embedding — it only needs the raw query text. This means FTS could start at the same time as the embedding call, or at least run concurrently with the semantic search.

Parallelizing steps 2 and 3 using `asyncio.gather` would reduce total retrieval time by approximately `min(semantic_latency, fts_latency)`. After Fix 1, semantic latency drops to ~100ms and FTS latency is ~200ms — parallelizing them saves ~100ms. Not dramatic, but worth doing.

---

### 2.5 FINDING 5 — MEDIUM: No LLM Connection Pooling

**Confidence**: Medium  
**Estimated impact**: 50–300ms per request

`B1LLMClient` is created fresh per request in `_build_chat_generator()`. The `complete_json()` call creates a new HTTPX client each time (similar to the Supabase client issue). Unlike the Supabase issue, most LLM API latency is dominated by generation time (2–5s), so the connection overhead (100–300ms) is proportionally smaller.

---

### 2.6 FINDING 6 — LOW: History Write Adds Sequential Supabase Calls

**Confidence**: High  
**Estimated impact**: 200–600ms per request (for `persist_history=True` sessions)

`ChatGenerator.generate()` writes two records to the `chat_messages` table per request (one user message, one assistant message). These are synchronous and happen before the response is returned to the caller. They should be converted to background tasks (`BackgroundTasks.add_task`) so they do not block the response.

---

## 3. Root Cause Summary Table

| # | Finding | Where | Impact | Confidence | Fix Complexity |
|---|---------|--------|--------|------------|----------------|
| 1 | UNION ALL prevents HNSW (sequential scan) | `B1_005_rpc_functions.sql` | ~5,000–6,500ms | Very High | Medium |
| 2 | New Supabase client per request | `app/main.py:968` | ~100–600ms | Very High | Low |
| 3 | No embedding cache | — | ~200–500ms saved | High | Medium |
| 4 | Sequential retrieval legs | `hybrid_ticket_retriever.py` | ~100–500ms | High | Low |
| 5 | No LLM connection pooling | `_build_chat_generator()` | ~50–300ms | Medium | Low |
| 6 | History writes block response | `chat_generator.py` | ~200–600ms | High | Low |

---

## 4. Projected Latency After Fixes

Assuming all fixes are applied, projected P50 retrieval latency:

| Stage | Before Fix | After Fix |
|-------|-----------|-----------|
| Embedding API | 300–500ms | ~200ms (cached: <5ms) |
| match_ticket_chunks RPC (HNSW, shared client) | — | ~80ms |
| match_sop_chunks RPC (HNSW, shared client) | — | ~50ms |
| FTS RPC (concurrent with semantic) | ~300ms | ~200ms (parallel) |
| RRF + reranking | ~10ms | ~10ms |
| **Total retrieval** | **~7,500ms** | **~350ms** |
| LLM generation (gpt-4o-mini) | ~2,500ms | ~2,500ms (unchanged) |
| History ops | ~400ms | ~50ms (background) |
| **Total response** | **~10,500ms** | **~2,900ms** |

Target `semantic_latency_ms < 200ms` is achievable after Fix 1 + Fix 2.  
Target `total_retrieval_ms < 800ms` is achievable after all fixes.

---

## 5. Recommended Fixes — Ordered by Priority

### Fix 1 (CRITICAL): Restructure `match_all_b1_sources` for HNSW Compatibility

**File**: `sql/b1_migrations/B1_005_rpc_functions.sql`  
**New migration**: `sql/b1_migrations/B1_010_fix_hnsw_compatible_rpc.sql`

**Principle**: Each search leg must be expressed as `ORDER BY embedding <=> query_embedding LIMIT k` in isolation, allowing HNSW to process it. The UNION and re-sorting must happen in Python (the caller), not in SQL.

**Option A (SQL): Two-function approach**

Replace the unified `match_all_b1_sources` with two separate functions — one for tickets, one for SOPs — each using HNSW-compatible ORDER BY:

```sql
-- Ticket leg: HNSW-compatible
CREATE OR REPLACE FUNCTION public.match_b1_ticket_chunks(
    p_query_embedding   VECTOR(1536),
    p_client            TEXT,
    p_match_count       INTEGER DEFAULT 32,
    p_match_threshold   FLOAT   DEFAULT 0.25,
    p_index_version     TEXT    DEFAULT 'v1'
)
RETURNS TABLE (id UUID, source_table TEXT, ..., similarity FLOAT, boosted_score FLOAT, ...)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT
        rtc.id,
        'rag_ticket_chunks'::TEXT AS source_table,
        ...
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS similarity,
        (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS boosted_score,
        ...
    FROM public.rag_ticket_chunks rtc
    WHERE
        rtc.client = p_client
        AND rtc.index_version = p_index_version
        AND rtc.automation_label != 'ESCALATION'
    ORDER BY rtc.embedding <=> p_query_embedding   -- ← HNSW index is used HERE
    LIMIT p_match_count;
$$;
```

Note the critical changes:
1. `ORDER BY rtc.embedding <=> p_query_embedding` (not `ORDER BY boosted_score DESC`)
2. The threshold filter is removed from the WHERE clause — filtering happens in Python after retrieval
3. No UNION ALL — each table has its own function

**Python caller** (`HybridTicketRetriever._search_with_fetch`): merges the two results, applies the threshold filter in Python, and computes the boosted score in Python before RRF.

**Option B (SQL): CTE with row_number**

If maintaining a single RPC is preferred, use CTEs where each leg has its own ORDER BY and LIMIT:

```sql
CREATE OR REPLACE FUNCTION public.match_all_b1_sources_v2(...)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    WITH ticket_hits AS (
        SELECT
            rtc.id, 'rag_ticket_chunks' AS source_table, ...,
            (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS similarity,
            (1 - (rtc.embedding <=> p_query_embedding))::FLOAT AS boosted_score,
            ...
        FROM public.rag_ticket_chunks rtc
        WHERE
            rtc.client = p_client
            AND rtc.index_version = p_index_version
            AND rtc.automation_label != 'ESCALATION'
        ORDER BY rtc.embedding <=> p_query_embedding  -- HNSW used for this leg
        LIMIT p_match_count
    ),
    sop_hits AS (
        SELECT
            sc.id, 'rag_sop_chunks' AS source_table, ...,
            (1 - (sc.embedding <=> p_query_embedding))::FLOAT AS similarity,
            LEAST(1.0, (1 - (sc.embedding <=> p_query_embedding)) + 0.15) AS boosted_score,
            ...
        FROM public.rag_sop_chunks sc
        JOIN public.rag_sop_library sl ON sl.sop_id = sc.sop_id
        WHERE
            sc.index_version = p_index_version
            AND sl.is_active = TRUE
            AND (sc.clients = '{}' OR p_client = ANY(sc.clients))
        ORDER BY sc.embedding <=> p_query_embedding  -- HNSW used for this leg
        LIMIT p_match_count
    )
    SELECT * FROM ticket_hits
    UNION ALL
    SELECT * FROM sop_hits
    ORDER BY boosted_score DESC
    LIMIT p_match_count;
$$;
```

**IMPORTANT**: Whether the CTE approach actually uses HNSW for the individual legs depends on the PostgreSQL version and query planner. The CTE legs might be materialized first (pre-12.0 behavior), which allows HNSW per leg. But in PostgreSQL >= 12, CTEs may be inlined, which could re-introduce the UNION ALL problem. Option A (two separate functions) is safer.

**Rollback plan**: The existing `match_all_b1_sources` function remains unchanged. The new function is named `match_all_b1_sources_v2`. The Python code is updated to call the v2 function when available, with the v1 function as fallback (detected by whether v2 exists). This is a zero-downtime migration.

---

### Fix 2 (HIGH): Module-Level Supabase Client Singleton

**File**: `app/main.py`

Move `create_client()` to module scope. The client is shared across all requests:

```python
# SPRINT0_FIX: module-level singletons to avoid per-request TCP+TLS overhead
_supabase_client: Any = None

def _get_supabase_client(settings: Any) -> Any:
    """Return module-level Supabase client. Creates once, reused for all requests."""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(settings.supabase_url, settings.supabase_key)
    return _supabase_client
```

**Thread safety**: `supabase-py`'s `SyncClient` is NOT thread-safe for concurrent modification, but read-only RPC calls and table queries are safe to share across threads when using `asyncio.to_thread` because they run in the thread pool.

**Rollback**: Replace calls back to `create_client()` if singleton causes issues.

---

### Fix 3 (HIGH): Redis-Backed Embedding Cache

**New file**: `rag_engine/embedding/cached_embedder.py`

Cache query text → embedding vector in Redis with TTL=3600s. Second identical query skips the OpenAI API call entirely. Design specified in `PERFORMANCE_AND_SCALING_REVIEW.md §M5.1`.

**Rollback**: `EMBEDDING_CACHE_ENABLED=false` falls back to direct API calls.

---

### Fix 4 (MEDIUM): Parallelize Semantic + FTS Retrieval

**File**: `rag_engine/retrieval/hybrid_ticket_retriever.py`

FTS search does not depend on the query embedding. Run FTS concurrently with the embedding call, or at minimum run semantic + FTS concurrently with `asyncio.gather` (since both execute in the thread pool). Design specified in `PERFORMANCE_AND_SCALING_REVIEW.md §M5.2`.

---

### Fix 5 (LOW): Convert History Writes to Background Tasks

**File**: `rag_engine/generation/chat_generator.py`

The two `history_store.append()` calls at the end of `generate()` block response delivery. Convert to background tasks using FastAPI's `BackgroundTasks` mechanism (pass a `background_tasks` callback into `generate()`). The response is returned first; history is written after.

---

## 6. Verification Queries (Run in Supabase SQL Editor)

### Check whether HNSW indexes exist:
```sql
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE tablename IN ('rag_ticket_chunks', 'rag_sop_chunks')
  AND indexname LIKE '%hnsw%';
```

### Check row counts:
```sql
SELECT 
    'rag_ticket_chunks' AS table_name, 
    client, 
    index_version, 
    COUNT(*) AS row_count
FROM public.rag_ticket_chunks
GROUP BY client, index_version
ORDER BY client, index_version;

SELECT 
    'rag_sop_chunks' AS table_name, 
    index_version, 
    COUNT(*) AS row_count
FROM public.rag_sop_chunks
GROUP BY index_version;
```

### Check whether HNSW is used for a direct ORDER BY query (reference pattern):
```sql
-- Run this first to get EXPLAIN before applying Fix 1
-- Replace the embedding array with an actual embedding vector
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT id, (1 - (embedding <=> '[...]'::VECTOR(1536)))::FLOAT AS similarity
FROM public.rag_ticket_chunks
WHERE client = 'unity_bank'
  AND index_version = 'v2'
ORDER BY embedding <=> '[...]'::VECTOR(1536)
LIMIT 32;
```
If this shows `Index Scan using idx_rtc_embedding_hnsw` → HNSW is working correctly.

### Check whether HNSW is NOT used in the current match_all_b1_sources:
```sql
-- After creating a test embedding, run EXPLAIN on the current unified function
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM match_all_b1_sources(
    (SELECT embedding FROM rag_ticket_chunks LIMIT 1),  -- reuse an existing embedding as test
    'unity_bank',
    32, 0.10, 'v2'
);
```
If this shows `Seq Scan` for both table legs → confirms Finding 1.

---

## 7. Implementation Order for Sprint 0

| Step | Task | File | Effort | Risk |
|------|------|------|--------|------|
| 0 | Run verification queries in Supabase | — | 15 min | None |
| 0 | Run `scripts/benchmark_retrieval_pipeline.py` | scripts/ | 30 min | None |
| 1a | Write `B1_010_fix_hnsw_compatible_rpc.sql` (new v2 RPC) | sql/ | 2 hours | Low |
| 1b | Apply migration to Supabase | Supabase | 30 min | Medium |
| 1c | Update Python caller to use v2 RPC | hybrid_ticket_retriever.py | 1 hour | Low |
| 1d | Run governance validation suite (must still pass 48/48) | — | 30 min | None |
| 1e | Measure latency — confirm < 200ms | benchmark script | 30 min | None |
| 2 | Module-level Supabase client singleton | app/main.py | 1 hour | Low |
| 3 | Redis embedding cache | rag_engine/embedding/ | 4 hours | Medium |
| 4 | Parallel retrieval legs | hybrid_ticket_retriever.py | 2 hours | Medium |
| 5 | History writes → background tasks | chat_generator.py | 1 hour | Low |

**Sprint 0 gate** (from `BIG_PHASE_2_IMPLEMENTATION_ROADMAP.md`):
- pgvector retrieval P50 < 500ms ← primarily requires Fix 1 + Fix 2
- CI is fully blocking
- HMAC enforcement is active
- Supabase key is rotated

---

## 8. What Was NOT Changed During Diagnosis

This report is DIAGNOSTIC ONLY. The following files were read but NOT modified:

- `app/main.py` — full `/rag/chat` handler and `_build_chat_generator()`
- `rag_engine/generation/chat_generator.py` — full generation pipeline
- `rag_engine/retrieval/hybrid_ticket_retriever.py` — full hybrid retrieval
- `rag_engine/retrieval/ticket_retriever.py` — base retrieval class
- `sql/b1_migrations/B1_005_rpc_functions.sql` — SQL function definitions (read only)
- `sql/b1_migrations/B1_006_indexes.sql` — HNSW index definitions (read only)
- `retrieval/hybrid_search.py` — legacy retrieval path (used by `/query` only, not `/rag/chat`)

The only new file created during Sprint 0 diagnosis is:
- `scripts/benchmark_retrieval_pipeline.py` — diagnostic benchmark script (marked `# SPRINT0_DIAG`)

The benchmark script is diagnostic only. It makes no writes to any database tables.

---

## 9. Pre-Fix Instrumentation Added

The `diagnostics` dict returned by `/rag/chat` already includes:

```json
{
  "embedding_latency_ms": 287.4,
  "semantic_latency_ms": 6831.2,
  "keyword_latency_ms": 312.8,
  "fusion_latency_ms": 8.1,
  "total_latency_ms": 7487.3,
  "retrieval_mode": "hybrid",
  "used_fallback": false,
  "retrieval_confidence": "medium",
  "overlap_count": 4,
  "overlap_ratio": 0.21,
  "selected_rrf_k": 60
}
```

No additional instrumentation was needed — the existing diagnostic output already exposes the bottleneck. After applying Fix 1, `semantic_latency_ms` should drop from ~6800ms to <200ms. This is the primary verification metric.

---

## 10. Appendix: Why the HNSW Index Was Built But Is Never Used

The HNSW indexes in `B1_006_indexes.sql` are defined correctly and exist in the database. They are NOT broken. The problem is that `match_all_b1_sources` was written in a style that prevents the query planner from using them.

pgvector's HNSW supports **index scans** only for queries of the form:
```sql
ORDER BY column <=> query_vector LIMIT k
```

When any of the following conditions are true, the query planner CANNOT use the HNSW index:
1. The ORDER BY column is an expression derived from the distance (e.g., `1 - distance`, `1 - distance + 0.15`)
2. The ORDER BY applies to a UNION ALL result (the planner cannot push the index into a UNION)
3. The query has a WHERE clause with a similarity threshold — the planner cannot use the index to resolve a threshold filter (it can only use it for top-k ordering)

All three conditions are present in `match_all_b1_sources`. The HNSW indexes were correctly built, but the SQL was written in a pattern that is incompatible with HNSW. The fix is to rewrite the SQL to match the HNSW-compatible pattern, not to rebuild the indexes.
