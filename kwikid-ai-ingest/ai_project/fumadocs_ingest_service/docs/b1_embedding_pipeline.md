# B1 Embedding Pipeline

**Module:** `rag_engine/embedding/`  
**Key files:** `openai_provider.py`, `batch_processor.py`, `base.py`  
**Date:** 2026-05-14

---

## Overview

The embedding pipeline converts text chunks into 1536-dimensional float vectors via the OpenAI Embeddings API (`text-embedding-3-small`). It is designed for production resilience: one bad batch, one network blip, or a temporary API outage must never corrupt or crash a full ingestion run.

---

## Batch Lifecycle

```
IngestionPipeline.run()
    │
    ├─ Step 1–4: Load parquet → build RagTicketDocuments → chunk → dedup
    │
    ├─ Step 5: _upsert_documents()     ← documents written to DB BEFORE embedding
    │                                     (so document_id FK is always valid)
    │
    └─ Step 6: batch_embedder.embed_chunks(chunks, on_batch_embedded=_upsert_chunks_batch)
                │
                ├─ For each TicketChunk:
                │   └─ _validate_chunk()  ← 7 checks (see below)
                │       → SKIP (metrics.chunks_invalid++) if any check fails
                │       → QUEUE if valid
                │
                ├─ Batch chunks into groups of batch_size (default 64)
                │
                └─ For each batch:
                    ├─ Circuit breaker check (abort if CB tripped max times)
                    ├─ provider.embed_batch(texts)  ← calls OpenAI API
                    │   ├─ Retry: ConnectError, NetworkError, TimeoutException, 429, 5xx
                    │   └─ No retry: 400, 401, 403 (unrecoverable)
                    ├─ Set chunk.embedding = embedding (in-place mutation)
                    ├─ on_batch_embedded(batch)  ← IMMEDIATE DB upsert
                    └─ Update metrics
```

---

## Retry Flow (`OpenAIEmbeddingProvider.embed_batch`)

```
attempt 1 of max_retries+1
  ├─ POST /v1/embeddings
  ├─ If 429: read Retry-After header → sleep → retry
  ├─ If 5xx: exponential backoff + jitter → retry
  ├─ If ConnectError/NetworkError/Timeout: exponential backoff + jitter → retry
  ├─ If 400/401/403: raise EmbeddingError immediately (NOT retried)
  └─ If success: return EmbeddingResult
  
attempt 2 ... max_retries+1: same pattern

If all attempts exhausted:
  raise EmbeddingError("... after N attempts. Last error: ...")
```

**Backoff formula:** `delay = base_delay × 2^(attempt-1) + jitter`  
**Jitter:** `uniform(0, delay × 0.3)` — ±30% prevents thundering herd  
**Max delay cap:** `retry_max_delay_s` (default 60s)  
**Default retries:** 6 (7 total attempts)

**Timeout breakdown (all configurable):**
| Timeout | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| Connect | `B1_EMBEDDING_CONNECT_TIMEOUT_S` | 10s | DNS + TCP handshake |
| Read | `B1_EMBEDDING_READ_TIMEOUT_S` | 90s | Waiting for response (long for large batches) |
| Write | `B1_EMBEDDING_WRITE_TIMEOUT_S` | 30s | Sending request body |
| Pool | `B1_EMBEDDING_POOL_TIMEOUT_S` | 10s | Waiting for a connection from pool |

---

## Circuit Breaker

Prevents hours of pointless API hammering when the embedding service is persistently unavailable.

```
State: consecutive_failures counter (resets on any success)

After every batch failure:
  consecutive_failures += 1

If consecutive_failures >= circuit_breaker_threshold (default 3):
  total_breaks += 1
  If total_breaks > circuit_breaker_max_breaks (default 5):
    LOG ERROR: "Embedding service persistently unavailable"
    BREAK — abort embedding phase (remaining chunks have no embedding)
  Else:
    LOG WARNING: "Pausing Xs before retry (trip N/M)"
    sleep(circuit_breaker_cooldown_s)  ← default 60s
    consecutive_failures = 0  ← reset after cooldown
```

**Recovery:** A single successful batch after a CB pause resets `consecutive_failures` to 0.

---

## Streaming Embed+Upsert (Resume Support)

The critical architectural decision is `on_batch_embedded` callback pattern:

```python
embed_metrics = self._batch_embedder.embed_chunks(
    chunks_to_embed,
    on_batch_embedded=self._upsert_chunks_batch,  ← called after each batch
)
```

**Why this matters:**
- Old approach: embed ALL chunks in memory → upsert all → if crash mid-embed, 0 DB writes, re-run embeds everything
- New approach (B1): embed batch → upsert batch → next batch. If crash at batch 47, re-run starts at batch 48 via dedup checker

**Dedup checker enables this:** `DeduplicationChecker` compares `content_hash` per `ticket_id`. If hash unchanged → `UNCHANGED` → skip embedding. Already-embedded chunks cost $0 on re-run.

---

## Pre-Embed Validation (`_validate_chunk`)

Seven checks in order, each returns a reason string (skip) or `None` (proceed):

| # | Check | Threshold | Reason |
|---|-------|-----------|--------|
| 1 | Empty content | any | "empty content" |
| 2 | Too short | < 10 chars | "content too short" |
| 3 | Whitespace ratio | > 90% | "whitespace ratio X%" |
| 4 | Word repetition | top word > 50% of all words | "excessive repetition: word X appears N/M times" |
| 5 | Control chars | > 10% non-printable (excl \n\r\t) | "excessive control/surrogate characters" |
| 6 | Encoded blob | > 50% of long words (≥50 chars) match base64 or hex regex | "suspected encoded blob" |
| 7 | Token overflow | > `EMBEDDING_MAX_INPUT_TOKENS` (default 7000) | "token count N exceeds max_input_tokens" |

Check 7 is a safety net — the chunker should prevent oversized chunks. If it fires, it indicates a chunking configuration bug, not a data issue.

---

## Idempotency Strategy

The full embedding + upsert flow is designed to be safely re-runnable:

| Component | Idempotency mechanism |
|-----------|----------------------|
| `rag_ticket_documents` | Upserted with `on_conflict="id"` (UUID5 deterministic) |
| `rag_ticket_chunks` | Upserted with `on_conflict="id"` (UUID5 deterministic) |
| `DeduplicationChecker` | SHA256 content hash per ticket_id → UNCHANGED if hash matches |
| `B1_INDEX_VERSION` | Changing `v1` → `v2` invalidates all chunk IDs for clean rebuild |

---

## Failure Recovery Flow

```
Scenario: Crash mid-embedding (e.g., network outage at batch 47 of 91)

1. Chunks 1–46 (batches 1–46) are already in rag_ticket_chunks with embeddings.
2. Chunks 47–91 (batches 47–91) have no embedding in DB.
3. rag_ticket_documents is fully written (Step 5 completes before Step 6).

Recovery:
  re-run: python -m rag_engine.cli.ingest_cli --mode full

  DeduplicationChecker loads existing hashes:
    → batches 1–46: hash unchanged → UNCHANGED → skip embedding (cost $0)
    → batches 47–91: not in DB or hash changed → NEW/UPDATED → re-embed

Cost of recovery: only the failed chunks are re-embedded.
```

---

## Metrics Emitted at Run End

```
Embedding complete:
  chunks_embedded:   N
  chunks_failed:     N  (each failed batch, all chunks counted as failed)
  chunks_invalid:    N  (pre-embed validation rejections)
  batches_succeeded: N
  batches_failed:    N
  api_calls:         N  (OpenAI API calls made)
  total_tokens:      N  (from usage.total_tokens in API response)
  avg_batch_latency: Xms
  throughput:        X chunks/s
  circuit_breaks:    N
```

**Cost estimate:** `total_tokens × $0.02 / 1,000,000` for `text-embedding-3-small`.  
For the full dataset: 5,817 chunks × ~250 tokens avg = ~1.45M tokens ≈ $0.029 per full run.
