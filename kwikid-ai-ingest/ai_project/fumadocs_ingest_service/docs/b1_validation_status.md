# B1 Validation Status

**Date:** 2026-05-14  
**Status:** OFFLINE VALIDATED. LIVE VALIDATION BLOCKED (pending SQL migrations + .env credentials).

---

## Validation Layers

| Layer | Script | Status | Mode |
|-------|--------|--------|------|
| Infrastructure | `scripts/validate_b1_infrastructure.py` | PASS | Offline |
| Ingestion | `scripts/validate_b1_ingestion.py` | PASS (dry-run) | Offline only |
| Retrieval | `scripts/validate_b1_retrieval.py` | PASS (10/10) | Offline only |
| Token Safety | `scripts/validate_b1_tokens.py` | PASS (19/19) | Offline (no API) |
| DB Integrity | `scripts/validate_b1_db_integrity.py` | NOT RUN | Live only |

---

## What "Offline" Means

- Offline = no Supabase connection, no OpenAI API call
- Tests structural correctness, schema validation, chunking logic, token arithmetic
- Does NOT prove that embeddings are generated correctly or stored in the DB
- Does NOT prove that retrieval returns correct results from real data

---

## What Was Fixed (Historical)

| Bug | Fix | Date |
|-----|-----|------|
| `httpx.ConnectError` not retried (ROOT CAUSE of original crash) | Added `_NETWORK_ERRORS` tuple covering ConnectError, NetworkError, RemoteProtocolError, ReadError, WriteError | 2026-05-13 |
| No backoff jitter (thundering herd) | Added `random.uniform(0, delay * 0.3)` to `_backoff_with_jitter()` | 2026-05-13 |
| Single global timeout for all operations | Split into connect/read/write/pool separate timeouts | 2026-05-13 |
| No circuit breaker (could hammer API for hours) | Added `BatchEmbeddingProcessor` circuit breaker with threshold/cooldown/max_breaks | 2026-05-13 |
| Embed-all-then-upsert (no resume on crash) | Streaming embed+upsert via `on_batch_embedded` callback | 2026-05-13 |
| Circular import in `reranking_hook.py` | `TYPE_CHECKING` guard on `RetrievedChunk` import | 2026-05-13 |
| `AutomationLabel.RESOLVABLE` doesn't exist | Fixed in test files to use `AutomationLabel.AUTO_REPLY` | 2026-05-13 |
| `embedder` undefined in `finally` block of `/rag/chat` | Added `embedder = None` before `try` block | 2026-05-13 |
| API key potentially in error log (security) | Added regex sanitization in `llm_client.py` | 2026-05-13 |
| Fallback search returned unranked table scan | Rewrote `_fallback_search()` to filter RCA-only + inject near-zero similarity + loud warning | 2026-05-13 |
| No pre-embed validation for base64 blobs | Added `_BASE64_CHARS`/`_HEX_CHARS` regex detection in `batch_processor.py` | 2026-05-13 |
| No pre-embed validation for control characters | Added `unicodedata.category(c) in {"Cc", "Cs"}` check in `batch_processor.py` | 2026-05-13 |
| Missing `recursive_split_if_oversized()` | Added to `rag_engine/utils/tokens.py` | 2026-05-13 |
| Real Supabase URL in `.env.example` | Replaced with `https://your-project-id.supabase.co` | 2026-05-13 |

---

## Ingestion Dry-Run Results (Offline)

From `data/reports/b1_ingestion_validation.md`:

| Metric | Value |
|--------|-------|
| Source rows | 3,635 |
| ESCALATION skipped | 1,304 |
| No-content skipped | 505 |
| Documents built | 1,826 |
| Chunks total | 5,817 |
| ISSUE_HEADER chunks | 1,826 |
| QUERY_BODY chunks | 2,165 |
| RESOLUTION_RCA chunks | 1,826 |
| Avg chunks/doc | 3.19 |
| QUERY_BODY avg words | 249.9 |
| RESOLUTION_RCA avg words | 12.2 (very low — most tickets lack RCA) |
| Tenants | 24 |
| Dominant tenant | unity_bank (2,726/3,635 rows) |
| Mapping errors | 0 |
| Validation errors | 0 |
| Duplicate chunk IDs | 0 |
| Throughput | 3,624 rows/s |

---

## Retrieval Sanity Results (Offline)

From `data/reports/b1_retrieval_sanity_report.md`: **10/10 checks passed.**

Checks covered:
- Schema structure validation
- `TicketRetriever` instantiation
- Filter construction (client, chunk_type, has_rca)
- Fallback search behavior (now warns loudly + filters RCA-only)
- Similarity threshold gating
- SOP boost application (+0.15)
- Confidence classification (low/medium/high)
- Reranking hook interface (NullReranker)
- Citation formatting
- Error handling (empty results, null embeddings)

---

## Token Safety Results (Offline)

`scripts/validate_b1_tokens.py`: **19/19 tests passed.**

Covers:
- `count_tokens` basic functionality
- `truncate_to_token_limit` respects limit
- `safe_split_by_tokens` no chunk exceeds limit
- `safe_split_by_tokens` empty input → []
- `safe_split_by_tokens` short text → single chunk
- `recursive_split_if_oversized` handles base64 blob (10,000-char no-whitespace)
- `recursive_split_if_oversized` normal text unchanged
- `recursive_split_if_oversized` empty → []
- Pre-embed: empty content detected
- Pre-embed: whitespace-only detected
- Pre-embed: repetitive content detected
- Pre-embed: base64 blob detected
- Pre-embed: control characters detected
- Pre-embed: good content passes
- Pre-embed: token overflow detected
- Chunker: no chunk exceeds `max_input_tokens`
- Chunker: same doc chunked twice → identical chunk IDs (idempotent)

---

## BLOCKING: What Prevents Live Validation

1. **SQL migrations not applied** — Tables `rag_ticket_documents`, `rag_ticket_chunks`, `rag_sop_library`, `rag_sop_chunks`, `rag_ingestion_logs`, `rag_feedback_logs` do not exist in Supabase yet. Apply B1_001–B1_006 in order.

2. **No `.env` with real credentials** — `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY` are not configured.

3. **`validate_b1_db_integrity.py` not run** — Can only run after migrations are applied and data is ingested.

---

## Exact Commands to Run After Migration

```powershell
# In repo root:
cd C:\Users\HP\Desktop\Think360\kwikid-ai-ingest\ai_project\fumadocs_ingest_service

# 1. Apply SQL migrations (do this in Supabase SQL editor, not here)
# See docs/manual_migration_steps.md

# 2. Configure .env
copy .env.example .env
# Edit .env: set SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY

# 3. Run offline validations first (already pass, but re-confirm on new machine)
python scripts/validate_b1_tokens.py
python scripts/validate_b1_infrastructure.py

# 4. Dry-run ingestion (no DB writes, no API calls)
python -m rag_engine.cli.ingest_cli --mode full --dry-run

# 5. Live ingestion (~15 min, costs ~$0.03 in OpenAI tokens)
python -m rag_engine.cli.ingest_cli --mode full

# 6. Validate ingestion
python scripts/validate_b1_ingestion.py --live --report

# 7. Validate retrieval
python scripts/validate_b1_retrieval.py --live --client unity_bank --report

# 8. Validate DB integrity
python scripts/validate_b1_db_integrity.py --live --report

# Only after all 8 steps pass: B1 is live-validated and B1.5 work can begin.
```

---

## Known Non-Issues (Investigated)

- **`rag_ticket_documents` empty** — NOT a code bug. Tables don't exist yet (migrations not applied).
- **`.cpython-311.pyc` and `.cpython-314.pyc` both present** — Two Python versions used on the dev machine. Standardize to Python 3.11 on the new machine.
