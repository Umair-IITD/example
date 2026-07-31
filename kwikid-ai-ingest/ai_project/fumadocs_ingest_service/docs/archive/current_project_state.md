# Current Project State

**Date:** 2026-05-14  
**Project:** KwikID AI Ingest — Think360.ai  
**Context:** Pre-migration snapshot. Repository being moved to enterprise workstation.

---

## Phase Overview

| Phase | Name | Status | Safe to Develop? |
|-------|------|--------|-----------------|
| B1 | RAG ingestion + retrieval | IMPLEMENTED, offline-validated, live-blocked | YES — after SQL migrations |
| B1.5 | SOP ingestion + retrieval eval | PLANNED only | NO — wait for B1 live-validation |
| B2 | LLM generation layer | IMPLEMENTED, DISABLED | NO — wait for B1.5 |
| B3 | Freshdesk webhook inbound | IMPLEMENTED, DISABLED | NO — wait for B1 live-validation |

---

## Phase B1 — RAG Architecture + Vector Ingestion

**Status: STABLE (offline) / BLOCKED (live)**

### What's complete
- Full ingestion pipeline: `rag_engine/ingestion/pipeline.py`
- Token-aware chunking: `rag_engine/chunking/ticket_chunker.py` + `rag_engine/utils/tokens.py`
- Production embedding provider: `rag_engine/embedding/openai_provider.py`
- Batch processor with circuit breaker: `rag_engine/embedding/batch_processor.py`
- Ticket retriever with SOP boost: `rag_engine/retrieval/ticket_retriever.py`
- Reranking hook: `rag_engine/retrieval/reranking_hook.py` (NullReranker; hook ready)
- Deduplication + delta tracking: `rag_engine/ingestion/deduplication.py`, `delta_tracker.py`
- 6 SQL migration files: `sql/b1_migrations/B1_001` through `B1_006`
- CLI entrypoint: `rag_engine/cli/ingest_cli.py` (`--mode full | delta | gold`)
- 4 validation scripts: `validate_b1_infrastructure.py`, `validate_b1_ingestion.py`, `validate_b1_retrieval.py`, `validate_b1_tokens.py`
- DB integrity audit: `scripts/validate_b1_db_integrity.py`
- Feedback loop schema: `rag_engine/feedback/feedback_loop.py`

### What's blocking
1. SQL migrations B1_001–B1_006 NOT applied to Supabase — see `docs/manual_migration_steps.md`
2. `.env` not configured with real credentials — see `docs/local_setup_guide.md`

### What's NOT done (B1 backlog)
- Live ingestion run (blocked by above)
- Live retrieval validation (blocked by live ingestion)
- Utility extraction: `_sha256`, `_word_count`, `_deterministic_chunk_id` duplicated in 3 files → move to `rag_engine/utils/`
- `MetricsCollector` in `rag_engine/observability/metrics_collector.py` is instantiated but never used (dead code)
- `sample_retrieval.py` in `rag_engine/cli/` is not imported or tested
- SOP ingestion pipeline (B1.5 task)

---

## Phase B1.5 — Consolidation + SOP Ingestion

**Status: PLANNED. DO NOT START before B1 live-validation.**

### What's planned
- `rag_engine/ingestion/sop_pipeline.py` — SOP markdown/JSON ingestion
- CLI `--mode sop` in `ingest_cli.py`
- Section-aware chunking for SOP documents (split on `##` headings)
- Retrieval precision eval: MRR@5, NDCG@5 on labeled unity_bank queries
- Utility extraction: `rag_engine/utils/hashing.py`, `text.py`, `ids.py`
- Structured JSON logging to `LOG_DIR`

### Gates before starting B1.5
- [ ] B1 live ingestion completes without error
- [ ] `validate_b1_ingestion.py --live` passes
- [ ] `validate_b1_retrieval.py --live --client unity_bank` passes
- [ ] `validate_b1_db_integrity.py --live` passes

---

## Phase B2 — LLM Generation Layer

**Status: IMPLEMENTED. DISABLED. DO NOT USE until B1.5 complete.**

### What's implemented
- `rag_engine/generation/context_assembler.py` — retrieved chunks → LLM context
- `rag_engine/generation/prompt_builder.py` — B1_SYSTEM_PROMPT + `build_user_prompt()`
- `rag_engine/generation/llm_client.py` — `B1LLMClient` with httpx, retry, sanitized errors
- `rag_engine/generation/chat_generator.py` — `ChatGenerator`, `GenerationRequest/Result`, `B1HistoryStore`
- `POST /rag/chat` endpoint in `app/main.py` — requires `client` (tenant slug)

### Why disabled (not a feature flag — it's design)
B2 `/rag/chat` requires a working retriever to return meaningful answers. Until B1 is live-validated and retrieval precision is confirmed (B1.5-B), using B2 will produce hallucinated or low-quality answers silently. The endpoint exists but should not be exposed publicly.

### What's NOT done (B2 backlog)
- Query router (`ENABLE_QUERY_ROUTER=false` — disabled)
- Multi-turn session management beyond `B1HistoryStore`
- Hybrid retrieval integration (B1.5-B precedes this)

---

## Phase B3 — Freshdesk Webhook Inbound

**Status: IMPLEMENTED. DISABLED by default. DO NOT ENABLE until B1 live-validated.**

### What's implemented
- `app/freshdesk_webhook.py` — full webhook handler module
- `POST /freshdesk/webhook` in `app/main.py`
- Feature flag: `FRESHDESK_WEBHOOK_ENABLED=false` in `.env.example`
- HMAC-SHA256 webhook token verification
- Tenant resolution from tags, custom fields, or default
- AI draft as private note (default) or public reply

### How to enable (after B1 is live-validated)
```
FRESHDESK_WEBHOOK_ENABLED=true
FRESHDESK_WEBHOOK_SECRET=<strong random secret>
FRESHDESK_WEBHOOK_DEFAULT_CLIENT=unity_bank
FRESHDESK_DOMAIN=yourcompany.freshdesk.com
FRESHDESK_API_KEY=<freshdesk api key>
```

### Current blocker
B1 must be live-validated. Without a populated vector DB, the webhook will always return low-confidence (empty retrieval) responses.

---

## Legacy Code (Pre-B1)

Still functional but NOT actively maintained. Old architecture, superseded by B1:

| Module | Status | Used by |
|--------|--------|---------|
| `app/ingest.py`, `app/embedder.py`, `app/embeddings.py` | Legacy | `/ingest` endpoint |
| `app/vector_store.py` | Legacy | `/query` endpoint (old) |
| `app/chat.py` | Legacy | `/chat` endpoint (old) |
| `retrieval/` (root-level) | Legacy stubs | Nothing in production |
| `query_router/` (root-level) | Legacy stubs | Nothing in production |
| `observability/` (root-level) | Legacy stubs | Nothing in production |

The legacy endpoints (`/ingest`, `/query`, `/chat`) operate on `public.documents` (old table). B1 operates on `rag_ticket_documents` + `rag_ticket_chunks`. Both coexist.

---

## Current Blocker Summary

```
BLOCKER 1: Apply SQL migrations B1_001–B1_006 to Supabase
  → See: docs/manual_migration_steps.md

BLOCKER 2: Configure .env with real credentials
  → See: docs/local_setup_guide.md

These two blockers prevent ALL live validation.
Until they are resolved: development is in PLANNING mode only.
```

---

## Correct Order of Future Work

```
1. Apply SQL migrations                     ← 5 min manual step
2. Configure .env                           ← 5 min manual step  
3. Run offline validations on new machine   ← confirm environment is correct
4. Run live ingestion (--mode full)         ← ~15 min, ~$0.03
5. Run live validation scripts              ← 3 scripts × ~5 min
6. BEGIN B1.5 work                          ← SOP ingestion + retrieval eval
7. BEGIN B1.5 utility extraction            ← tech debt cleanup
8. Evaluate retrieval precision             ← MRR@5, NDCG@5 on unity_bank
9. BEGIN B2 integration testing             ← only after retrieval confirmed
10. Enable B3 webhook in staging            ← only after B2 stable
```
