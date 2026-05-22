# Algorithms and Design Decisions

## Retrieval Algorithms

### Why Hybrid Retrieval (Semantic + FTS)?

**Problem with pure semantic (vector) search:**
- Misses exact-keyword matches when embeddings don't align perfectly
- Struggles with specific identifiers (ticket numbers, product codes)
- Can return "semantically similar but not relevant" results

**Problem with pure keyword search:**
- Cannot capture intent or paraphrase
- "Account can't be accessed" ≠ "login blocked" in BM25
- No understanding of context

**Solution: Hybrid = Complementary strengths**
- Semantic search: finds conceptually related content
- FTS: finds exact terminology and specific identifiers
- RRF fusion: rewards chunks that score high in BOTH — the true relevant results

### Reciprocal Rank Fusion (RRF)

```
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

Why RRF over weighted score combination?
1. **Scale-agnostic**: cosine similarity (0–1) and ts_rank (arbitrary scale) can't be directly summed
2. **Rank-stable**: small score differences don't dominate
3. **No training required**: k=60 is a well-validated default from the IR literature
4. **De-duplication built-in**: the same document at rank 1 in both lists scores ~0.033, beating any document at rank 3 in one list (~0.016)

### BM25 Reranking

BM25 (Okapi BM25) is a probabilistic retrieval model:

```
BM25(d, q) = Σ IDF(t) × f(t,d) × (k1+1) / (f(t,d) + k1 × (1 - b + b × |d|/avgdl))
```

Where:
- `f(t,d)` = term frequency in document
- `|d|` = document length
- `avgdl` = average document length in corpus
- `k1=1.5`, `b=0.75` (tunable, BM25+ variant used)

Why BM25 for reranking (not just a second FTS pass)?
- BM25 scores chunks against the actual query at rerank time
- The RRF fusion already selected the best candidates — BM25 just orders them
- Cheaper than a neural cross-encoder (pure Python, no GPU)
- Better than raw cosine for short, keyword-rich queries

### Boosted SOP Scores

The +0.15 SOP boost is applied in SQL (not Python) for two reasons:
1. **Efficiency**: The SQL RPC can apply the boost during the pgvector scan, avoiding a second pass
2. **Correctness**: The boost is invisible to the per-source-type threshold check — raw similarity is still used for filtering, only the returned `boosted_score` has the bonus

This means a SOP chunk with raw cosine 0.40 (would pass a threshold of 0.27) gets boosted to 0.55 in the result — triggering `exact_match` classification. This is intentional: SOP procedure knowledge should be highly preferred over general context when it's relevant.

## Chunking Algorithm

### Why Token-Aware?

The OpenAI embedding API has a hard limit of 8192 tokens per input. Word count ≈ token count for English, but code, URLs, and non-ASCII content can have much higher token-to-word ratios. A 500-word chunk with a lot of code snippets might exceed 1500 tokens.

Token-aware chunking (using `tiktoken`) prevents `invalid_request_error` from the API and ensures predictable embedding quality.

### Overlap Strategy

Overlapping chunks (CHUNK_OVERLAP_TOKENS = 150) solve the "boundary problem":
- Without overlap: a query phrase split across two chunks would not be retrievable
- With overlap: both chunks containing the phrase become retrievable candidates
- 150 tokens (~110 words) is enough to capture a typical sentence or code block

### Why Not Document-Level Embeddings?

A 1000-word document embedded as a single vector averages meaning across the entire document. A query about "OTP delivery failure" would match a general KYC document at moderate similarity even if OTP is only mentioned in one paragraph.

Chunking at 1200 tokens produces more focused embeddings that match specific topics rather than document-level themes.

## SOP Parser Design

### Why Regex Over ML/NLP?

| Dimension | Regex | NLP/ML |
|-----------|-------|--------|
| Latency | Microseconds | Seconds (model load + inference) |
| Accuracy | High (structured SOPs) | Higher (unstructured text) |
| Dependencies | None | `transformers`, GPU optional |
| Determinism | Absolute | Probabilistic |
| Testability | Simple unit tests | Complex evaluation sets |
| Startup cost | Zero | Model loading (1–30s) |

SOPs are structured documents written in a consistent style. Regex patterns calibrated to that style achieve the same detection quality as NLP with zero runtime overhead.

### Frozen Dataclass (Immutability)

`SopDocumentFlags` is `@dataclass(frozen=True)`:
- Thread-safe: multiple goroutines can access the same flags object without locks
- Stateless: parsing produces a new object, never modifies existing state
- Hashable: can be used as dict keys or in sets (not needed yet, but future-proof)

## Confidence Scoring

### Why Not a Single Threshold?

Early versions used a single `similarity_threshold`. This caused two problems:
1. **False positives**: Low-similarity matches above threshold produced low-confidence answers that were still sent
2. **False negatives**: High-quality matches just below threshold were rejected

The current approach uses:
1. **Retrieval confidence**: How good is the best match?
2. **Workflow match type**: Is it an exact SOP match or just related?
3. **Branch completeness**: Did the LLM actually address all required branches?

All three feed into a composite `confidence` level. This is more robust than a single threshold.

### Automation Safety Gate Design

The gate requires ALL four conditions:
```python
automation_safe = (
    not requires_human       # No escalation flag
    AND confidence == "high"  # Strong composite confidence
    AND exact_match          # SOP-grounded, not guessing
    AND retrieval_confidence == "high"  # Multiple strong matches
)
```

Why OR-of-conditions doesn't work:
- `confidence == "high"` alone could be fooled by a single strong match without SOP coverage
- `exact_match` alone doesn't ensure the LLM addressed escalation branches
- The AND ensures multiple independent signals all agree

## Multi-Tenant Architecture

### Why Three Isolation Layers?

Defense in depth — no single layer should be the only barrier:

1. **Application layer** (`ValueError` on missing `client` parameter): Prevents misuse by developers who forget to pass the client identifier. Fast fail, no DB call.

2. **SQL layer** (`WHERE index_version = ? AND client = ?`): Even if the application layer is bypassed (direct API call with wrong client), the SQL filter ensures cross-tenant data is never returned.

3. **Supabase RLS**: Row-level policies ensure that even if the SQL RPC has a bug, the DB layer applies tenant isolation. The service-role key bypasses RLS for ingestion (required for bulk writes), but retrieval can be configured to use RLS.

### Why not just RLS?

RLS adds latency per query (policy evaluation for each row). The SQL RPC filter is applied as a WHERE clause during the pgvector scan — much faster than post-scan RLS evaluation.

## Rate Limiting Design

### In-Process Sliding Window

```python
class InProcessSlidingWindow:
    def __init__(self, limit: int, window_s: int):
        self._requests = defaultdict(deque)

    def is_allowed(self, ip: str) -> bool:
        now = time()
        window = self._requests[ip]
        cutoff = now - self._window_s
        # Remove expired entries
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        return True
```

**Complexity**: O(n) per check where n = requests in window. With limit=20 and window=60s, n ≤ 20 — effectively O(1).

**Why deque?** `popleft()` is O(1) vs O(n) for `list.pop(0)`.

### Redis Sliding Window

The Redis implementation uses a sorted set with timestamps as scores:
```
ZREMRANGEBYSCORE key 0 (now - window_s)   # Remove expired
ZCARD key                                  # Count remaining
ZADD key now now                           # Add current
EXPIRE key window_s                        # TTL for cleanup
```

All four operations run in a single Lua script (atomic, avoids race conditions).

## API Key Comparison

### The Timing Oracle Problem

Consider this naive implementation:
```python
# WRONG — timing oracle
if provided_key in valid_keys:
    return True
```

Python's `in` operator on a set short-circuits on the first hash match. If a set with 3 keys returns True in 5μs vs False in 15μs, an attacker can enumerate valid keys by measuring response times.

### Constant-Time Solution

```python
def _check(provided, valid_keys):
    result = False
    for key in valid_keys:
        result |= hmac.compare_digest(provided.encode(), key.encode())
    return result
```

- `hmac.compare_digest` is implemented in C with constant-time guarantee
- Bitwise `|=` instead of `or=` ensures all keys are always checked
- Loop always completes (no early exit)

Time is proportional to `len(valid_keys)`, not to whether a match was found.
