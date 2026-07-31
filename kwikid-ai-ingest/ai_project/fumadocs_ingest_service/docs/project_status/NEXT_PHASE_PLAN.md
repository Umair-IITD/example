# Next Phase Plan — KwikID RAG Engine

**Date:** 2026-05-13  
**Status:** Phase B1 hardening complete. B1.5 and B2 defined below.

---

## Phase B1 Status

### Completed

| Area | Item | Status |
|------|------|--------|
| Architecture | RAG ingestion pipeline (load → build → chunk → dedup → embed → upsert) | Done |
| Architecture | Streaming embed+upsert (resume on crash via dedup) | Done |
| Embedding | `OpenAIEmbeddingProvider` with httpx connection pool | Done |
| Embedding | `ConnectError` / `NetworkError` caught and retried (root crash fix) | Done |
| Embedding | Separate timeouts: `connect`, `read`, `write`, `pool` | Done |
| Embedding | Exponential backoff with jitter | Done |
| Embedding | Circuit breaker (N consecutive failures → cooldown) | Done |
| Embedding | `close()` called in CLI `finally` block (no connection leak) | Done |
| Ingestion | Batch-level fault isolation — one bad batch does not abort run | Done |
| Ingestion | `on_batch_embedded` callback — upsert immediately after each batch | Done |
| Ingestion | Delta mode (ingest only new tickets since last run) | Done |
| Ingestion | Gold mode (ingest only Gold-tier Q→A pairs) | Done |
| Ingestion | `chunks_updated` counter now tracked | Done |
| Metrics | Extended `BatchEmbeddingMetrics` (batches_total/succeeded/failed, circuit_breaks, retry_count) | Done |
| Config | `RagEngineSettings` — separate timeout + circuit breaker fields | Done |
| Config | All B1 env vars documented in `.env.example` | Done |
| Schemas | `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` | Done |
| Schemas | Error message truncation now appends `...` to indicate truncation | Done |
| Security | API key validated on `OpenAIEmbeddingProvider.__init__` — fails fast on placeholder | Done |
| Security | Exception messages sanitized to 200 chars (`_safe_exc_str`) | Done |
| Security | Security comments added to `.env.example` for `SUPABASE_KEY` and `OPENAI_API_KEY` | Done |
| Retrieval | `TicketRetriever` — semantic search + reranking | Done |
| Retrieval | `RerankingHook` circular import fixed (TYPE_CHECKING guard) | Done |
| SQL | Migrations B1_001 through B1_006 defined | Done (not yet applied) |
| Tests | `test_b1_embedding_resilience.py` — ConnectError retry, circuit breaker, callback, resume | Done |
| Validation | `validate_b1_ingestion.py` — offline dry-run + optional live run | Done |
| Validation | `validate_b1_retrieval.py` — 10-category offline structural checks + live retrieval tests | Done |

### Remaining Before B1 is Production-Ready

| Item | Effort | Blocking? |
|------|--------|-----------|
| Apply SQL migrations B1_001–B1_006 in Supabase | 5 min | Yes — live ingest will fail without tables |
| Configure `.env` with real credentials | 5 min | Yes — `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY` |
| Run live ingestion: `python -m rag_engine.cli.ingest_cli --mode full` | ~15 min | Yes — validates end-to-end |
| Run live validation: `python scripts/validate_b1_ingestion.py --live --report` | 5 min | No (confirms DB state) |
| Run live retrieval: `python scripts/validate_b1_retrieval.py --live --client unity_bank --report` | 5 min | No (confirms retrieval works) |
| Extract duplicated `_sha256`, `_word_count`, `_deterministic_id` to `rag_engine/utils/` | 1 hr | No (tech debt — L1–L3 in REVIEW.md) |
| Wire or remove dead `MetricsCollector` | 30 min | No (M4 in REVIEW.md) |
| Add `__all__` to subpackage `__init__.py` files | 30 min | No (M7 in REVIEW.md) |

---

## Phase B1.5 — Consolidation and SOP Ingestion

**Goal:** Complete the second ingestion path (SOP documents) and harden the retrieval layer before adding LLM generation.

**Why B1.5 before B2:** The retrieval layer is the foundation of answer quality. Adding generation before validating retrieval precision creates compounding errors that are hard to debug later.

### B1.5-A: SOP Ingestion Pipeline

The `rag_sop_library` and `rag_sop_chunks` tables exist in the schema (B1_001–B1_002) but have no ingestion pipeline yet. `sop_builder.py` has the document schema; the pipeline is missing.

Tasks:
1. `rag_engine/ingestion/sop_pipeline.py` — mirror of `pipeline.py` for SOP markdown/JSON sources
2. CLI flag `--mode sop` in `ingest_cli.py`
3. SOP chunking: section-aware chunking (split on `##` headings, not word count)
4. `validate_b1_ingestion.py` — add SOP dry-run section
5. Tests: `test_b1_sop_ingestion.py`

**Input:** `data/sop/` directory (markdown files pulled from Fumadocs repo)  
**Output:** `rag_sop_library` + `rag_sop_chunks` populated

### B1.5-B: Retrieval Quality Validation

Before connecting the retrieval layer to any generation layer, confirm precision with labeled examples.

Tasks:
1. Build a small labeled set: 20–30 {query, expected_ticket_id, expected_top_1_similarity} tuples for unity_bank
2. `scripts/evaluate_retrieval_precision.py` — MRR@5, NDCG@5, hit@1, hit@3
3. Tune `B1_DEFAULT_SIMILARITY_THRESHOLD` and `B1_SOP_SIMILARITY_BOOST` based on eval results
4. Document threshold choices in `data/reports/retrieval_eval.md`

### B1.5-C: Utility Extraction (Tech Debt)

Extracted from REVIEW.md (L1–L4, M4):

1. Create `rag_engine/utils/hashing.py` — `sha256_hex(text: str) -> str`
2. Create `rag_engine/utils/text.py` — `word_count(text: str) -> int`, `WORD_RE`
3. Create `rag_engine/utils/ids.py` — `deterministic_chunk_id(ticket_id, chunk_index, index_version) -> str`
4. Update all callers: `ticket_chunker.py`, `chunk_schema.py`, `ticket_builder.py`
5. Wire `MetricsCollector` to log p95 batch latency at run end, or remove it

### B1.5-D: Observability Improvements

1. Add structured JSON logging to a file in `LOG_DIR` (currently logs only to console)
2. Log at batch 1 always (not just every N batches) — see M2 in REVIEW.md
3. Emit run summary JSON to `INGEST_REPORTS_PATH` at run end (complements existing markdown report)

---

## Phase B2 — LLM Generation Layer

**Depends on:** B1.5-A (SOP ingestion) and B1.5-B (retrieval validation) complete.

**Goal:** Connect the retrieval layer to a chat/generation layer to produce grounded answers.

### B2-A: Query Router

The `ENABLE_QUERY_ROUTER` env var already exists. Build the router:

1. `rag_engine/routing/query_router.py`
   - Input: raw user query
   - Classify: `ticket` (historical support match) vs `sop` (procedural lookup) vs `escalate`
   - Route to the appropriate retriever
   - Fall back to both retrievers and merge results by score

2. `rag_engine/routing/query_classifier.py`
   - Small classifier: keyword heuristics first, LLM fallback for ambiguous queries

### B2-B: Generation Layer

1. `rag_engine/generation/answer_generator.py`
   - Input: retrieved chunks + user query
   - Output: grounded answer string + source citations
   - Use `OPENAI_CHAT_MODEL` (already in `.env.example`)
   - Respect `CHAT_CONTEXT_CHUNK_MAX_CHARS` to bound prompt size
   - Source citations: include `ticket_id`, `chunk_index`, `similarity_score`

2. `rag_engine/generation/prompt_builder.py`
   - Separate prompt construction from generation logic
   - Support both ticket-match and SOP-lookup prompt templates

### B2-C: Chat History

`CHAT_HISTORY_TABLE=chat_messages` already exists in `.env.example`. The table schema is in `sql/chat_messages.sql`.

1. `rag_engine/chat/history_manager.py` — read/write last N turns (`CHAT_HISTORY_TURNS=6`)
2. `rag_engine/chat/session.py` — stateful conversation wrapper

### B2-D: API Layer

1. `app/routers/chat.py` — `POST /chat` endpoint
   - Input: `{query: str, session_id: str, client_id: str}`
   - Output: `{answer: str, sources: [...], confidence: float}`
2. `app/routers/ingest.py` — `POST /ingest/trigger` (webhook-triggered delta ingest)
3. Integration tests: `tests/test_b2_chat_api.py`

---

## Milestone Summary

| Milestone | Gate | ETA |
|-----------|------|-----|
| **B1 Production-Ready** | Live ingest completes without error; 6/6 online validation checks pass | Next session |
| **B1.5 Complete** | SOP ingested; retrieval MRR@5 ≥ 0.70 on labeled eval set; utility extraction done | 1–2 sessions |
| **B2 Alpha** | `/chat` endpoint returns grounded answers with source citations for unity_bank queries | 2–3 sessions |
| **B2 Production** | Multi-tenant, chat history, observability complete | 3–5 sessions |

---

## Immediate Next Commands (copy-paste ready)

```powershell
# 1. Run the embedding resilience tests
cd C:\Users\HP\Desktop\Think360\kwikid-ai-ingest\ai_project\fumadocs_ingest_service
python -m pytest tests/test_b1_embedding_resilience.py -v

# 2. After applying SQL migrations and configuring .env:
python -m rag_engine.cli.ingest_cli --mode full --dry-run

# 3. Live ingest (requires credentials):
python -m rag_engine.cli.ingest_cli --mode full

# 4. Validate:
python scripts/validate_b1_ingestion.py --live --report
python scripts/validate_b1_retrieval.py --live --client unity_bank --report
```
