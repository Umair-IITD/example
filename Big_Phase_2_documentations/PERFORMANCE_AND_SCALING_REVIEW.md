# Performance and Scaling Review — Big Phase 2

## 1. Current Performance Baseline

The production system exhibits the following observed latencies:

| Component | Observed Latency | Notes |
|-----------|-----------------|-------|
| Semantic retrieval (pgvector) | ~6.8s | Dominant bottleneck |
| FTS retrieval | ~0.3s | Fast; BM25 ranked |
| RRF fusion + BM25 reranking | ~0.05s | In-process Python; negligible |
| OpenAI embedding (query) | ~0.6–1.2s | 1 API call per request |
| OpenAI chat generation | ~2–4s | gpt-4o-mini; depends on answer length |
| Total P50 | ~10s | Unacceptable for scale |
| Total P95 | ~15s | Critically problematic |

**Root cause analysis**: 6.8 seconds for pgvector retrieval on a HNSW index is anomalous. The HNSW index should return results in 10–100ms for typical corpus sizes. This indicates one or more of:

1. **Cold connection**: Supabase connection is being initialized on each request (no connection pooling / persistent connection)
2. **HNSW build not complete**: The index may be in a partially-built state or `ef_search` is set too high
3. **Supabase plan limits**: Free/starter Supabase tiers throttle RPC function execution
4. **Network latency**: The service and Supabase project are in different regions
5. **RPC function overhead**: The `match_all_b1_sources` function is doing more work than pgvector retrieval alone (joining, filtering, boosting) before returning results

---

## 2. Bottleneck Analysis

### 2.1 pgvector Retrieval (~6.8s) — CRITICAL

**Expected behavior**: HNSW `<=>` (cosine) scan on 1536-dim vectors returns top-K in 10–100ms for corpora under 1 million documents.

**Likely causes and diagnostic steps**:

```
Hypothesis A: Connection not pooled
Diagnostic: Add timing instrumentation
  start = time.perf_counter()
  client = get_supabase_client()   # ← measure this
  result = client.rpc(...).execute()  # ← measure this separately
  
If client creation = 5+ seconds → connection pooling issue.
Fix: Use a module-level persistent client; do not create per-request.

Hypothesis B: Supabase plan throttling  
Diagnostic: Measure RPC latency directly in Supabase SQL editor:
  EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM match_all_b1_sources(...);
  
If <100ms in SQL editor but >6s in Python → overhead is in Python-Supabase HTTP layer.
Fix: Evaluate asyncpg direct connection; or Supabase connection pooler (PgBouncer).

Hypothesis C: HNSW index not properly built
Diagnostic: 
  SELECT * FROM pg_indexes WHERE tablename = 'documents';
  SELECT COUNT(*) FROM documents WHERE index_version = 'v2';
  
If index not present or only partial → rebuild with:
  CREATE INDEX CONCURRENTLY ON documents USING hnsw(embedding vector_cosine_ops)
  WITH (m=16, ef_construction=64);

Hypothesis D: ef_search too high
The HNSW ef_search parameter controls accuracy vs. speed tradeoff.
Default is often 40; if set to 200+ for accuracy, it linearly increases latency.
Fix: SET hnsw.ef_search = 40; in the RPC function for retrieval (reduce from 200).

Hypothesis E: Network region mismatch
If service is in Mumbai and Supabase is in US East → 200ms round-trip minimum,
multiplied by sequential queries (embedding API → Supabase → LLM).
Fix: Deploy service in same region as Supabase project.
```

### 2.2 OpenAI Embedding (~0.6–1.2s per request)

Every `/rag/chat` call embeds the query. This is a synchronous external API call on the critical path.

**Optimization options**:

1. **Query embedding cache** (highest impact, lowest risk): Hash the normalized query text; cache the embedding in Redis with TTL=3600s. For support tickets, queries like "OTP not received" are extremely common — cache hit rate may be 30–50%.

2. **Batch pre-warming** (low priority): For known high-frequency queries (from analytics), pre-embed and cache them overnight.

3. **Alternative embedding endpoint** (risky): Use a faster/cheaper model like `text-embedding-3-large` is slower; `text-embedding-ada-002` is faster but lower quality. Do not optimize embedding model without re-ingesting all documents.

### 2.3 OpenAI Chat Generation (~2–4s)

Generation latency is irreducible without:
- Streaming (reduces perceived latency but not actual latency)
- A faster model (GPT-4o is faster than GPT-4o-mini at generation but costs more)
- Caching answers (risky — stale answers)

**Target**: Accept 2–4s generation as fixed cost. Reduce retrieval from 6.8s to <1s, making total latency 3–5s.

---

## 3. Target Latency Architecture

### 3.1 Latency Budget (Phase 2A Target)

| Component | Current | Target | Method |
|-----------|---------|--------|--------|
| Query embedding | 0.6–1.2s | <0.1s (cache hit) / 0.8s (miss) | Redis embedding cache |
| pgvector retrieval | 6.8s | <0.3s | Fix connection pooling + HNSW config |
| FTS retrieval | 0.3s | 0.15s | Run parallel with semantic |
| RRF + BM25 | 0.05s | 0.05s | Already optimal |
| Memory read | — | <0.1s (cache hit) / 0.15s (miss) | Redis-first pattern |
| Intent classification | — | <0.2s | In-process classifier (no LLM) |
| Chat generation | 2–4s | 2–4s | Cannot reduce without streaming |
| **Total P50** | **~10s** | **<4s** | All optimizations applied |
| **Total P95** | **~15s** | **<8s** | |

### 3.2 Parallel Retrieval Architecture

Currently, semantic and FTS retrieval are sequential. They can be parallelized:

```python
# Current (sequential):
semantic_results = await semantic_retrieve(query_embedding, client)
fts_results = await fts_retrieve(query_text, client)

# Target (parallel):
semantic_task = asyncio.create_task(semantic_retrieve(query_embedding, client))
fts_task = asyncio.create_task(fts_retrieve(query_text, client))
embedding_task = asyncio.create_task(embed_query(query_text))  # overlap with retrieval setup

semantic_results, fts_results = await asyncio.gather(semantic_task, fts_task)
```

**Impact**: Eliminates the sequential overhead of FTS (0.3s) by running it during the same window as semantic retrieval. Net savings: 0.3s.

**Risk**: Requires both retrieval calls to be async-native. Verify that the Supabase Python client's `.execute()` is non-blocking in async context, or wrap with `asyncio.to_thread()`.

---

## 4. Caching Strategy

### 4.1 Query Embedding Cache

```
Cache key: embed:{sha256(normalized_query_text)[:16]}:{embedding_model}
Cache value: JSON array of 1536 floats (≈12KB per entry)
TTL: 3600s (1 hour)
Eviction: LRU (Redis maxmemory-policy allkeys-lru)
```

**Implementation**:
```python
async def get_or_embed(query_text: str, model: str) -> list[float]:
    cache_key = f"embed:{hashlib.sha256(query_text.encode()).hexdigest()[:16]}:{model}"
    cached = await redis.get(cache_key)
    if cached:
        metrics.increment("embedding_cache_hit")
        return json.loads(cached)
    embedding = await openai_embed(query_text, model)
    await redis.setex(cache_key, 3600, json.dumps(embedding))
    metrics.increment("embedding_cache_miss")
    return embedding
```

**Memory cost**: 1000 cached embeddings × 12KB = 12MB. Negligible for a 256MB Redis instance.

**Expected cache hit rate**: 20–40% for typical support ticket traffic (common queries recur).

### 4.2 SOP Content Cache

After RRF fusion, the top SOP chunks for common queries are deterministic (same query → same chunks). Cache the assembled context:

```
Cache key: ctx:{sha256(query_embedding_hex[:32] + client)[:16]}:{index_version}
Cache value: JSON {chunks: [...], context_text: "...", top_similarity: 0.72}
TTL: 1800s (30 minutes — longer TTL risks serving stale SOP content)
Invalidation: on new ingestion, flush ctx:* for affected client
```

**Risk**: If a SOP is updated and ingested, cached context keys must be invalidated. Implement with a cache-bust on successful ingestion: publish `invalidate:{client}:{index_version}` event to Redis pub/sub.

### 4.3 Intent Classification Cache

```
Cache key: intent:{sha256(normalized_query)[:16]}
Cache value: JSON {category, confidence, retrieval_profile}
TTL: 7200s (2 hours — intent classification is stable)
```

### 4.4 What NOT to Cache

- **Generated answers**: Stale answers are worse than slow answers. Never cache LLM output.
- **Governance decisions**: Never cache `requires_human` or `automation_safe`. These must be evaluated per-request.
- **Memory reads from Supabase**: Cache is the Redis layer (handled by MemoryRouter). The Supabase result itself is not cached beyond the Redis TTL.

---

## 5. Database Optimization

### 5.1 Supabase Connection Strategy

**Current issue**: The Supabase Python client (`supabase-py`) uses HTTPX under the hood. If a new client is created per request, every retrieval call pays TCP handshake + TLS negotiation cost.

**Fix**: Module-level singleton client with connection reuse:
```python
# In supabase_client.py — already partially implemented
_client: Optional[Client] = None

def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client
```

Verify that `_client` is initialized once at module import, not per-request.

### 5.2 HNSW Index Configuration Review

```sql
-- Check current index parameters:
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'documents' AND indexdef LIKE '%hnsw%';

-- Optimal parameters for <100K documents:
CREATE INDEX ON documents 
USING hnsw(embedding vector_cosine_ops)
WITH (m=16, ef_construction=64);

-- Set ef_search for retrieval (lower = faster, slightly less accurate):
SET hnsw.ef_search = 40;  -- in RPC function or session setting
```

**Trade-off**: `ef_search=40` vs `ef_search=200`. At 40, HNSW returns 99%+ accuracy for well-distributed embeddings. The 1% missed case is extremely unlikely to affect support quality.

### 5.3 RPC Function Optimization

The `match_all_b1_sources` RPC function combines:
- pgvector cosine scan
- CASE WHEN for SOP boost
- WHERE clause for client + version
- JOIN with SOP flags
- LIMIT + ORDER BY

Analyze with `EXPLAIN ANALYZE` to verify index is used (not a sequential scan). If the WHERE clause on `client` + `index_version` does not use an index, add a composite index:

```sql
CREATE INDEX idx_documents_client_version 
ON documents(client, index_version);
```

### 5.4 PgBouncer / Connection Pooler

For multi-worker deployments (4 Gunicorn workers × concurrent requests = ~32 simultaneous connections), enable Supabase's built-in PgBouncer:

- Mode: **transaction mode** (not session mode — session mode breaks pgvector's `SET` commands)
- Pool size: 10 connections
- Reduces connection overhead for burst traffic

**Warning**: pgvector `SET hnsw.ef_search` is a session-level setting. In transaction mode pooling, session settings do not persist across connections. Set `ef_search` in the function body instead:
```sql
CREATE OR REPLACE FUNCTION match_all_b1_sources(...)
...
BEGIN
  SET LOCAL hnsw.ef_search = 40;  -- session-local, works in transaction mode
  ...
END;
```

---

## 6. Concurrency Model

### 6.1 Current State

```
Gunicorn: 1 worker (single process)
Uvicorn: async event loop per worker
Result: ~10–20 concurrent requests (limited by event loop + blocking calls)
```

### 6.2 Phase 2A Target

```
Gunicorn: 4 workers (4 processes, each with Uvicorn event loop)
Redis: shared rate limiting, session cache
Result: ~40–80 concurrent requests
```

**Blocker**: In-process rate limiting state must be migrated to Redis before adding workers (each worker would have its own counter without Redis).

### 6.3 Async-First Refactoring

Phase 2A should audit all blocking calls on the async path and wrap with `asyncio.to_thread()`:

```python
# These are blocking calls that must be wrapped:
supabase_client.rpc("match_all_b1_sources", ...).execute()  # HTTPX sync
openai_client.embeddings.create(...)  # OpenAI sync SDK

# Target pattern:
results = await asyncio.to_thread(
    supabase_client.rpc("match_all_b1_sources", {...}).execute
)
```

An alternative is to switch to the async Supabase client (`supabase-py>=2.x` has async support via `AsyncClient`).

---

## 7. Throughput Assumptions

### 7.1 Traffic Model

For a KwikID support deployment serving 3–5 enterprise clients:

| Scenario | Tickets/Day | Requests/Minute (peak) |
|----------|-------------|------------------------|
| Current production | ~50 | ~1–2 |
| Phase 2A target | ~200 | ~5–8 |
| Phase 2C target | ~1000 | ~20–30 |
| Phase 2D target | ~5000 | ~100+ |

### 7.2 OpenAI API Rate Limits

The largest external constraint is the OpenAI API tier:

| OpenAI Tier | RPM (chat) | TPM (embedding) |
|-------------|-----------|----------------|
| Tier 1 | 500 | 1,000,000 |
| Tier 2 | 5,000 | 2,000,000 |
| Tier 4 | 10,000 | 5,000,000 |

At Phase 2A targets (5–8 RPM chat, 5–8 RPM embedding), Tier 1 is sufficient. Phase 2D throughput targets require Tier 2+.

**Mitigation for rate limit spikes**: Query embedding cache (reduces embedding API calls by 20–40%). Phase 2D multi-provider routing further mitigates.

---

## 8. Scalability Model

### 8.1 Phase 2A Scaling Ceiling

With 4 workers + Redis + Supabase + OpenAI Tier 1:
- **Throughput ceiling**: ~20 RPM (OpenAI embedding limit becomes constraint before infrastructure)
- **Latency at ceiling**: P95 ~8s (memory adds ~150ms; embedding cache adds 0ms for cache hits)
- **Supabase connections at ceiling**: 4 workers × 5 concurrent = 20 connections (within free tier limits)

### 8.2 Phase 2D Scaling Path

When throughput exceeds 20 RPM:
1. Enable multi-provider embedding (Azure OpenAI + OpenAI, load-balanced) → doubles effective RPM
2. Enable horizontal pod scaling (Kubernetes, 8+ replicas) → linear throughput scaling
3. Enable pgvector dedicated instance (Supabase dedicated plan or self-hosted PostgreSQL) → removes Supabase connection pooling limit
4. Enable separate embedding service (batch inference, GPU) → eliminates OpenAI embedding API dependency

---

## 9. Performance Monitoring Plan

Prometheus metrics to add in Phase 2A:

```python
# Latency histograms
rag_embedding_latency_seconds = Histogram(...)
rag_retrieval_latency_seconds = Histogram(buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0])
rag_generation_latency_seconds = Histogram(...)
rag_memory_read_latency_seconds = Histogram(...)
rag_total_latency_seconds = Histogram(...)

# Cache effectiveness
rag_embedding_cache_hits_total = Counter(...)
rag_embedding_cache_misses_total = Counter(...)
rag_context_cache_hits_total = Counter(...)

# Throughput
rag_requests_total = Counter(labelnames=["client", "confidence", "status"])
```

SLO targets (Phase 2A):
- P50 latency < 5s
- P95 latency < 8s  
- P99 latency < 12s
- Error rate < 0.5%
- Embedding cache hit rate > 20%

Alerting thresholds:
- P95 > 10s for 5 consecutive minutes → alert
- Error rate > 2% for 2 consecutive minutes → alert
- Embedding API connection errors > 3 consecutive → circuit breaker opens
