# RCA Retrieval Fix Report — T6 Failure Resolution

**Date**: 2026-05-18  
**Severity**: Medium (1/50 check failing; 49/50 pass)  
**Status**: FIXED  
**File modified**: `rag_engine/retrieval/ticket_retriever.py`  

---

## 1. Failing Test — T6 Detail

From `data/reports/b1_retrieval_sanity_report.md`:

```
T6 — RCA-heavy retrieval  [FAILED]
  [OK]  'root cause analysis for OTP delivery failure' → 2 RESOLUTION_RCA chunks
  [OK]  'why does the video KYC session keep dropping' → 4 RESOLUTION_RCA chunks
  [OK]  'what causes authentication failure in the sys' → 7 RESOLUTION_RCA chunks
  [FAIL] chunk_type filter returns only RESOLUTION_RCA: 10 chunks
         Non-RESOLUTION_RCA chunks leaked: N (non-zero)
```

The first three checks pass (RESOLUTION_RCA chunks surface in general retrieval). The fourth check fails: when `chunk_types=["RESOLUTION_RCA"]` is explicitly requested, non-RESOLUTION_RCA chunks are still included in results.

---

## 2. Root Cause Analysis

### 2.1 Retrieval Request with chunk_types

The test calls:
```python
resp = harness.retrieve(
    "why does authentication fail",
    client=client,
    top_k=10,
    chunk_types=["RESOLUTION_RCA"],   # ← explicit filter requested
)
non_resolution = [c for c in resp.chunks if c.chunk_type != "RESOLUTION_RCA"]
assert len(non_resolution) == 0       # ← this fails
```

### 2.2 Trace Through the Code

**Step 1 — `RetrievalRequest` is created:**
```python
# validate_b1_retrieval.py → RetrievalTestHarness.retrieve()
req = RetrievalRequest(
    query_text=query,
    client=client,
    top_k=top_k,
    chunk_types=["RESOLUTION_RCA"],   # stored in request object
    ...
)
```

**Step 2 — `TicketRetriever.retrieve()` calls `_search()`:**
```python
# ticket_retriever.py:_search()
response = self._client.rpc(
    "match_all_b1_sources",
    {
        "p_query_embedding": embedding,
        "p_client": request.client,
        "p_match_count": request.top_k * 3,
        "p_match_threshold": request.similarity_threshold,
        "p_index_version": request.index_version,
        # ← request.chunk_types is NEVER passed here
    }
).execute()
```

**Step 3 — `match_all_b1_sources` RPC signature:**
```sql
CREATE OR REPLACE FUNCTION public.match_all_b1_sources(
    p_query_embedding   VECTOR(1536),
    p_client            TEXT,
    p_match_count       INTEGER DEFAULT 10,
    p_match_threshold   FLOAT   DEFAULT 0.25,
    p_index_version     TEXT    DEFAULT 'v1'
    -- NO p_chunk_types parameter
)
```

The unified RPC has no chunk-type filter parameter. All chunk types (ISSUE_HEADER, QUERY_BODY, RESOLUTION_RCA, SOP_STEPS) are returned based on similarity only.

**Step 4 — Results converted, no filter applied:**
```python
chunks = [self._row_to_chunk(r) for r in raw_results]
total_candidates = len(chunks)
# ← request.chunk_types is never consulted here either
chunks = self._reranker.rerank(...)
```

The `chunk_types` field in `RetrievalRequest` was defined but never wired to any filtering logic. It was a silent no-op.

### 2.3 Why It Matters

The chunk_type filter is used in two legitimate scenarios:
1. **T6 diagnostic check**: Verifies the filter mechanism works (the failing test).
2. **`retrieve_query_body_only()`**: A convenience method that sets `chunk_types=["QUERY_BODY"]`. Without the filter, this method also returns RESOLUTION_RCA and ISSUE_HEADER chunks — the wrong behavior.
3. **Future B2 usage**: The generation layer may want to retrieve only RESOLUTION_RCA for context assembly.

---

## 3. Fix Implementation

### 3.1 Code Change

**File**: `rag_engine/retrieval/ticket_retriever.py`  
**Method**: `retrieve()`, between Step 3 and Step 4  

**Before** (lines 147–155):
```python
        # ── Step 3: Convert to RetrievedChunk objects ───────────────────────
        chunks = [self._row_to_chunk(r) for r in raw_results]
        total_candidates = len(chunks)

        # ── Step 4: Reranking (NullReranker by default) ─────────────────────
        chunks = self._reranker.rerank(...)
```

**After** (lines 147–160):
```python
        # ── Step 3: Convert to RetrievedChunk objects ───────────────────────
        chunks = [self._row_to_chunk(r) for r in raw_results]
        total_candidates = len(chunks)

        # ── Step 3b: chunk_type filter ──────────────────────────────────────
        # match_all_b1_sources returns all chunk types; filter here when requested.
        # total_candidates above reflects the pre-filter RPC count for observability.
        if request.chunk_types:
            allowed = set(request.chunk_types)
            chunks = [c for c in chunks if c.chunk_type in allowed]

        # ── Step 4: Reranking (NullReranker by default) ─────────────────────
        chunks = self._reranker.rerank(...)
```

### 3.2 Design Decisions

**Why Python-side filter (not SQL)?**

Option considered: add `p_chunk_types TEXT[] DEFAULT NULL` to `match_all_b1_sources` and filter at DB level. This would be more efficient but requires:
- A `CREATE OR REPLACE FUNCTION` statement applied in Supabase
- A re-test of the RPC

The Python-side filter is:
- Zero migration risk
- Correct for all current usage patterns
- Negligible performance overhead (filtering a list of ~30 pre-fetched dicts)
- Future optimization: SQL-level filter can be added when the RPC is next modified

**Why filter before reranking?**

The filter is placed before `NullReranker.rerank()` so that:
1. The reranker only sees the filtered type set
2. If a real reranker is later plugged in (LexicalReranker, CrossEncoderReranker), it cannot reintroduce filtered-out chunk types

**Why use `set(request.chunk_types)` for O(1) lookup?**

Although the typical chunk_types list is small (1–3 items), using a set instead of list membership check (`in allowed` vs `in request.chunk_types`) avoids O(n) scans per chunk. Defensive correctness.

**Why is `total_candidates` computed before the filter?**

`total_candidates` is returned in `RetrievalResponse.retrieval_metadata` for observability. It should reflect how many candidates the RPC returned, not how many survived the type filter. This is more informative for debugging: "RPC returned 30 candidates, 8 survived chunk_type filter" is more useful than "8 candidates returned".

### 3.3 Backward Compatibility

- `chunk_types=None` (the default in `RetrievalRequest`) → filter block is skipped → behavior unchanged
- All existing callers that don't set `chunk_types` are unaffected
- `retrieve_query_body_only()` now correctly returns only QUERY_BODY chunks (was previously broken)
- `retrieve_resolution_for_ticket()` uses a direct table query with `.eq("chunk_type", "RESOLUTION_RCA")` — not affected by this change

---

## 4. Validation Evidence

### 4.1 Pre-Fix Test State (from report)

```
T6 — RCA-heavy retrieval  [FAILED]  (3/4 checks)
  [FAIL] chunk_type filter returns only RESOLUTION_RCA: 10 chunks
         Non-RESOLUTION_RCA chunks leaked: N
```

### 4.2 Expected Post-Fix State

After applying the fix and re-running `python scripts/validate_b1_retrieval.py --live --report`:

```
T6 — RCA-heavy retrieval  [PASSED]  (4/4 checks)
  [OK]  chunk_type filter returns only RESOLUTION_RCA: X chunks
        Non-RESOLUTION_RCA chunks leaked: 0
```

Overall score: **100%** (50/50 checks)

### 4.3 Offline Verification

The fix can be verified without a live DB by tracing the code path:
1. `RetrievalRequest(chunk_types=["RESOLUTION_RCA"])` sets the field
2. `_search()` returns a mock list of mixed chunk types
3. After `_row_to_chunk()`, the new filter `[c for c in chunks if c.chunk_type in {"RESOLUTION_RCA"}]` removes non-matching chunks
4. Reranker receives only RESOLUTION_RCA chunks

---

## 5. Performance Impact

| Metric | Before Fix | After Fix |
|---|---|---|
| Latency | 404ms avg for T6 | +0–2ms (list comprehension on ~30 items) |
| Network I/O | Unchanged | Unchanged (filter is client-side) |
| DB load | Unchanged | Unchanged |
| Memory | Unchanged | Slightly reduced (fewer chunk objects held) |

The fix adds a single list comprehension over at most `top_k * 3` items (≤ 30 dicts for default settings). This is negligible.

---

## 6. Future Enhancement (Optional)

To push the filter to the database level, add to `match_all_b1_sources`:

```sql
-- Add parameter:
p_chunk_types TEXT[] DEFAULT NULL,

-- Add to WHERE clause (ticket leg only; SOP chunks are always SOP_STEPS):
AND (p_chunk_types IS NULL OR rtc.chunk_type = ANY(p_chunk_types))
```

This optimization is not required for correctness. Apply when the RPC is next modified for other reasons.
