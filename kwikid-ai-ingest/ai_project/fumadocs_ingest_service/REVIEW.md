# Phase B1 Codebase Review

**Reviewed:** 2026-05-13  
**Reviewer:** Senior Backend/Platform Engineer (AI-assisted audit)  
**Scope:** `rag_engine/` module — all submodules reviewed

---

## Architecture Observations

### What Works Well
- Clean module boundaries: `config`, `schemas`, `document_builder`, `chunking`, `embedding`, `ingestion`, `retrieval`, `observability`, `feedback`
- Deterministic UUID5 chunk IDs make idempotent upserts possible without pre-checks
- `DatasetSchemaMapper` cleanly isolates dataset-column → schema-field translation
- Pydantic v2 models with `field_validator` are well-structured
- `TicketChunker` is stateless and thread-safe — correct design
- `DeduplicationChecker` correctly uses `{ticket_id: {chunk_index: hash}}` structure
- `DeltaTracker` avoids Freshdesk API dependency — reads only from preprocessed parquet

### Architecture Gap: Embed-then-upsert (no streaming)
The pipeline embeds ALL 5,817 chunks into memory, then upserts all at once.  
If the process crashes mid-embedding (the reported failure mode), ZERO chunks are  
written to the DB — resuming from scratch re-embeds everything.  
**Fix:** Streaming embed+upsert: upsert each batch immediately after embedding it.  
This gives free resume capability via the existing deduplication checker.

---

## Critical Issues

### C1 — `httpx.ConnectError` not retried (ROOT CAUSE of crash)
**File:** `rag_engine/embedding/openai_provider.py`  
**Lines:** 145–159

The retry loop catches `httpx.TimeoutException` and `httpx.HTTPStatusError`, but NOT:
- `httpx.ConnectError` — DNS failure (`getaddrinfo failed`)
- `httpx.NetworkError` — connection reset, remote protocol error
- `httpx.RemoteProtocolError` — server closed connection mid-request

When `getaddrinfo failed` occurs, Python raises `httpx.ConnectError`, which is a
`httpx.NetworkError`. This is NOT caught, propagates through `embed_batch()`, is NOT
an `EmbeddingError`, is NOT caught by `BatchEmbeddingProcessor.embed_chunks()`, and
crashes the entire run with `FAILED` status.

**Fix:** Add `except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError)` to the retry loop.

### C2 — No backoff jitter (thundering herd risk)
**File:** `rag_engine/embedding/openai_provider.py`, `_backoff_delay()`

Pure exponential backoff without jitter causes all concurrent clients (if ever used)
to retry at the same moment after rate-limiting, amplifying the problem.

**Fix:** Add uniform jitter: `delay += random.uniform(0, delay * 0.3)`

### C3 — `httpx.Client` uses single global timeout
**File:** `rag_engine/embedding/openai_provider.py`, line 60

`httpx.Timeout(timeout_s)` applies the same value to connect, read, and write.
DNS resolution should be shorter than read timeout (embedding API calls can be slow
with large batches). Setting connect timeout too high wastes time on dead DNS.

**Fix:** Separate `connect`, `read`, `write`, `pool` timeouts via distinct env vars.

### C4 — No circuit breaker
If the OpenAI API is down, the pipeline retries each batch independently.
With 91 batches × 6 attempts × 30s per 429 = ~4.5 hours of pointless waiting before
admitting the API is unavailable.

**Fix:** After N consecutive batch-level failures, pause for `cooldown_s` seconds
before retrying the next batch.

---

## High Priority Issues

### H1 — Embed-all-then-upsert (no resume on crash)
**File:** `rag_engine/ingestion/pipeline.py`, lines 225–241

See Architecture Gap above. On crash, all embedding work is lost.  
**Fix:** Stream embed+upsert: embed batch → upsert batch → next batch.

### H2 — `BatchEmbeddingProcessor` catches only `EmbeddingError`
**File:** `rag_engine/embedding/batch_processor.py`, lines 101–107

Only `EmbeddingError` is caught per batch. Any other exception (`ConnectError`,
`KeyError` from malformed API response, etc.) will propagate and crash the run.

**Fix:** Catch `Exception` broadly per batch; convert to logged skip with metrics.

### H3 — Pipeline embedding phase not isolated from fatal crash
**File:** `rag_engine/ingestion/pipeline.py`, line 226

```python
embed_metrics = self._batch_embedder.embed_chunks(chunks_to_embed)
```
No try/except. Any exception from `embed_chunks` crashes the entire run.

**Fix:** Wrap embedding phase in try/except; on failure, mark as PARTIAL, continue upsert of already-embedded chunks.

### H4 — `datetime.utcnow()` deprecated in Python 3.12+
**File:** `rag_engine/schemas/ingestion_record.py`, line 15

`datetime.utcnow()` is deprecated since 3.12. Use `datetime.now(timezone.utc)`.

### H5 — Missing `__pycache__` in `.gitignore` (multiple Python versions cached)
Both `.cpython-311.pyc` and `.cpython-314.pyc` files are present, indicating
two Python versions are being used. The project should commit with a single version.

---

## Medium Priority Issues

### M1 — httpx Client not closed in nominal runs
The `OpenAIEmbeddingProvider` has `close()` / context manager, but the CLI and pipeline
never call `close()`. On long runs, connections leak into OS socket exhaustion territory.

**Fix:** Use `embedding_provider` as a context manager in the pipeline or CLI.

### M2 — `log_every_n_batches` produces no early progress log
With 91 batches and `log_every=5`, first log appears at batch 5 with no indication
of start. If batch 1 hangs for 30s (timeout), there is zero console feedback.

**Fix:** Log at batch 1 always, then every N.

### M3 — Ingestion logger writes JSON as a single line to console
`_log_to_console()` calls `json.dumps(summary, indent=None)` — produces unreadable
wall of JSON. Indent for console, compact for file.

### M4 — `MetricsCollector` instantiated but never read in pipeline
`self._metrics = MetricsCollector()` is created in `IngestionPipeline.__init__()` but
never used or logged. It's dead code in the current pipeline.

**Fix:** Remove or actually wire it up.

### M5 — `IngestionRunRecord.chunks_updated` not incremented
`chunks_updated` field exists in the DB schema and `to_db_row()` but is never set
in `pipeline.py`. The `to_update` list (changed chunks) count is not tracked.

### M6 — Off-by-one in retry count description
`for attempt in range(1, self._max_retries + 2)` with comment `"+2: attempt 1 is first try"` is confusing. The loop gives `max_retries + 1` total attempts, not `max_retries` retries. Rename variable or clarify.

### M7 — No `__all__` exports in subpackage `__init__.py`
`rag_engine/embedding/__init__.py` is empty. Subpackage APIs should be declared in `__all__` or at least have a comment describing the public surface.

---

## Low Priority Improvements

### L1 — `_word_count` duplicated in chunker and chunk_schema
Both `ticket_chunker.py` and `chunk_schema.py` define `_word_count()`. Extract to a shared utility.

### L2 — `_sha256` duplicated in three files
`ticket_chunker.py`, `chunk_schema.py`, and `ticket_builder.py` all define `_sha256()`. Extract to `rag_engine/utils/hashing.py`.

### L3 — `_deterministic_chunk_id` duplicated  
Both `ticket_chunker.py` and `chunk_schema.py` implement the same UUID5 logic.
`TicketChunk.build()` already encapsulates this — `ticket_chunker.py` should use `TicketChunk.build()` instead of calling `_deterministic_id()` directly.

### L4 — `_WORD_RE` compiled regex only in chunker
Should be a module-level constant in the shared utility.

### L5 — `sample_retrieval.py` is untested
`rag_engine/cli/sample_retrieval.py` exists but is not imported by anything or tested.

---

## Security Observations

### S1 — API key potentially logged via exception message
If the `httpx.ConnectError` message happens to include URL context that embeds an API key, it would appear in logs. The current exception logging in `embed_batch()` uses `str(last_exc)` which is safe for `ConnectError` but could be risky for auth errors.

### S2 — `SUPABASE_KEY` env var name conflict
The `supabase_client.py` reads `SUPABASE_KEY`, but `.env.example` shows both `SUPABASE_KEY=your_service_role_key`. Some Supabase SDKs expect `SUPABASE_SERVICE_ROLE_KEY`. Clarify naming.

### S3 — No input validation on `ticket_ids` CLI argument
`args.tickets.split(",")` is passed directly as a list. While not a direct injection risk (it goes through Pydantic validation), very long or malformed input could cause silent issues.

### S4 — `error_message[:2000]` truncation may cut mid-exception
`IngestionErrorRecord.to_db_row()` truncates error messages to 2000 chars. This is correct, but the truncation should indicate it was truncated: `error_message[:1997] + "..."`.

---

## Naming Consistency

| Element | Current | Recommendation |
|---------|---------|----------------|
| `SUPABASE_KEY` vs `SUPABASE_SERVICE_ROLE_KEY` | Inconsistent | Standardize to `SUPABASE_SERVICE_ROLE_KEY` with alias |
| `embedding_api_key` in `app.config` | Uses `app.config` name | Add `EMBEDDING_API_KEY` alias in `rag_engine` path |
| `chunks_updated` | Set in DB row, never incremented | Fix or remove |
| `log_every_n_batches` | Only works mid-run | Rename `log_every_n` |

---

## Recommended Next Steps for Phase B1.5

1. **Immediate** — Fix C1 (ConnectError retry): single-line change, unblocks all network-sensitive environments
2. **Immediate** — Fix H1 (streaming embed+upsert): enables resume on crash
3. **Short-term** — Add circuit breaker (C4): prevents hours of pointless API hammering
4. **Short-term** — Separate timeouts (C3): shorter connect timeout catches DNS failures faster
5. **Medium-term** — Fix duplicated utilities (L1–L4): extract to `rag_engine/utils/`
6. **Medium-term** — Wire `MetricsCollector` (M4): emit p95 latency at run end
7. **Medium-term** — Implement SOP ingestion pipeline for `rag_sop_library` / `rag_sop_chunks`
8. **Long-term** — Phase B2: connect `TicketRetriever` to LLM generation layer
